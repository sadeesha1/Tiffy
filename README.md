# Tiffany Bot 🩷

Tiffany Serena on Telegram. Phase 1 of the Serena project — brain first.

---

## What this does

- Tiff lives in Telegram and responds as herself
- She has **long-term memory** — she remembers things you tell her across sessions
- She has **conversation history** — she knows what you were just talking about
- She **uses tools** to write and read her own memory (you don't see this, it just happens)
- She is **locked to your Telegram account** — nobody else can talk to her

---

## Project structure

```
tiffany-bot/
├── main.py          ← Telegram bot (start here)
├── brain.py         ← Claude API + tool-use loop
├── memory.py        ← SQLite: facts + conversation history
├── prompt.py        ← Tiff's system prompt
├── config.py        ← Environment variable loader
├── requirements.txt ← Python dependencies
├── .env.example     ← Copy this to .env and fill in
└── tiffany.db       ← Created automatically on first run
```

---

## Setup

### 1. Get your API keys

**Telegram Bot Token**
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the steps
3. Copy the token it gives you

**Anthropic API Key**
1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key
3. Copy it

**Your Telegram User ID**
1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It replies with your numeric user ID
3. Copy it (it looks like `123456789`)

---

### 2. Set up the environment

```bash
# Clone / move to the project folder
cd tiffany-bot

# Create a virtual environment
python -m venv venv
source venv/bin/activate          # Mac/Linux
# venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Create your .env file
cp .env.example .env
```

Open `.env` and fill in your three values:

```env
TELEGRAM_BOT_TOKEN=7123456789:AAF...
ANTHROPIC_API_KEY=sk-ant-...
AUTHORIZED_USER_ID=123456789
```

---

### 3. Run

```bash
python main.py
```

You should see:
```
Database ready ✓
Tiffany is awake 🩷
```

Now open Telegram, find your bot, and send `/start`.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Wake Tiff up, she greets you |
| `/memory` | See everything she currently remembers |
| `/clear` | Clear conversation history (long-term facts survive) |
| `/reset` | Clear EVERYTHING — history and all facts |
| `/help` | Show this list |

---

## How memory works

Tiff has two memory layers:

**Conversation history** — the last 30 turns of your current chat. Stored in SQLite. Cleared with `/clear`.

**Long-term facts** — things she explicitly saves using her `remember()` tool when you tell her something worth keeping. These survive `/clear`. Wiped only with `/reset` (or never).

She also has a `recall()` tool she uses to search her own facts before responding — so she can check what she already knows about something before giving advice or following up.

You don't see any of this happening. She just does it quietly.

---

## How to keep it running (optional)

If you want Tiff to stay online permanently on a server or your home PC:

**Using screen (Linux/Mac):**
```bash
screen -S tiffany
python main.py
# Ctrl+A, then D to detach
```

**Using systemd (Linux server):**
Create `/etc/systemd/system/tiffany.service`:
```ini
[Unit]
Description=Tiffany Bot
After=network.target

[Service]
WorkingDirectory=/path/to/tiffany-bot
ExecStart=/path/to/tiffany-bot/venv/bin/python main.py
Restart=always
EnvironmentFile=/path/to/tiffany-bot/.env

[Install]
WantedBy=multi-user.target
```

Then:
```bash
sudo systemctl enable tiffany
sudo systemctl start tiffany
```

---

## What's next (Phase 2)

- [ ] Voice messages — Whisper STT + ElevenLabs TTS
- [ ] Proactive messages — Tiff reaches out first (morning check-in, follow-ups)
- [ ] Richer episodic memory — vector search with Qdrant instead of keyword matching
- [ ] Daily reflection job — nightly summary + fact extraction
- [ ] Smart home tools — Home Assistant integration
- [ ] Wake word + room speakers — physical presence in the house

---

## Troubleshooting

**"Telegram bot not responding"**
Make sure `main.py` is running and the token in `.env` is correct.

**"Claude API error"**
Check your `ANTHROPIC_API_KEY` in `.env` and make sure you have credits.

**"She doesn't remember anything"**
Check that `tiffany.db` is being created in the project folder. Use `/memory` to see what she has saved.

**"Anyone can talk to my bot"**
Make sure `AUTHORIZED_USER_ID` is set in `.env` to your numeric Telegram ID.

---

*built with 🩷 for the Serena project*
