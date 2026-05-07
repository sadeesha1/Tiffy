import os
from dotenv import load_dotenv

load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
DB_PATH            = os.getenv("DB_PATH", "tiffany.db")
MODEL              = os.getenv("MODEL", "claude-haiku-4-5-20251001")
QDRANT_PATH        = os.getenv("QDRANT_PATH", "./qdrant_storage")
EMBEDDING_MODEL    = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
GNEWS_API_KEY      = os.getenv("GNEWS_API_KEY", "")
OMDB_API_KEY       = os.getenv("OMDB_API_KEY", "")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")

# Telegram user ID of the only person allowed to talk to Tiff.
# Set to 0 in .env for dev mode (no restriction).
_raw_id = os.getenv("AUTHORIZED_USER_ID", "0")
AUTHORIZED_USER_ID = int(_raw_id) if _raw_id.strip().lstrip("-").isdigit() else 0

# How many conversation turns to keep in context window
MAX_HISTORY_TURNS = 12

# Max facts to inject into the dynamic context block
MAX_FACTS_IN_CONTEXT = 15

# Summarise history when message count exceeds this threshold.
# Older messages get compressed into one summary row, keeping the DB lean
# and reducing input tokens on every request.
HISTORY_SUMMARISE_THRESHOLD = 40

# Hour (Sri Lanka time, 0-23) at which the daily morning digest is sent.
DAILY_DIGEST_HOUR = int(os.getenv("DAILY_DIGEST_HOUR", "7"))
