"""
main.py
-------
Tiffany Bot — Telegram entry point.

Commands:
  /start    → Tiff wakes up and greets you
  /memory   → Shows everything Tiff currently remembers
  /clear    → Clears conversation history (keeps long-term facts)
  /reset    → Clears EVERYTHING — history and all facts (nuclear option)
  /help     → Command list
"""

import asyncio
import base64
import logging
import os
import tempfile
from telegram import Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

from config import TELEGRAM_BOT_TOKEN, AUTHORIZED_USER_ID, DAILY_DIGEST_HOUR
from memory import (
    init_db, get_all_facts, clear_history, clear_all,
    get_open_threads, close_thread,
    get_due_reminders, mark_reminder_sent,
    save_setting, get_setting,
    get_reminders_for_today,
)
from brain import chat

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

TELEGRAM_MAX = 4096

async def safe_reply(update: Update, text: str):
    """
    Send a reply, splitting on Telegram's 4096-char limit and retrying
    once on transient timeouts / network errors.
    """
    # Split into chunks if needed
    chunks = [text[i:i + TELEGRAM_MAX] for i in range(0, len(text), TELEGRAM_MAX)]
    for chunk in chunks:
        for attempt in range(2):
            try:
                await update.message.reply_text(chunk)
                break
            except (TimedOut, NetworkError) as e:
                if attempt == 0:
                    logger.warning(f"Telegram send failed ({e}), retrying in 3s…")
                    await asyncio.sleep(3)
                else:
                    logger.error(f"Telegram send failed after retry: {e}")
                    # Last-ditch: send a short fallback so the user isn't left hanging
                    try:
                        await update.message.reply_text(
                            "sorry, i had a little network hiccup 🥺 say that again?"
                        )
                    except Exception:
                        pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — logs cleanly without a full traceback wall."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)):
        logger.warning(f"Network error (non-fatal): {err}")
    else:
        logger.error(f"Unhandled exception: {err}", exc_info=err)


# ── Sinhala detection ────────────────────────────────────────────────────────

def is_sinhala(text: str) -> bool:
    """True if the text contains Sinhala Unicode characters (U+0D80–U+0DFF)."""
    return any('඀' <= c <= '෿' for c in text)


# ── Document text extraction ──────────────────────────────────────────────────

def extract_text(file_path: str) -> str:
    """Extract plain text from a PDF, DOCX, or TXT file."""
    ext = os.path.splitext(file_path)[1].lower()
    try:
        if ext == ".pdf":
            import pypdf
            reader = pypdf.PdfReader(file_path)
            parts = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(parts).strip()
        elif ext in (".docx", ".doc"):
            import docx
            doc = docx.Document(file_path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        else:
            # Treat as plain text
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except Exception as e:
        return f"[Couldn't read file: {e}]"


# ── Daily digest ──────────────────────────────────────────────────────────────

async def send_daily_digest(app: Application, chat_id: int) -> None:
    """Build and send Tiff's morning digest."""
    from tools import get_weather, get_quote, get_local_news
    from memory import get_setting

    city    = get_setting("location_city") or "Negombo"
    country = get_setting("location_country") or "Sri Lanka"

    # Gather pieces in threads so we don't block
    weather = await asyncio.to_thread(get_weather, f"{city}, {country}")
    quote   = await asyncio.to_thread(get_quote)
    news    = await asyncio.to_thread(get_local_news, "all")
    reminders = get_reminders_for_today()

    # Build the digest text
    lines = ["🌅 good morning love! here's your morning digest 🩷\n"]

    lines.append(f"☀️ **weather in {city}**\n{weather[:300]}\n")

    if reminders:
        lines.append("⏰ **things on your plate today:**")
        for r in reminders[:5]:
            time_str = r["remind_at"][11:16]  # HH:MM
            lines.append(f"  • {r['message']} at {time_str}")
        lines.append("")

    if news and "unavailable" not in news.lower():
        lines.append("📰 **local headlines:**")
        # Take first 3 news items
        news_lines = [l for l in news.split("\n") if l.startswith("[")][:3]
        for nl in news_lines:
            lines.append(f"  {nl}")
        lines.append("")

    lines.append(f"✨ **thought for today:**\n{quote}")

    digest_text = "\n".join(lines)
    try:
        await app.bot.send_message(chat_id=chat_id, text=digest_text)
        logger.info("Daily digest sent ✓")
    except Exception as e:
        logger.error(f"Failed to send daily digest: {e}")


# ── Reminder + digest background loop ────────────────────────────────────────

async def reminder_loop(app: Application) -> None:
    """
    Runs every 60 seconds.
    - Fires any reminders whose remind_at has passed
    - Sends daily digest at DAILY_DIGEST_HOUR (Sri Lanka time)
    """
    digest_sent_today: str = ""  # track date string so we only send once

    while True:
        await asyncio.sleep(60)
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            chat_id_str = get_setting("chat_id")
            if not chat_id_str:
                continue  # No messages yet — don't know where to send

            chat_id = int(chat_id_str)
            now_sl  = datetime.now(ZoneInfo("Asia/Colombo"))

            # ── Fire due reminders ────────────────────────────────────────────
            due = get_due_reminders()
            for r in due:
                try:
                    await app.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏰ hey! reminder: {r['message']} 🩷"
                    )
                    mark_reminder_sent(r["id"])
                    logger.info(f"Reminder sent: {r['message']}")
                except Exception as e:
                    logger.error(f"Failed to send reminder #{r['id']}: {e}")

            # ── Daily digest at configured hour ───────────────────────────────
            today_str = now_sl.strftime("%Y-%m-%d")
            if now_sl.hour == DAILY_DIGEST_HOUR and digest_sent_today != today_str:
                digest_sent_today = today_str
                asyncio.create_task(send_daily_digest(app, chat_id))

        except Exception as e:
            logger.error(f"Reminder loop error: {e}")


async def on_startup(app: Application) -> None:
    """Called once after the bot initialises — starts background tasks."""
    asyncio.create_task(reminder_loop(app))
    logger.info("Reminder loop started ✓")


# ── Auth helper ───────────────────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    """
    Only Sadeesha can talk to Tiff.
    If AUTHORIZED_USER_ID is 0 (dev mode), all users are allowed.
    """
    if AUTHORIZED_USER_ID == 0:
        return True
    return update.effective_user.id == AUTHORIZED_USER_ID


# ── Handlers ──────────────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main message handler — routes plain text to Tiff's brain."""
    if not is_authorized(update):
        return  # Silently ignore — don't even acknowledge unauthorized users

    user_text = update.message.text.strip()
    if not user_text:
        return

    # Persist chat_id so the reminder loop knows where to send proactive messages
    save_setting("chat_id", str(update.effective_chat.id))

    # ── Sinhala translation pipeline ─────────────────────────────────────────
    sinhala_mode = is_sinhala(user_text)
    if sinhala_mode:
        from tools import translate_to_english, translate_to_sinhala
        english_text = await asyncio.to_thread(translate_to_english, user_text)
        # Tell Claude to stay in plain English so the Sinhala translation is clean.
        # Without this, the system prompt's "mix back naturally" rule kicks in and
        # produces Singlish which Google Translate can't convert properly.
        english_text = (
            "[System note: The user wrote in Sinhala. "
            "Reply in plain English only — no Sinhala words — "
            "your response will be translated back to Sinhala automatically.]\n\n"
            + english_text
        )
    else:
        english_text = user_text

    # Show typing indicator while we wait for Claude
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    response = await chat(english_text)

    if sinhala_mode:
        response = await asyncio.to_thread(translate_to_sinhala, response)

    await safe_reply(update, response)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages — passes the image to Claude for vision analysis."""
    if not is_authorized(update):
        return

    save_setting("chat_id", str(update.effective_chat.id))

    # Download the highest-resolution version of the photo
    photo = update.message.photo[-1]
    caption = (update.message.caption or "").strip()

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        tg_file = await photo.get_file()
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        with open(tmp_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
        os.unlink(tmp_path)

        prompt = caption if caption else "i sent you a photo 👀"
        response = await chat(prompt, image_b64=image_b64, media_type="image/jpeg")
    except Exception as e:
        logger.error(f"Photo handler error: {e}")
        response = "something went wrong loading that photo 🥺 try again?"

    await safe_reply(update, response)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads — extract text and summarise or answer questions about them."""
    if not is_authorized(update):
        return

    save_setting("chat_id", str(update.effective_chat.id))

    doc      = update.message.document
    filename = doc.file_name or "document"
    caption  = (update.message.caption or "").strip()
    ext      = os.path.splitext(filename)[1].lower()

    if ext not in (".pdf", ".docx", ".doc", ".txt", ".md"):
        await update.message.reply_text(
            f"i can read PDFs, Word docs (.docx), and text files (.txt) 📄\n"
            f"that looks like a {ext or 'unknown'} file — i can't open that one 😅"
        )
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        tg_file = await doc.get_file()
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        text = await asyncio.to_thread(extract_text, tmp_path)
        os.unlink(tmp_path)

        if not text or text.startswith("[Couldn't"):
            await update.message.reply_text(
                f"hmm i couldn't read that file 🥺 is it a normal {ext} document?"
            )
            return

        # Truncate very long documents to keep tokens sane
        MAX_DOC_CHARS = 8000
        truncated = text[:MAX_DOC_CHARS]
        truncation_note = f"\n\n[document truncated to {MAX_DOC_CHARS} chars]" if len(text) > MAX_DOC_CHARS else ""

        question = caption if caption else "summarise this for me"
        user_msg = (
            f"[Document: {filename}]\n"
            f"---\n{truncated}{truncation_note}\n---\n\n"
            f"{question}"
        )

        response = await chat(user_msg)
    except Exception as e:
        logger.error(f"Document handler error: {e}")
        response = "something went weird reading that file 🥺 try again?"

    await safe_reply(update, response)


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location shares — save coordinates and reverse-geocode to city."""
    if not is_authorized(update):
        return

    save_setting("chat_id", str(update.effective_chat.id))

    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    # Reverse geocode via Nominatim (free, no key)
    city    = "your location"
    country = ""
    try:
        import httpx as _httpx
        r = _httpx.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "TiffBot/1.0"},
            timeout=8,
        )
        if r.status_code == 200:
            addr = r.json().get("address", {})
            city    = addr.get("city") or addr.get("town") or addr.get("village") or "your location"
            country = addr.get("country", "")
    except Exception as e:
        logger.warning(f"Reverse geocode failed: {e}")

    # Save to settings
    from memory import save_setting as _ss
    _ss("location_city",    city)
    _ss("location_country", country)
    _ss("location_lat",     str(lat))
    _ss("location_lon",     str(lon))

    location_str = f"{city}, {country}".strip(", ")
    prompt = (
        f"[System: Sadeesha just shared his live location. "
        f"Coordinates: {lat:.4f}, {lon:.4f}. "
        f"Reverse geocoded to: {location_str}. "
        f"Location saved for weather and local queries.] "
        f"Acknowledge naturally."
    )
    response = await chat(prompt)
    await safe_reply(update, response)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — Tiff's opening greeting."""
    if not is_authorized(update):
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    response = await chat("hey tiff, i'm here")
    await safe_reply(update, response)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/memory — Show everything Tiff currently remembers."""
    if not is_authorized(update):
        return

    facts = get_all_facts()

    if not facts:
        await update.message.reply_text(
            "nothing in my memory yet 🥺\ntell me things and i'll remember them 🩷"
        )
        return

    lines = ["things i remember 🤍\n"]
    for i, fact in enumerate(facts, 1):
        lines.append(f"{i}. {fact}")

    # Telegram has a 4096 char limit per message
    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n…(and more)"

    await update.message.reply_text(text)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/clear — Wipe conversation history. Long-term facts survive."""
    if not is_authorized(update):
        return

    clear_history()
    await update.message.reply_text(
        "conversation cleared 🤍\nfresh start — but i still remember the important stuff."
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset — Nuclear option: wipe everything including long-term memory."""
    if not is_authorized(update):
        return

    clear_all()
    await update.message.reply_text(
        "everything cleared 🥺\nclean slate — like we just met. hi 🩷"
    )


async def cmd_threads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/threads — Show all open threads Tiff is tracking."""
    if not is_authorized(update):
        return

    threads = get_open_threads()
    if not threads:
        await update.message.reply_text(
            "no open threads right now 🤍\neverything's resolved or we haven't started tracking yet."
        )
        return

    lines = ["things i'm keeping an eye on 🧵\n"]
    for t in threads:
        lines.append(f"#{t['id']} — {t['summary']}")

    lines.append("\nuse /resolve <id> to close one")
    await update.message.reply_text("\n".join(lines))


async def cmd_resolve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/resolve <id> — Close an open thread by ID."""
    if not is_authorized(update):
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("usage: /resolve <thread id>\ne.g. /resolve 3")
        return

    result = close_thread(int(args[0]))
    await update.message.reply_text(f"{result} 🤍")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — Show available commands."""
    if not is_authorized(update):
        return

    text = (
        "commands 🩷\n\n"
        "/start — wake tiff up\n"
        "/memory — see what tiff remembers\n"
        "/threads — see open threads tiff is tracking\n"
        "/resolve <id> — close a thread\n"
        "/clear — clear chat history (keeps memories)\n"
        "/reset — clear everything (nuclear)\n"
        "/help — this list"
    )
    await update.message.reply_text(text)


# ── App setup ─────────────────────────────────────────────────────────────────

def main():
    # Init database
    init_db()
    logger.info("Database ready ✓")

    # Build the bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(on_startup).build()

    # Register command handlers
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("memory",  cmd_memory))
    app.add_handler(CommandHandler("threads", cmd_threads))
    app.add_handler(CommandHandler("resolve", cmd_resolve))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("reset",   cmd_reset))
    app.add_handler(CommandHandler("help",    cmd_help))

    # Main message handler (text only, ignores commands)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Vision — photo messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # Document reader — PDF, DOCX, TXT
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Location sharing
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Global error handler — prevents "No error handlers registered" noise
    app.add_error_handler(error_handler)

    logger.info("Tiffany is awake 🩷")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    import time
    RETRY_DELAY = 10  # seconds between startup retries
    while True:
        try:
            main()
            break  # clean exit (shouldn't happen under normal operation)
        except KeyboardInterrupt:
            logger.info("Shutting down 🩷")
            break
        except Exception as e:
            logger.error(f"Startup failed: {e} — retrying in {RETRY_DELAY}s…")
            time.sleep(RETRY_DELAY)
