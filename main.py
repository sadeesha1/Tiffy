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
import logging
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

from config import TELEGRAM_BOT_TOKEN, AUTHORIZED_USER_ID
from memory import (
    init_db, get_all_facts, clear_history, clear_all,
    get_open_threads, close_thread,
    get_due_reminders, mark_reminder_sent,
    save_setting, get_setting,
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


# ── Reminder background loop ──────────────────────────────────────────────────

async def reminder_loop(app: Application) -> None:
    """
    Runs every 60 seconds. Fires any reminders whose remind_at has passed
    and sends them directly to the user's Telegram chat.
    """
    while True:
        await asyncio.sleep(60)
        try:
            chat_id = get_setting("chat_id")
            if not chat_id:
                continue  # No messages yet — don't know where to send
            due = get_due_reminders()
            for r in due:
                try:
                    await app.bot.send_message(
                        chat_id=int(chat_id),
                        text=f"⏰ hey! reminder: {r['message']} 🩷"
                    )
                    mark_reminder_sent(r["id"])
                    logger.info(f"Reminder sent: {r['message']}")
                except Exception as e:
                    logger.error(f"Failed to send reminder #{r['id']}: {e}")
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

    # Global error handler — prevents "No error handlers registered" noise
    app.add_error_handler(error_handler)

    logger.info("Tiffany is awake 🩷")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
