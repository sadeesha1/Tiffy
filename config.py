import os
from dotenv import load_dotenv

load_dotenv(override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# ── AI Backend ────────────────────────────────────────────────────────────────
# "ollama"  → Ollama cloud/local via OpenAI-compatible API (default, free)
# "claude"  → Anthropic Claude API
AI_BACKEND = os.getenv("AI_BACKEND", "ollama")

# ── Ollama settings ───────────────────────────────────────────────────────────
# Cloud:  set OLLAMA_BASE_URL=https://api.ollama.ai/v1  and  OLLAMA_API_KEY=<your key>
# Local:  leave defaults (http://localhost:11434/v1, key "ollama")
OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL",  "http://localhost:11434/v1")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL",     "qwen3-coder-next")
OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL","qwen3-coder-next")
OLLAMA_API_KEY    = os.getenv("OLLAMA_API_KEY",   "ollama")

# ── Claude settings (used when AI_BACKEND=claude) ─────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL             = os.getenv("MODEL",      "claude-sonnet-4-5")
FAST_MODEL        = os.getenv("FAST_MODEL", "claude-haiku-4-5-20251001")
THINKING_BUDGET   = int(os.getenv("THINKING_BUDGET", "8000"))

# ── Storage ───────────────────────────────────────────────────────────────────
DB_PATH         = os.getenv("DB_PATH",         "tiffany.db")
QDRANT_PATH     = os.getenv("QDRANT_PATH",     "./qdrant_storage")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# ── External API keys ─────────────────────────────────────────────────────────
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY", "")
OMDB_API_KEY  = os.getenv("OMDB_API_KEY",  "")

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is not set. Add it to your .env file.")

if AI_BACKEND == "claude" and not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY is required when AI_BACKEND=claude. Add it to your .env file.")

# Telegram user ID of the only person allowed to talk to Tiff.
# Set to 0 in .env for dev mode (no restriction).
_raw_id = os.getenv("AUTHORIZED_USER_ID", "0")
AUTHORIZED_USER_ID = int(_raw_id) if _raw_id.strip().lstrip("-").isdigit() else 0

# How many conversation turns to keep in context window
MAX_HISTORY_TURNS = 12

# Max facts to inject into the dynamic context block
MAX_FACTS_IN_CONTEXT = 15

# Summarise history when message count exceeds this threshold.
HISTORY_SUMMARISE_THRESHOLD = 40

# Hour (Sri Lanka time, 0-23) at which the daily morning digest is sent.
DAILY_DIGEST_HOUR = int(os.getenv("DAILY_DIGEST_HOUR", "7"))
