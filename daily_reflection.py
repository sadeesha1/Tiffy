"""
daily_reflection.py
-------------------
Runs once a day (via Windows Task Scheduler).

What it does:
  1. Pulls the last 24 hours of conversation from SQLite
  2. Sends it to Claude: "extract the 5 most important things to remember"
  3. Saves those as long-term facts
  4. Logs a summary so you can see what was captured

Run manually:   python daily_reflection.py
Scheduled:      run setup_scheduler.py once to register it with Task Scheduler
"""

import asyncio
import logging
from datetime import datetime

import anthropic

from config import ANTHROPIC_API_KEY, MODEL
from memory import get_history_since, remember, init_db

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """\
You are reviewing a conversation between Sadeesha and his AI companion Tiff.
Extract the 5 most important things worth remembering from this conversation.
Focus on: personal facts, emotional moments, decisions made, upcoming events, problems mentioned.
Write each as a clear, standalone fact — no numbering, one per line.
If there are fewer than 5 meaningful things, return only what's genuinely worth keeping."""


async def reflect():
    init_db()

    messages = get_history_since(hours=24)
    if not messages:
        logger.info("No messages in the last 24 hours — nothing to reflect on.")
        return

    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in messages
    )

    logger.info(f"Reflecting on {len(messages)} messages…")

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=REFLECTION_PROMPT,
        messages=[{"role": "user", "content": transcript}],
    )

    raw = response.content[0].text.strip()
    facts = [line.strip() for line in raw.splitlines() if line.strip()]

    date_tag = datetime.now().strftime("%Y-%m-%d")
    saved = 0
    for fact in facts[:5]:
        result = remember(f"[{date_tag}] {fact}")
        logger.info(result)
        saved += 1

    logger.info(f"Daily reflection complete — {saved} facts saved.")


if __name__ == "__main__":
    asyncio.run(reflect())
