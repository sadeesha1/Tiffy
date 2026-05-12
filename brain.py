"""
brain.py
--------
Tiff's brain. Handles:
  - Building the full system prompt with dynamic context
  - Calling the AI (Ollama or Claude) with tool-use support
  - Running the tool-use loop until end_turn / stop
  - Saving the final exchange to memory

Backend selection (switchable at runtime via /backend command):
  AI_BACKEND="ollama"  → Ollama cloud/local via OpenAI-compatible API (default, free)
  AI_BACKEND="claude"  → Anthropic Claude API (requires ANTHROPIC_API_KEY)
"""

import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from config import (
    ANTHROPIC_API_KEY, MODEL, FAST_MODEL, THINKING_BUDGET,
    MAX_HISTORY_TURNS, MAX_FACTS_IN_CONTEXT,
    OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_API_KEY,
    AI_BACKEND as _DEFAULT_BACKEND,
)
from memory import (
    remember, recall, save_message, get_history, get_all_facts,
    open_thread, close_thread, get_open_threads, maybe_summarise_history,
    set_reminder, remember_person, get_person, save_note, search_notes,
    log_mood, get_mood_trend, get_setting, save_setting,
    capture_learning, _extract_signals_from_text,
)
from tools import (
    get_weather, get_sl_weather_summary, get_time, search_web, search_wikipedia,
    get_news, get_movie, get_book, get_definition,
    get_holidays, get_quote, get_exchange_rate,
    get_local_news, calculate, get_youtube, multi_search,
)
from prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ── Backend management ────────────────────────────────────────────────────────

def _load_backend() -> str:
    """Load active backend from DB (runtime override) or fall back to config."""
    try:
        saved = get_setting("ai_backend")
        return saved if saved in ("claude", "ollama") else _DEFAULT_BACKEND
    except Exception:
        return _DEFAULT_BACKEND

_active_backend: str = _load_backend()


def set_backend(backend: str) -> str:
    """Switch the active AI backend at runtime. Returns the new backend name."""
    global _active_backend, _ollama_client, _claude_client
    if backend not in ("claude", "ollama"):
        return f"unknown backend '{backend}' — use 'claude' or 'ollama'"
    _active_backend = backend
    # Reset clients so they re-init with fresh env vars on next use
    _ollama_client = None
    _claude_client = None
    save_setting("ai_backend", backend)
    logger.info(f"AI backend switched to: {backend}")
    return backend


def get_backend() -> str:
    return _active_backend


# ── Clients (lazy-init) ───────────────────────────────────────────────────────

_claude_client: anthropic.AsyncAnthropic | None = None
_ollama_client = None


def _get_claude_client() -> anthropic.AsyncAnthropic:
    global _claude_client
    if _claude_client is None:
        _claude_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    return _claude_client


def _get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        from openai import AsyncOpenAI
        # Re-read from env in case .env was updated after process started
        import os
        base_url = os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        api_key  = os.getenv("OLLAMA_API_KEY",  OLLAMA_API_KEY) or "ollama"
        logger.info(f"Ollama client init → base_url={base_url}")
        _ollama_client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return _ollama_client


# ── Query complexity routing (Claude backend only) ────────────────────────────

_COMPLEX_KEYWORDS = [
    # Reasoning / analysis
    "why", "how does", "how do", "explain", "analyze", "analyse",
    "compare", "difference between", "what do you think", "your opinion",
    "should i", "help me decide", "is it worth", "better option",
    "pros and cons", "strategy", "plan", "advice", "figure out",
    "make sense of", "what would you", "what if", "break down",
    "walk me through", "help me understand", "critique", "review",
    "evaluate", "what's wrong", "why isn't", "how can i improve",
    "think about", "thoughts on", "feedback on",
    # News / factual lookups — need synthesis across tool results
    "latest news", "news about", "news on", "news around",
    "what happened", "what's happening", "whats happening",
    "any news", "any updates", "update on", "updates on",
    "tell me about", "tell me what", "what is the latest",
    "what are the latest", "info on", "information about",
    "current status", "recent news", "what's going on with",
    "latest on", "anything new about",
]

_EMOTIONAL_MARKERS = [
    "i love you", "miss you", "hurt", "sorry", "alone", "lonely",
    "us", " we ", "you and i", "you and me", "between us",
    "fail", "failed", "failure", "waste", "broken", "break",
    "shut up", "bitch", "hate", "stupid", "idiot",
    "babe", "baby", "honey", "love", "tiff", "tiffy",
    "what are you", "what you are", "what we have",
    "you're not", "you are not", "you can't", "you cant",
]


def _is_emotional(text: str) -> bool:
    tl = text.lower()
    return any(marker in tl for marker in _EMOTIONAL_MARKERS)


def _is_complex_query(text: str) -> bool:
    """True when message warrants extended thinking — analytical, not emotional."""
    if _is_emotional(text):
        return False
    tl = text.lower()
    return any(kw in tl for kw in _COMPLEX_KEYWORDS)


_CJK_RE = __import__("re").compile(
    r"[一-鿿"    # CJK Unified Ideographs
    r"㐀-䶿"     # CJK Extension A
    r"　-〿"     # CJK Symbols & Punctuation
    r"＀-￯]+"   # Fullwidth / Halfwidth Forms
)


def _clean_response(text: str) -> str:
    """
    Strip CJK characters that occasionally leak from Chinese-origin models
    (qwen3 etc.) into otherwise English responses.
    Also collapse poem-style excessive line breaks — 3+ newlines → 2,
    and 2+ newlines between short fragments → single newline.
    """
    cleaned = _CJK_RE.sub("", text)
    import re as _re
    # Collapse 3+ consecutive newlines to 2 (one blank line max)
    cleaned = _re.sub(r"\n{3,}", "\n\n", cleaned)
    # If lines are very short (≤40 chars) and separated by a single newline,
    # join them into flowing prose — this fixes the "poem" style from qwen3
    lines = cleaned.split("\n")
    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (merged and stripped
                and len(stripped) <= 60
                and not stripped.startswith(("•", "-", "*", "#", "1", "2", "3", "4", "5"))
                and merged[-1] != ""
                and len(merged[-1]) <= 60):
            merged[-1] = merged[-1].rstrip() + " " + stripped
        else:
            merged.append(line)
    cleaned = "\n".join(merged)
    # Collapse any double-spaces left behind
    cleaned = _re.sub(r"  +", " ", cleaned)
    return cleaned.strip()


def _process_user_signals(text: str):
    """
    Extract self-learning signals from a user message and save them.
    Runs in a background thread — zero cost, zero latency impact.
    """
    signals = _extract_signals_from_text(text)
    for category, content in signals:
        try:
            status = capture_learning(category, content)
            logger.debug(f"Signal [{category}] {status}: {content[:55]}")
        except Exception as e:
            logger.warning(f"Signal capture failed: {e}")


def _route(user_text: str) -> tuple[str, dict | None, int]:
    """Return (model, thinking_config, max_tokens) for Claude routing."""
    if _is_complex_query(user_text) and THINKING_BUDGET > 0:
        thinking = {"type": "enabled", "budget_tokens": THINKING_BUDGET}
        return MODEL, thinking, THINKING_BUDGET + 2048
    return FAST_MODEL, None, 768


# ── Tool definitions (Anthropic format — source of truth) ────────────────────

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
                "fact": {"type": "string", "description": "The fact or memory to save, written clearly."}
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
                "query": {"type": "string", "description": "What to search for in memory."}
            },
            "required": ["query"]
        }
    },
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
        "name": "get_sl_weather_summary",
        "description": (
            "Get weather for multiple Sri Lankan cities at once. Perfect for "
            "'weather around Sri Lanka', 'how's the weather across the island', "
            "or travel planning. Returns a city-by-city snapshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cities": {
                    "type": "string",
                    "description": (
                        "Comma-separated city names, or 'all' for the 6 major cities "
                        "(Colombo, Kandy, Galle, Jaffna, Negombo, Nuwara Eliya). Default: 'all'."
                    )
                }
            },
            "required": []
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
        "description": "Quick web search using DuckDuckGo for current events, facts, or general knowledge.",
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
        "description": "Get the latest news headlines on any topic. Defaults to Sri Lanka news if no topic given.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "News topic e.g. 'Sri Lanka economy', 'AI', 'cricket'"}
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
                "year": {"type": "integer", "description": "Year. Defaults to current year if 0 or omitted."}
            },
            "required": []
        }
    },
    {
        "name": "get_quote",
        "description": "Get a random inspirational or thoughtful quote.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_exchange_rate",
        "description": "Get the live exchange rate between two currencies. Default target is LKR (Sri Lankan Rupee).",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_currency": {"type": "string", "description": "Source currency code e.g. USD, EUR, GBP, AED"},
                "to_currency":   {"type": "string", "description": "Target currency code. Default to LKR unless user specifies otherwise."}
            },
            "required": ["from_currency", "to_currency"]
        }
    },
    {
        "name": "open_thread",
        "description": (
            "Flag something as an open/unresolved thread — something to follow up on. "
            "Use when Sadeesha mentions an upcoming event, unsolved problem, or anything to check back on."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Short description of the unresolved topic."}
            },
            "required": ["summary"]
        }
    },
    {
        "name": "close_thread",
        "description": "Mark an open thread as resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "integer", "description": "The ID of the thread to close."}
            },
            "required": ["thread_id"]
        }
    },
    {
        "name": "set_reminder",
        "description": (
            "Set a reminder for Sadeesha at a specific time. Set remind_at 20 minutes BEFORE the event. "
            "The current Sri Lanka time is in your dynamic context — use it to calculate the correct datetime."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message":   {"type": "string", "description": "What to remind about e.g. 'Doctor appointment'."},
                "remind_at": {"type": "string", "description": "ISO 8601 Sri Lanka time: YYYY-MM-DDTHH:MM:SS."}
            },
            "required": ["message", "remind_at"]
        }
    },
    {
        "name": "remember_person",
        "description": "Save or update information about a specific person Sadeesha mentions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name":    {"type": "string", "description": "The person's name."},
                "details": {"type": "string", "description": "What to remember — relationship, personality, recent events, etc."}
            },
            "required": ["name", "details"]
        }
    },
    {
        "name": "get_person",
        "description": "Look up everything Tiff knows about a specific person by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The person's name to look up."}
            },
            "required": ["name"]
        }
    },
    {
        "name": "save_note",
        "description": "Save a note for Sadeesha — an idea, to-do, list, or anything worth writing down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":   {"type": "string", "description": "Short title for the note."},
                "content": {"type": "string", "description": "Full content of the note."},
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
    {
        "name": "log_mood",
        "description": (
            "Log how Sadeesha is feeling on a 1-10 scale. Use when he expresses how he's feeling "
            "or when you naturally sense his mood. 1 = very low, 10 = amazing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "Mood score 1-10."},
                "note":  {"type": "string",  "description": "Optional short note about what's going on."}
            },
            "required": ["score"]
        }
    },
    {
        "name": "set_location",
        "description": "Save Sadeesha's current location for weather and local queries.",
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
    {
        "name": "get_local_news",
        "description": (
            "Get the latest Sri Lanka news from Ada Derana and The Island RSS feeds. "
            "Use for local Sri Lanka news, breaking news, or 'what's happening in Sri Lanka'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "News source: 'adaderana', 'island', or 'all' (default)."
                }
            },
            "required": []
        }
    },
    {
        "name": "get_youtube",
        "description": (
            "Fetch YouTube video information and/or transcript. Use this when Sadeesha "
            "shares a YouTube link and wants a summary, wants to know what a video is about, "
            "or asks questions about video content. Pass the full URL."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url":  {"type": "string", "description": "Full YouTube video URL e.g. https://youtu.be/abc123"},
                "what": {
                    "type": "string",
                    "description": "'info' for title/channel only, 'transcript' for full text, 'all' for both (default: 'all')"
                }
            },
            "required": ["url"]
        }
    },
    {
        "name": "multi_search",
        "description": (
            "Multi-engine web search that queries DuckDuckGo AND SearXNG (which aggregates "
            "Google, Bing, Brave and others) for richer results. Use this when search_web "
            "returns weak results, when Sadeesha wants thorough research, or for complex "
            "queries where multiple perspectives help."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query":   {"type": "string", "description": "Search query"},
                "engines": {
                    "type": "string",
                    "description": "'ddg' for DuckDuckGo only, 'searx' for SearXNG only, 'all' for both (default: 'all')"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "calculate",
        "description": (
            "Evaluate a mathematical expression accurately. Use for any calculations — "
            "arithmetic, percentages, currency conversions, splits, estimates, etc. "
            "Supports: +, -, *, /, **, sqrt(), log(), sin(), cos(), pi, e, round(), etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "Math expression e.g. '(450 * 1.08) / 3' or 'sqrt(144)'"}
            },
            "required": ["expression"]
        }
    },
]


def _tools_openai() -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI function-calling format for Ollama."""
    return [
        {
            "type": "function",
            "function": {
                "name":        t["name"],
                "description": t["description"],
                "parameters":  t["input_schema"],
            }
        }
        for t in TOOLS
    ]


# ── Tool executor ─────────────────────────────────────────────────────────────

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
    # Weather
    elif name == "get_weather":
        location = inputs.get("location", "")
        if not location:
            city    = get_setting("location_city") or "Negombo"
            country = get_setting("location_country") or "Sri Lanka"
            location = f"{city}, {country}"
        return get_weather(location)
    elif name == "get_sl_weather_summary":
        return get_sl_weather_summary(inputs.get("cities", "all"))
    # Time / web
    elif name == "get_time":
        return get_time(inputs.get("timezone", "Asia/Colombo"))
    elif name == "search_web":
        return search_web(inputs.get("query", ""))
    elif name == "search_wikipedia":
        return search_wikipedia(inputs.get("query", ""))
    # News
    elif name == "get_news":
        return get_news(inputs.get("topic", ""))
    elif name == "get_local_news":
        return get_local_news(inputs.get("source", "all"))
    # Info
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
    elif name == "get_youtube":
        return get_youtube(inputs.get("url", ""), inputs.get("what", "all"))
    elif name == "multi_search":
        return multi_search(inputs.get("query", ""), inputs.get("engines", "all"))
    return f"Unknown tool: {name}"


# ── Dynamic context builder ───────────────────────────────────────────────────

def build_system(auto_recall: str = "") -> list[dict]:
    """
    Build system prompt as a two-block list for Anthropic prompt caching.
    Block 1 = static personality (cached).
    Block 2 = dynamic context (not cached — changes every request).
    """
    now   = datetime.now(ZoneInfo("Asia/Colombo")).strftime("%A, %B %d %Y — %I:%M %p (Sri Lanka time)")
    facts = get_all_facts()
    facts_text = (
        "\n".join(f"- {f}" for f in facts[:MAX_FACTS_IN_CONTEXT])
        if facts else "None yet — this is the beginning of everything."
    )

    threads = get_open_threads()
    threads_text = (
        "\n".join(f"- [#{t['id']}] {t['summary']}" for t in threads[:10])
        if threads else "None."
    )

    city    = get_setting("location_city")
    country = get_setting("location_country")
    location_text = f"{city}, {country}" if city else "Negombo, Sri Lanka (default)"

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

    if (auto_recall
            and "No close matches" not in auto_recall
            and "No memories yet" not in auto_recall
            and "Couldn't search" not in auto_recall):
        dynamic_block += f"\nRelevant memories recalled for this message:\n{auto_recall}\n"

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


def build_system_flat(auto_recall: str = "") -> str:
    """
    Build the system prompt as a single flat string for OpenAI-compatible APIs.
    Ollama (and other OpenAI-compat endpoints) don't support Anthropic's
    multi-block / cache_control format — this joins them into one system message.
    """
    blocks = build_system(auto_recall=auto_recall)
    return "\n\n".join(b["text"] for b in blocks)


# ── Claude backend (Anthropic API) ───────────────────────────────────────────

async def _chat_claude(user_text: str, image_b64: str | None = None, media_type: str = "image/jpeg") -> str:
    """Process one message through the Anthropic Claude backend."""
    save_message("user", user_text)
    # Self-learning: extract preference/habit/correction signals in background
    asyncio.create_task(asyncio.to_thread(_process_user_signals, user_text))
    auto_mem = await asyncio.to_thread(recall, user_text)
    messages = get_history(limit=MAX_HISTORY_TURNS)

    # Inject image into last user message if provided
    if image_b64 and messages and messages[-1]["role"] == "user":
        messages[-1] = {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": image_b64}
                },
                {"type": "text", "text": user_text or "what do you see?"}
            ]
        }

    model, thinking, max_tokens = _route(user_text)
    logger.info(f"[Claude] model={model}, thinking={'on' if thinking else 'off'}, max_tokens={max_tokens}")

    system = build_system(auto_recall=auto_mem)
    claude  = _get_claude_client()

    loop_guard = 0
    while loop_guard < 10:
        loop_guard += 1

        api_kwargs: dict = dict(
            model=model, max_tokens=max_tokens,
            system=system, tools=TOOLS, messages=messages,
        )
        if thinking:
            api_kwargs["thinking"] = thinking

        try:
            response = await claude.messages.create(**api_kwargs)
        except anthropic.BadRequestError as e:
            if "thinking" in str(e).lower() or "extended" in str(e).lower():
                logger.warning(f"Thinking not supported on {model}, retrying without it.")
                thinking = None
                api_kwargs.pop("thinking", None)
                api_kwargs["max_tokens"] = 1024
                response = await claude.messages.create(**api_kwargs)
            else:
                raise
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error: {e}")
            return "something went wrong on my end love 🥺 try again in a sec?"

        if response.stop_reason == "end_turn":
            raw = "".join(
                b.text for b in response.content if b.type == "text" and b.text
            ).strip()
            final_text = _clean_response(raw) or "🤍"
            save_message("assistant", final_text)
            asyncio.create_task(asyncio.to_thread(maybe_summarise_history))
            return final_text

        elif response.stop_reason == "tool_use":
            assistant_content = []
            for block in response.content:
                if block.type == "thinking":
                    assistant_content.append({"type": "thinking", "thinking": block.thinking})
                elif block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})

            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await asyncio.to_thread(execute_tool, block.name, block.input)
                    logger.info(f"Tool: {block.name}({block.input}) → {str(result)[:120]}")
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
        else:
            logger.warning(f"Unexpected stop_reason: {response.stop_reason}")
            return "hmm something unexpected happened 🥺 try again?"

    logger.error("Claude tool loop exceeded guard limit")
    return "i got a bit lost in my thoughts 😅 say that again?"


# ── Ollama backend (OpenAI-compatible API) ────────────────────────────────────

async def _chat_ollama(user_text: str, image_b64: str | None = None, media_type: str = "image/jpeg") -> str:
    """
    Process one message through the Ollama backend (qwen3-coder-next or any
    OpenAI-compatible model). Uses standard function-calling format.
    """
    save_message("user", user_text)
    # Self-learning: extract preference/habit/correction signals in background
    asyncio.create_task(asyncio.to_thread(_process_user_signals, user_text))
    auto_mem = await asyncio.to_thread(recall, user_text)

    system_text = build_system_flat(auto_recall=auto_mem)
    history     = get_history(limit=MAX_HISTORY_TURNS)

    # Build messages array: system first, then history
    messages: list[dict] = [{"role": "system", "content": system_text}]

    if image_b64 and history and history[-1]["role"] == "user":
        # All history except the last message (which gets the image)
        for msg in history[:-1]:
            messages.append({"role": msg["role"], "content": msg["content"]})
        # Last user message: multimodal OpenAI format
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                },
                {"type": "text", "text": user_text or "what do you see?"},
            ]
        })
    else:
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

    ollama   = _get_ollama_client()
    tools_oa = _tools_openai()
    model    = OLLAMA_MODEL
    logger.info(f"[Ollama] model={model}")

    loop_guard = 0
    while loop_guard < 10:
        loop_guard += 1

        try:
            response = await ollama.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools_oa,
                tool_choice="auto",
                temperature=0.75,
            )
        except Exception as e:
            err_str = str(e)
            logger.error(f"Ollama API error ({type(e).__name__}): {err_str}")
            # Surface useful hints based on error type
            if "401" in err_str or "unauthorized" in err_str.lower() or "api key" in err_str.lower():
                return "ollama auth failed 🥺 check your OLLAMA_API_KEY in .env"
            if "404" in err_str or "not found" in err_str.lower():
                return f"model not found on ollama cloud 🥺 check OLLAMA_MODEL in .env (got: {model})"
            if "connect" in err_str.lower() or "network" in err_str.lower() or "timeout" in err_str.lower():
                return "can't reach ollama cloud right now 🥺 check your internet connection?"
            return f"ollama error: {err_str[:120]} 🥺"

        choice = response.choices[0]
        finish = choice.finish_reason
        has_tools = bool(choice.message.tool_calls)

        # End of turn — extract text
        if finish == "stop" or (not has_tools and finish in ("stop", None, "length")):
            text = _clean_response(choice.message.content or "") or "🤍"
            save_message("assistant", text)
            asyncio.create_task(asyncio.to_thread(maybe_summarise_history))
            return text

        # Tool calls
        elif finish == "tool_calls" or has_tools:
            tool_calls = choice.message.tool_calls or []

            # Append assistant message with tool_calls (OpenAI format)
            messages.append({
                "role":       "assistant",
                "content":    choice.message.content or "",
                "tool_calls": [
                    {
                        "id":   tc.id,
                        "type": "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            # Execute each tool and append result
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await asyncio.to_thread(execute_tool, tc.function.name, args)
                logger.info(f"Tool: {tc.function.name}({args}) → {str(result)[:120]}")
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result,
                })

        else:
            logger.warning(f"Ollama unexpected finish_reason: {finish}")
            return "hmm something unexpected happened 🥺 try again?"

    logger.error("Ollama tool loop exceeded guard limit")
    return "i got a bit lost in my thoughts 😅 say that again?"


# ── Main chat dispatcher ──────────────────────────────────────────────────────

async def chat(user_text: str, image_b64: str | None = None, media_type: str = "image/jpeg") -> str:
    """
    Route one user message to the active AI backend.

    Active backend is controlled by:
      1. Runtime switch via set_backend() / /backend Telegram command (saved to DB)
      2. AI_BACKEND env var (default fallback)
    """
    backend = _active_backend
    logger.info(f"Backend: {backend}")
    if backend == "claude":
        return await _chat_claude(user_text, image_b64, media_type)
    else:
        return await _chat_ollama(user_text, image_b64, media_type)
