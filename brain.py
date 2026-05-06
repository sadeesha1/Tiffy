"""
brain.py
--------
Tiff's brain. Handles:
  - Building the full system prompt with dynamic context
  - Calling the Claude API with tool-use support
  - Running the tool-use loop until end_turn
  - Saving the final exchange to memory
"""

import logging
from datetime import datetime

import anthropic

from config import ANTHROPIC_API_KEY, MODEL, MAX_HISTORY_TURNS, MAX_FACTS_IN_CONTEXT
from memory import remember, recall, save_message, get_history, get_all_facts
from prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Async Anthropic client
client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "remember",
        "description": (
            "Save an important fact about Sadeesha or something noteworthy "
            "to Tiff's long-term memory. Use this when you learn something "
            "worth keeping — a preference, project milestone, important date, "
            "personal detail, or anything you'd want to remember later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or memory to save, written clearly."
                }
            },
            "required": ["fact"]
        }
    },
    {
        "name": "recall",
        "description": (
            "Search Tiff's long-term memory for facts about Sadeesha or "
            "their relationship. Use this when you want to check what you "
            "already know before giving advice or following up on something."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory."
                }
            },
            "required": ["query"]
        }
    }
]


def execute_tool(name: str, inputs: dict) -> str:
    """Run a tool by name and return its result as a string."""
    if name == "remember":
        return remember(inputs.get("fact", ""))
    elif name == "recall":
        return recall(inputs.get("query", ""))
    return f"Unknown tool: {name}"


# ── Dynamic context builder ───────────────────────────────────────────────────

def build_system() -> list[dict]:
    """
    Build the system prompt as a two-block list for prompt caching.

    Block 1 — static personality (cached): SYSTEM_PROMPT never changes, so
    Anthropic caches it after the first call and charges only 10% on reruns.

    Block 2 — dynamic context (not cached): date/time + current facts change
    every request, so it must stay outside the cache boundary.
    """
    now  = datetime.now().strftime("%A, %B %d %Y — %I:%M %p")
    facts = get_all_facts()

    if facts:
        facts_text = "\n".join(f"- {f}" for f in facts[:MAX_FACTS_IN_CONTEXT])
    else:
        facts_text = "None yet — this is the beginning of everything."

    dynamic_block = f"""[DYNAMIC CONTEXT]
Today: {now}

Things Tiff remembers about Sadeesha and their relationship:
{facts_text}
[END DYNAMIC CONTEXT]"""

    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_block,
        },
    ]


# ── Main chat function ────────────────────────────────────────────────────────

async def chat(user_text: str) -> str:
    """
    Process one user message through Tiff's brain.

    Flow:
      1. Save the user message to history
      2. Build messages array from recent history (includes the new message)
      3. Call Claude with tools defined
      4. Loop: if tool_use → execute → feed result back → call again
      5. On end_turn → extract text, save to history, return to Telegram
    """

    # 1. Persist the incoming message
    save_message("user", user_text)

    # 2. Build messages from DB (newest user message is last)
    messages = get_history(limit=MAX_HISTORY_TURNS)

    # 3. Build system with injected context
    system = build_system()

    # 4. Tool-use loop
    loop_guard = 0
    while loop_guard < 4:  # remember/recall chains never exceed 2 deep; 4 is safe headroom
        loop_guard += 1

        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
                tools=TOOLS,
                messages=messages
            )
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            return "something went wrong on my end love 🥺 try again in a sec?"

        # ── End turn: extract and return text ────────────────────────────────
        if response.stop_reason == "end_turn":
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text") and block.text
            ]
            final_text = "".join(text_parts).strip()

            if not final_text:
                final_text = "🤍"  # Shouldn't happen, but fallback

            save_message("assistant", final_text)
            return final_text

        # ── Tool use: execute and loop ────────────────────────────────────────
        elif response.stop_reason == "tool_use":

            # Build assistant message content (may mix text + tool_use blocks)
            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({
                        "type": "text",
                        "text": block.text
                    })
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id":    block.id,
                        "name":  block.name,
                        "input": block.input
                    })

            messages.append({"role": "assistant", "content": assistant_content})

            # Execute every tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = execute_tool(block.name, block.input)
                    logger.info(f"Tool call: {block.name}({block.input}) → {result}")
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            # Loop back to call Claude again with the tool results

        else:
            # Unexpected stop reason
            logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
            return "hmm something unexpected happened 🥺 try again?"

    # Shouldn't reach here under normal operation
    logger.error("Tool loop exceeded guard limit")
    return "i got a bit lost in my thoughts 😅 say that again?"
