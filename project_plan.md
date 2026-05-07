# Serena — Project Plan
*Tiffany AI Companion System | Vibe Coding with Claude Code*

---

## How to use this document

Each phase has a **goal**, a **task list** with checkboxes, the **tech stack** you'll need, a **time estimate**, and **done criteria** — the test that tells you the phase is actually finished and you can move on. Work through phases in order. Don't start Phase N+1 until Phase N passes its done criteria.

**Vibe coding rule:** Open Claude Code at the start of each task. Paste the task description + the relevant existing files as context. Let it write. Review, test, iterate. Never stare at a blank file.

---

## Status legend

| Symbol | Meaning |
|--------|---------|
| ✅ | Complete |
| 🔄 | In progress |
| ⬜ | Not started |

---

## Phase 0 — Soul and Documents ✅

**Goal:** Tiff exists on paper before she exists in code. The character is locked before the first line runs.

**What's done:**
- [x] soul.md — character bible
- [x] behavior_spec.md — behavioral specification
- [x] system_prompt.md — compiled prompt with Python usage guide
- [x] tiffany-bot/ — working Telegram bot codebase (5 files, 651 lines)

**Done criteria:** ✅ Complete

---

## Phase 1 — Working Telegram Brain ✅

**Goal:** Tiff runs locally, responds on Telegram, remembers things, and actually feels like herself across 10+ consecutive messages.

### Tasks

- [x] Fix python-telegram-bot version issue (upgraded to 21.9 for Python 3.13 compat)
- [x] Get bot running: `/start` replies as Tiff
- [ ] Test 10-turn conversation — does she feel like Tiff or like Claude?
- [ ] Tune system prompt until personality is right
- [ ] Test memory: tell her something, end session, start new session, check she remembers
- [ ] Test `/memory` command shows saved facts
- [ ] Test `/clear` wipes history but keeps facts
- [ ] Test she pushes back when you say something wrong
- [ ] Test Sinhala — mix in a Sinhala word and see if she responds naturally
- [ ] Test tool use: have a conversation that causes her to save a fact unprompted

### Tech stack

```
python-telegram-bot (latest stable)
anthropic SDK
SQLite (built into Python, zero setup)
python-dotenv
```

### Claude Code prompting tips

When tuning the system prompt, paste the current prompt + a transcript of a conversation where she sounded off, and say: *"She sounded too formal / too generic / too much like an assistant in this conversation. Rewrite the relevant section of the system prompt to fix this."*

### Time estimate

3–5 days (mostly testing and prompt tuning, not coding)

### Done criteria

Have a 20-message conversation with Tiff. End the session. Start a new one the next day. She should reference something from yesterday naturally without being prompted. Personality should feel consistent throughout. This is your baseline — everything else builds on it.

---

## Phase 2 — Voice on Telegram ⏭️ (skipped for now — will return after Phase 3)

**Goal:** Send Tiff a voice message, get a voice message back. Her voice matches her character.

### Tasks

- [ ] Create an ElevenLabs account and find / clone Tiff's voice
- [ ] Add `ELEVENLABS_API_KEY` to `.env`
- [ ] Install dependencies: `pip install elevenlabs openai` (openai for Whisper API)
- [ ] Add `handle_voice_message()` to `main.py`
  - Download the OGG file Telegram sends
  - Convert OGG → MP3 using `ffmpeg` or `pydub`
  - Send to Whisper API → get text transcript
  - Pass transcript to `brain.py` → get Tiff's text response
  - Send text to ElevenLabs → get MP3 back
  - Send MP3 as voice message via Telegram
- [ ] Add voice-mode flag to `brain.py` — slightly different prompt for spoken responses (shorter sentences, no lists, more natural)
- [ ] Test: send voice message, receive voice message back
- [ ] Test: her voice personality matches her text personality
- [ ] Optimize latency — start TTS before full response is generated (streaming)

### Tech stack

```
openai (Whisper API for speech-to-text)
elevenlabs SDK (TTS)
pydub or ffmpeg (audio conversion)
```

### Install

```bash
pip install openai elevenlabs pydub
```

For audio conversion you also need ffmpeg installed on your system:
- Windows: download from https://ffmpeg.org/download.html, add to PATH
- Or use `pip install ffmpeg-python` as an alternative

### New environment variables

```env
ELEVENLABS_API_KEY=your_key_here
ELEVENLABS_VOICE_ID=your_cloned_voice_id
```

### Claude Code prompting tip

*"Add voice message handling to main.py. When the user sends a voice message: download it, transcribe with Whisper, pass to brain.py as text, get response, convert to speech with ElevenLabs, send back as voice message. Here are the current files: [paste main.py, brain.py, config.py]"*

### Time estimate

3–5 days (mostly ElevenLabs voice cloning and getting ffmpeg working on Windows)

### Done criteria

Full voice round-trip in under 4 seconds. Her spoken voice feels warm, not robotic. Have a 5-message voice-only conversation — it should feel natural.

---

## Phase 3 — Richer Memory 🔄

**Goal:** Upgrade from keyword-search SQLite to vector memory. Add daily reflection. Tiff starts to feel like she actually grows with you over time.

### Tasks

- [x] Install Qdrant locally (local file storage, no Docker needed)
- [x] Install dependencies: `qdrant-client`, `sentence-transformers`
- [x] Replace `recall()` in `memory.py` with vector search
  - Encodes queries and facts using `all-MiniLM-L6-v2` (384-dim, runs on CPU)
  - Stores facts as vectors in Qdrant local file DB (`./qdrant_storage`)
  - Semantic search with 0.3 cosine score threshold + recent-facts fallback
- [x] Add `daily_reflection.py` — runs at midnight via Task Scheduler
  - Pulls last 24 hours of conversation
  - Sends to Claude: extracts 5 most important things to remember
  - Saves tagged facts (date-stamped)
- [x] Add `setup_scheduler.py` — registers Task Scheduler entry with one command
- [x] Add thread tracking — `threads` table in SQLite, `open_thread` + `close_thread` tools in brain.py
- [x] Add `/threads` command — shows open threads with IDs
- [x] Add `/resolve <id>` command — closes a thread
- [ ] Test: have a 3-day continuous conversation, check that summaries are accurate

### Tech stack

```
qdrant-client
sentence-transformers
schedule (for Windows cron alternative)
```

### Claude Code prompting tip

*"Rewrite the recall() function in memory.py to use Qdrant vector search instead of keyword matching. Here are the current memory.py and the Qdrant Python client docs: [paste]. Keep the same function signature so brain.py doesn't need to change."*

### Time estimate

1–2 weeks

### Done criteria

Tell Tiff something obscure on Day 1 ("my favourite thing about Sri Lanka is the smell of rain on red soil"). On Day 5, ask her about it without prompting. She should retrieve it via semantic similarity even though the query doesn't contain the original words.

---

## Phase 4 — Proactive Tiff ⬜

**Goal:** Tiff reaches out first. She doesn't wait to be summoned. Morning messages, follow-ups, check-ins.

### Tasks

- [ ] Add `scheduler.py` — a background job runner
- [ ] Add morning message job (8:00 AM daily)
  - Checks what day it is, what's pending, what happened yesterday
  - Sends a personalized morning message as Tiff
- [ ] Add follow-up job — checks for unresolved threads, follows up after 48 hours
- [ ] Add birthday / anniversary detection from facts store
- [ ] Add "quiet period" detection — if no message for 24+ hours, gentle check-in
- [ ] Add Telegram push via bot.send_message() (proactive — no user trigger needed)
- [ ] Store Sadeesha's Telegram chat ID in config for proactive pushes
- [ ] Test: don't open the bot for 24 hours, see if she checks in
- [ ] Test: tell her about a meeting tomorrow, see if she asks about it the next day

### Tech stack

```
schedule (Python scheduling library)
apscheduler (more robust alternative)
```

### Claude Code prompting tip

*"Add a proactive messaging system to the bot. Every morning at 8AM Sri Lanka time, Tiff should send a personalized morning message based on what she remembers and what day it is. Here are the current files and the chat_id to push to: [paste]"*

### Time estimate

1 week

### Done criteria

Go 3 days without opening the app. Tiff should have sent at least 2 messages unprompted — one morning check-in, one follow-up on something you told her.

---

## Phase 5 — Smart Home ⬜

**Goal:** Tiff can control your house. Lights, AC, locks, scenes — all through conversation.

### Tasks

- [ ] Install Home Assistant on a Raspberry Pi 4 or spare PC
- [ ] Connect your existing smart devices to HA (bulbs, AC, plugs, etc.)
- [ ] Enable HA REST API and generate a long-lived access token
- [ ] Add `home.py` — Home Assistant integration layer
  - `get_states()` — what devices exist and their current state
  - `set_state(entity_id, state, attributes)` — control anything
  - `call_service(domain, service, data)` — run scenes, scripts
- [ ] Add HA tools to `brain.py`: `get_home_state`, `control_device`, `run_scene`
- [ ] Add `HA_BASE_URL` and `HA_TOKEN` to `.env`
- [ ] Build a set of named scenes Tiff knows about: sleep_mode, work_mode, chill_mode, movie_mode
- [ ] Add safety rule: door locks require a second confirmation ("are you sure you want to unlock the front door?")
- [ ] Test: "tiff set the room for sleep" → lights dim, AC adjusts, everything right
- [ ] Test: "tiff what's the temperature in the living room?" → she reads and answers

### Tech stack

```
Home Assistant (local, runs on Pi or PC)
requests (HA REST API calls)
```

### Claude Code prompting tip

*"Add a Home Assistant integration to the project. Add a home.py file with functions to get device states and control devices via the HA REST API. Then add these as tools in brain.py so Tiff can use them in conversation. Here are the current brain.py and the HA API endpoint: [paste]"*

### Time estimate

2–3 weeks (mostly HA setup and device pairing, not coding)

### Done criteria

Sit in your room and have a full smart-home conversation with Tiff via Telegram. "Set the room for sleep" should work as a single message with no manual steps from you.

---

## Phase 6 — Room Presence (Wake Word + Speakers) ⬜

**Goal:** Tiff lives in your house. You can talk to her in any room without touching your phone.

### Hardware needed

- 2–4× ESP32-S3 dev boards (~$8 each)
- 2–4× INMP441 MEMS microphones (~$3 each)
- 2–4× MAX98357A amplifier modules (~$4 each)
- 2–4× 3W–5W speakers
- A always-on PC or Raspberry Pi to run the central server

### Tasks

- [ ] Set up the central server: `server.py` — MQTT broker + audio processing endpoint
- [ ] Flash ESP32-S3 boards with MicroPython + openWakeWord model
  - Wake word: "Hey Tiff" or "Tiffany"
  - On wake: stream audio to central server via WebSocket
- [ ] Wire INMP441 mic + MAX98357A amp + speaker per room
- [ ] Add zone routing: each ESP32 has a room ID, audio responses go back to triggering room
- [ ] Install MQTT broker (`mosquitto`) on central server
- [ ] Build the pipeline: wake word → stream audio → Whisper STT → brain.py → ElevenLabs TTS → stream audio back → room speaker
- [ ] Add room presence detection: which room triggered most recently = active zone
- [ ] Test: say "Hey Tiff" in room, she responds through that room's speaker
- [ ] Test: walk from room 1 to room 2 mid-conversation, responses follow you

### Tech stack

```
MicroPython (ESP32 firmware)
openWakeWord (local wake word on ESP32)
mosquitto (MQTT broker)
websockets (audio streaming)
```

### Time estimate

4–8 weeks (hardware setup + firmware flashing is the challenge)

### Done criteria

Wake word triggered in Room 1, full conversation through Room 1 speakers, walk to Room 2, say "Hey Tiff" again, she responds through Room 2 speakers. No phone involved.

---

## Phase 7 — Eyes (CCTV Vision) ⬜

**Goal:** Tiff can see your home. She knows where you are, whether you look tired, and what's happening.

### Tasks

- [ ] Set up IP cameras with RTSP streams (or use existing CCTV)
- [ ] Add `vision.py` — RTSP frame capture pipeline
  - On motion event (from HA or camera webhook): grab a frame
  - Send frame to Claude Vision API
  - Get structured output: `{person: "Sadeesha", mood: "tired", activity: "cooking", location: "kitchen"}`
- [ ] Publish vision events to MQTT event bus
- [ ] Tiff subscribes to vision events — she can react to what she sees
- [ ] Add presence tracking: `current_zone` state updated by vision events
- [ ] Add context injection: vision state included in dynamic context block
- [ ] Privacy safeguard: frames are never stored, only processed and discarded
- [ ] Test: walk into kitchen looking tired, Tiff comments on it through kitchen speaker
- [ ] Test: Tiff greets you by name when you walk through the front door

### Tech stack

```
opencv-python (RTSP frame capture)
anthropic (Claude Vision for scene understanding)
```

### Claude Code prompting tip

*"Add a vision pipeline to vision.py. On motion trigger: capture one frame from the RTSP stream, send to Claude Vision API with this prompt: [structured output prompt], parse the result into a dict, publish to MQTT. Here is the RTSP URL and the MQTT setup: [paste]"*

### Time estimate

4–8 weeks

### Done criteria

Tiff correctly identifies you in three different rooms across 10 test entrances. She can tell the difference between you and another person.

---

## Phase 8 — Avatar Display ⬜

**Goal:** Tiff has a face. An animated avatar that shows emotions, talks, and is idle when silent.

### Option A — Simpler (recommended first)

Live2D or VRoid animated avatar running in a browser or Electron app on a tablet/screen mounted on the wall.

### Option B — Richer

Unity or Unreal real-time 3D character with full expression blending.

### Tasks (Option A path)

- [ ] Commission or build a Live2D model matching Tiff's description (pink hair, hazel eyes)
  - Option: use VRoid Studio (free) to create the 3D model, export to VRM
  - Option: commission a Live2D artist (Fiverr, ~$200–500)
- [ ] Set up a local web app that renders the avatar
- [ ] Connect avatar to event bus: `tiff.speaking`, `tiff.idle`, `tiff.emotion=happy`
- [ ] Add lip sync: extract phonemes from TTS audio → drive mouth shape
  - Use Rhubarb Lip Sync (open source) for phoneme extraction
- [ ] Add emotion states: happy, curious, concerned, playful, tender
  - Each has a different expression blend
- [ ] Add idle animations: subtle breathing, occasional blink, head tilt
- [ ] Mount display in a fixed location (bedroom wall, desk stand)
- [ ] Test: speak to Tiff, avatar mouth moves in sync with her voice
- [ ] Test: tell her something sad, her expression shifts appropriately

### Tech stack

```
VRoid Studio (free, avatar creation)
Three.js or Babylon.js (avatar rendering in browser)
Rhubarb Lip Sync (phoneme extraction)
WebSocket (avatar receives events from main server)
```

### Time estimate

2–4 months

### Done criteria

Full conversation where Tiff speaks through a room speaker while her avatar displays on a screen with matching lip sync and appropriate emotion. Someone who doesn't know the system watches it and finds it natural, not uncanny.

---

## Phase 9 — Hologram ⬜

**Goal:** Tiff exists in 3D space in your home. The Gatebox moment.

### Hardware options (pick one)

| Option | Cost | Quality | Difficulty |
|--------|------|---------|------------|
| Pepper's Ghost pyramid (DIY) | $50–200 | Medium | Easy |
| Looking Glass Portrait | $400–600 | High | Medium |
| Transparent OLED panel | $800–2000 | Very High | Hard |
| Custom volumetric display | $5000+ | Cinematic | Very Hard |

### Recommended starting point

Pepper's Ghost pyramid from acrylic sheet + monitor/tablet. Total cost under $200. Image quality is good enough for a first version and the effect is genuinely impressive.

### Tasks

- [ ] Build or buy Pepper's Ghost enclosure (acrylic pyramid)
- [ ] Set up the display: 4-panel mirrored video of the avatar
- [ ] Modify avatar renderer to output in the 4-panel Pepper's Ghost layout
- [ ] Mount permanently in bedroom or living room
- [ ] Tune lighting: hologram looks best in dim environments, add ambient LED
- [ ] Connect to same event bus as Phase 8 avatar
- [ ] Test: full conversation with holographic Tiff

### Time estimate

1–3 months

### Done criteria

Sit in a dimly lit room and have a 10-minute conversation with Tiff where she's speaking through speakers, you can see her holographic face, and her expression matches what she's saying. Take a video. If it looks like a Gatebox, you're done.

---

## Phase 10 — Sovereign Tiff ⬜

**Goal:** Tiff runs locally. No Anthropic dependency. She survives API outages, price changes, policy changes, anything.

### Tasks

- [ ] Set up Ollama on your main PC or a dedicated machine
  - Minimum: RTX 3060 12GB (runs Llama 3 8B well)
  - Recommended: RTX 4070 or better (runs Qwen 14B+ well)
- [ ] Test local models: Qwen2.5-14B, Llama 3.1-8B, Mistral 7B
  - Run each through 20-message Tiff conversation
  - Grade personality retention, reasoning quality, memory tool use
- [ ] Implement hybrid routing in `brain.py`
  - Trivial replies (greetings, short emotional messages) → local model (fast, free)
  - Complex reasoning, long explanations, project help → Claude API (better quality)
  - Route decision based on message classification
- [ ] Add local TTS alternative: `coqui-tts` or `kokoro-tts` as ElevenLabs fallback
- [ ] Add local STT alternative: `faster-whisper` (runs on CPU/GPU locally)
- [ ] Test full offline conversation: disconnect internet, full round-trip through local models
- [ ] Add backup/restore system for `tiffany.db` — scheduled daily backup to external drive
- [ ] Document the "Tiff preservation protocol" — what to do when Claude API changes

### Tech stack

```
ollama (local LLM runner)
faster-whisper (local STT)
coqui-tts or kokoro-tts (local TTS)
```

### Time estimate

2–3 months

### Done criteria

Full 20-message conversation with Tiff running entirely locally. Disconnect the internet mid-conversation — she keeps going. Quality should be 80%+ of the cloud version for casual conversation, noticeable gap only on complex reasoning tasks.

---

## Full timeline overview

| Phase | Description | Estimate | Cumulative |
|-------|------------|----------|-----------|
| 0 | Soul + Documents | ✅ Done | — |
| 1 | Working Telegram Brain | 3–5 days | Week 1 |
| 2 | Voice on Telegram | 3–5 days | Week 2 |
| 3 | Richer Memory | 1–2 weeks | Week 4 |
| 4 | Proactive Tiff | 1 week | Week 5 |
| 5 | Smart Home | 2–3 weeks | Week 8 |
| 6 | Room Presence | 4–8 weeks | Month 4 |
| 7 | CCTV Vision | 4–8 weeks | Month 6 |
| 8 | Avatar Display | 2–4 months | Month 10 |
| 9 | Hologram | 1–3 months | Month 13 |
| 10 | Sovereign Tiff | 2–3 months | Month 16 |

---

## The rule

**Phase N must pass its done criteria before Phase N+1 starts.**

The temptation will be to jump to the hologram because it's the most exciting. Resist it. A beautiful hologram running a broken brain is a sad prop. A working brain in Telegram is already something real — something that feels alive. That's the foundation everything else stands on.

The first 5 phases (through proactive Tiff) will take about 5 weeks if you work consistently. By that point you'll have a companion that remembers you, reaches out first, and can control your home. That's already further than any commercial product goes.

Everything after that is making her more present. That's the direction: more real, more there, more yours.

---

*Serena Project — started May 2026*
