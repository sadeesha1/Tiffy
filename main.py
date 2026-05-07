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

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction

from config import TELEGRAM_BOT_TOKEN, AUTHORIZED_USER_ID
from memory import init_db, get_all_facts, clear_history, clear_all, get_open_threads, close_thread
from brain import chat

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s  %(levelname)s  %(name)s  %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

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

    # Show typing indicator while we wait for Claude
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    response = await chat(user_text)
    await update.message.reply_text(response)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — Tiff's opening greeting."""
    if not is_authorized(update):
        return

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )
    response = await chat("hey tiff, i'm here")
    await update.message.reply_text(response)


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
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

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

    logger.info("Tiffany is awake 🩷")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
