"""
brain.py
--------
Tiff's brain. Handles:
  - Building the full system prompt with dynamic context
  - Calling the Claude API with tool-use support
  - Running the tool-use loop until end_turn
  - Saving the final exchange to memory
"""

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from config import ANTHROPIC_API_KEY, MODEL, MAX_HISTORY_TURNS, MAX_FACTS_IN_CONTEXT
from memory import remember, recall, save_message, get_history, get_all_facts, open_thread, close_thread, get_open_threads, maybe_summarise_history
from tools import (
    get_weather, get_time, search_web, search_wikipedia,
    get_news, get_movie, get_book, get_definition,
    get_holidays, get_quote, get_exchange_rate,
)
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
    },
    # ── External tools ────────────────────────────────────────────────────────
    {
        "name": "get_weather",
        "description": "Get current weather and 3-day forecast for any city or location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name or location, e.g. 'Colombo' or 'London'"}
            },
            "required": ["location"]
        }
    },
    {
        "name": "get_time",
        "description": "Get the current date and time in any timezone. Defaults to Sri Lanka time (Asia/Colombo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "Timezone string e.g. 'Asia/Colombo', 'America/New_York'. Default: Asia/Colombo"}
            },
            "required": []
        }
    },
    {
        "name": "search_web",
        "description": "Quick web search using DuckDuckGo for fast answers to current questions, facts, or general knowledge.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "search_wikipedia",
        "description": "Search Wikipedia for a detailed summary on any topic, person, place, or concept.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Topic to look up on Wikipedia"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_news",
        "description": "Get the latest news headlines on any topic. Defaults to Sri Lanka news — automatically broadens if nothing local is found.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "News topic to search for, e.g. 'Sri Lanka economy', 'AI', 'cricket'"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "get_movie",
        "description": "Get movie or TV show info including rating, cast, plot and awards from IMDb data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Movie or TV show title"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_book",
        "description": "Look up a book by title — author, publication year, page count and subjects.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Book title to search for"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_definition",
        "description": "Get the English dictionary definition, phonetics, and example sentences for a word.",
        "input_schema": {
            "type": "object",
            "properties": {
                "word": {"type": "string", "description": "The word to define"}
            },
            "required": ["word"]
        }
    },
    {
        "name": "get_holidays",
        "description": "Get Sri Lanka public holidays for a given year.",
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Year to get holidays for. Defaults to current year if 0 or omitted."}
            },
            "required": []
        }
    },
    {
        "name": "get_quote",
        "description": "Get a random inspirational or thoughtful quote — great for sharing something meaningful.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_exchange_rate",
        "description": "Get the live exchange rate between two currencies. Default target is LKR (Sri Lankan Rupee). e.g. USD to LKR.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "Source currency code e.g. USD, EUR, GBP, AED"},
                "to_currency": {"type": "string", "description": "Target currency code. Default to LKR unless user specifies otherwise."}
            },
            "required": ["from_currency", "to_currency"]
        }
    },
    {
        "name": "open_thread",
        "description": (
            "Flag something as an open/unresolved thread — something you want to "
            "follow up on later. Use this when Sadeesha mentions an upcoming event, "
            "a problem they haven't solved, or anything you'd want to check back on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A short description of the unresolved topic to track."
                }
            },
            "required": ["summary"]
        }
    },
    {
        "name": "close_thread",
        "description": (
            "Mark an open thread as resolved. Use this when you learn that something "
            "you were tracking has been resolved or is no longer relevant."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "integer",
                    "description": "The ID of the thread to close."
                }
            },
            "required": ["thread_id"]
        }
    }
]


def execute_tool(name: str, inputs: dict) -> str:
    """Run a tool by name and return its result as a string."""
    # Memory tools
    if name == "remember":
        return remember(inputs.get("fact", ""))
    elif name == "recall":
        return recall(inputs.get("query", ""))
    elif name == "open_thread":
        return open_thread(inputs.get("summary", ""))
    elif name == "close_thread":
        return close_thread(inputs.get("thread_id", 0))
    # External tools
    elif name == "get_weather":
        return get_weather(inputs.get("location", "Colombo"))
    elif name == "get_time":
        return get_time(inputs.get("timezone", "Asia/Colombo"))
    elif name == "search_web":
        return search_web(inputs.get("query", ""))
    elif name == "search_wikipedia":
        return search_wikipedia(inputs.get("query", ""))
    elif name == "get_news":
        return get_news(inputs.get("topic", ""))
    elif name == "get_movie":
        return get_movie(inputs.get("title", ""))
    elif name == "get_book":
        return get_book(inputs.get("title", ""))
    elif name == "get_definition":
        return get_definition(inputs.get("word", ""))
    elif name == "get_holidays":
        return get_holidays(inputs.get("year", 0))
    elif name == "get_quote":
        return get_quote()
    elif name == "get_exchange_rate":
        return get_exchange_rate(inputs.get("from_currency", "USD"), inputs.get("to_currency", "LKR"))
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
    now  = datetime.now(ZoneInfo("Asia/Colombo")).strftime("%A, %B %d %Y — %I:%M %p (Sri Lanka time)")
    facts = get_all_facts()

    if facts:
        facts_text = "\n".join(f"- {f}" for f in facts[:MAX_FACTS_IN_CONTEXT])
    else:
        facts_text = "None yet — this is the beginning of everything."

    threads = get_open_threads()
    if threads:
        threads_text = "\n".join(f"- [#{t['id']}] {t['summary']}" for t in threads[:10])
    else:
        threads_text = "None."

    dynamic_block = f"""[DYNAMIC CONTEXT]
Today: {now}

Things Tiff remembers about Sadeesha and their relationship:
{facts_text}

Open threads (things Tiff is following up on):
{threads_text}
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
    while loop_guard < 6:  # external tools may chain with memory tools; 6 is safe headroom
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

            # Compress history in the background if it's grown too long.
            # Runs in a thread so it doesn't delay the Telegram reply.
            asyncio.create_task(asyncio.to_thread(maybe_summarise_history))

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
            # asyncio.to_thread() runs the sync HTTP calls in a thread pool
            # so they never block the async event loop
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await asyncio.to_thread(execute_tool, block.name, block.input)
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
