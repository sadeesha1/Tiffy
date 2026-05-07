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
from memory import (
    remember, recall, save_message, get_history, get_all_facts,
    open_thread, close_thread, get_open_threads, maybe_summarise_history,
    set_reminder, remember_person, get_person, save_note, search_notes,
    log_mood, get_mood_trend, get_setting, save_setting,
)
from tools import (
    get_weather, get_time, search_web, search_wikipedia,
    get_news, get_movie, get_book, get_definition,
    get_holidays, get_quote, get_exchange_rate,
    get_local_news, calculate,
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
    },
    {
        "name": "set_reminder",
        "description": (
            "Set a reminder to notify Sadeesha at a specific time. Use this whenever "
            "she mentions an upcoming event, appointment, deadline, or asks to be "
            "reminded about something. Set remind_at to 20 minutes BEFORE the event "
            "so she gets a heads-up. The current Sri Lanka date and time is always "
            "shown in your dynamic context — use it to calculate the correct datetime."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Short description of what to remind about, e.g. 'Doctor appointment' or 'Call with Kamal'."
                },
                "remind_at": {
                    "type": "string",
                    "description": "When to send the reminder — ISO 8601 format in Sri Lanka time: YYYY-MM-DDTHH:MM:SS. Set this 20 minutes before the actual event."
                }
            },
            "required": ["message", "remind_at"]
        }
    },
    # ── Relationship memory ───────────────────────────────────────────────────
    {
        "name": "remember_person",
        "description": (
            "Save or update information about a specific person Sadeesha mentions — "
            "a friend, family member, colleague, or anyone who comes up in conversation. "
            "Use this to keep track of who matters to him and what you know about them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "The person's name."},
                "details": {"type": "string", "description": "What to remember about them — relationship, personality, recent events, etc."}
            },
            "required": ["name", "details"]
        }
    },
    {
        "name": "get_person",
        "description": "Look up everything you know about a specific person by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name to look up."}
            },
            "required": ["name"]
        }
    },
    # ── Notes ─────────────────────────────────────────────────────────────────
    {
        "name": "save_note",
        "description": (
            "Save a note for Sadeesha — an idea, a to-do, a list, anything worth writing down. "
            "Use this when he asks you to remember something specific that isn't about a person or a fact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title":   {"type": "string", "description": "Short title for the note."},
                "content": {"type": "string", "description": "The full content of the note."},
                "tags":    {"type": "string", "description": "Optional comma-separated tags e.g. 'work,ideas,lucya'"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "search_notes",
        "description": "Search through saved notes by keyword, title, or tag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Keyword or tag to search notes for."}
            },
            "required": ["query"]
        }
    },
    # ── Mood tracking ─────────────────────────────────────────────────────────
    {
        "name": "log_mood",
        "description": (
            "Log how Sadeesha is feeling on a 1-10 scale. Use this when he expresses "
            "how he's feeling, or when you naturally sense his mood from the conversation. "
            "1 = very low, 10 = amazing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "Mood score 1-10."},
                "note":  {"type": "string",  "description": "Optional short note about why or what's going on."}
            },
            "required": ["score"]
        }
    },
    # ── Location ──────────────────────────────────────────────────────────────
    {
        "name": "set_location",
        "description": (
            "Save Sadeesha's current location. Use this when he shares his location "
            "or tells you where he is. The location is used for weather and local info."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "city":    {"type": "string", "description": "City name e.g. 'Negombo'."},
                "country": {"type": "string", "description": "Country e.g. 'Sri Lanka'."},
                "lat":     {"type": "number", "description": "Latitude (optional)."},
                "lon":     {"type": "number", "description": "Longitude (optional)."}
            },
            "required": ["city"]
        }
    },
    # ── Local news ────────────────────────────────────────────────────────────
    {
        "name": "get_local_news",
        "description": (
            "Get the latest Sri Lanka news from Ada Derana and Daily Mirror RSS feeds. "
            "Use this for local Sri Lanka news, breaking news, or when he asks what's happening in Sri Lanka."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "News source: 'adaderana', 'dailymirror', or 'all' (default). Use 'all' unless he asks for a specific source."
                }
            },
            "required": []
        }
    },
    # ── Calculator ────────────────────────────────────────────────────────────
    {
        "name": "calculate",
        "description": (
            "Evaluate a mathematical expression accurately. Use this for any calculations — "
            "arithmetic, percentages, currency conversions, split bills, mortgage estimates, etc. "
            "Supports: +, -, *, /, **, sqrt(), log(), sin(), cos(), pi, e, round(), etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Mathematical expression to evaluate e.g. '(450 * 1.08) / 3' or 'sqrt(144)'"}
            },
            "required": ["expression"]
        }
    },
]


def execute_tool(name: str, inputs: dict) -> str:
    """Run a tool by name and return its result as a string."""
    # ── Memory tools ──────────────────────────────────────────────────────────
    if name == "remember":
        return remember(inputs.get("fact", ""))
    elif name == "recall":
        return recall(inputs.get("query", ""))
    elif name == "open_thread":
        return open_thread(inputs.get("summary", ""))
    elif name == "close_thread":
        return close_thread(inputs.get("thread_id", 0))
    elif name == "set_reminder":
        return set_reminder(inputs.get("message", ""), inputs.get("remind_at", ""))
    elif name == "remember_person":
        return remember_person(inputs.get("name", ""), inputs.get("details", ""))
    elif name == "get_person":
        return get_person(inputs.get("name", ""))
    elif name == "save_note":
        return save_note(inputs.get("title", ""), inputs.get("content", ""), inputs.get("tags", ""))
    elif name == "search_notes":
        return search_notes(inputs.get("query", ""))
    elif name == "log_mood":
        return log_mood(inputs.get("score", 5), inputs.get("note", ""))
    elif name == "set_location":
        city    = inputs.get("city", "Negombo")
        country = inputs.get("country", "Sri Lanka")
        lat     = inputs.get("lat")
        lon     = inputs.get("lon")
        save_setting("location_city",    city)
        save_setting("location_country", country)
        if lat is not None:
            save_setting("location_lat", str(lat))
        if lon is not None:
            save_setting("location_lon", str(lon))
        return f"Location saved: {city}, {country}"
    # ── External tools ────────────────────────────────────────────────────────
    elif name == "get_weather":
        # Use saved location if no explicit location given
        location = inputs.get("location", "")
        if not location:
            city    = get_setting("location_city") or "Negombo"
            country = get_setting("location_country") or "Sri Lanka"
            location = f"{city}, {country}"
        return get_weather(location)
    elif name == "get_time":
        return get_time(inputs.get("timezone", "Asia/Colombo"))
    elif name == "search_web":
        return search_web(inputs.get("query", ""))
    elif name == "search_wikipedia":
        return search_wikipedia(inputs.get("query", ""))
    elif name == "get_news":
        return get_news(inputs.get("topic", ""))
    elif name == "get_local_news":
        return get_local_news(inputs.get("source", "all"))
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
    elif name == "calculate":
        return calculate(inputs.get("expression", ""))
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

    # Location context
    city    = get_setting("location_city")
    country = get_setting("location_country")
    location_text = f"{city}, {country}" if city else "Negombo, Sri Lanka (default)"

    # Mood trend
    mood_text = get_mood_trend(days=7)

    dynamic_block = f"""[DYNAMIC CONTEXT]
Today: {now}
Sadeesha's location: {location_text}

Things Tiff remembers about Sadeesha and their relationship:
{facts_text}

Open threads (things Tiff is following up on):
{threads_text}
"""
    if mood_text:
        dynamic_block += f"\n{mood_text}\n"

    dynamic_block += "[END DYNAMIC CONTEXT]"

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

async def chat(user_text: str, image_b64: str | None = None, media_type: str = "image/jpeg") -> str:
    """
    Process one user message through Tiff's brain.

    Args:
        user_text:  The text message from Sadeesha.
        image_b64:  Optional base64-encoded image for vision requests.
        media_type: MIME type of the image (default: image/jpeg).

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

    # If there's an image, replace the last user message content with a multimodal block
    if image_b64:
        last_msg = messages[-1] if messages else None
        if last_msg and last_msg["role"] == "user":
            messages[-1] = {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": media_type,
                            "data":       image_b64,
                        }
                    },
                    {
                        "type": "text",
                        "text": user_text or "what do you see?"
                    }
                ]
            }

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
