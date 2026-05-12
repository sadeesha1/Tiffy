"""
memory.py
---------
Handles all persistence for Tiff:
  - facts    → long-term memories, stored in SQLite + Qdrant vector index
  - messages → conversation history
  - threads  → open/unresolved topics Tiff is tracking
"""

import atexit
import sqlite3
import logging

from config import DB_PATH, QDRANT_PATH, EMBEDDING_MODEL, HISTORY_SUMMARISE_THRESHOLD

logger = logging.getLogger(__name__)

COLLECTION = "tiff_facts"

# ── Lazy singletons (heavy — load once, reuse forever) ────────────────────────

_embedder = None
_qdrant   = None


def _shutdown():
    """Cleanly close Qdrant before Python tears down — prevents the __del__ noise."""
    global _qdrant
    if _qdrant is not None:
        try:
            _qdrant.close()
        except Exception:
            pass
        _qdrant = None

atexit.register(_shutdown)


def _get_embedder():
    global _embedder
    if _embedder is None:
        from fastembed import TextEmbedding
        logger.info("Loading embedding model…")
        _embedder = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2")
        logger.info("Embedding model ready ✓")
    return _embedder


def _get_qdrant():
    global _qdrant
    if _qdrant is None:
        from qdrant_client import QdrantClient
        _qdrant = QdrantClient(path=QDRANT_PATH)
    return _qdrant


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables and vector collection. Safe to call on every startup."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                role       TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                summary    TEXT    NOT NULL,
                status     TEXT    NOT NULL DEFAULT 'open',
                created_at TEXT    DEFAULT (datetime('now')),
                updated_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message    TEXT    NOT NULL,
                remind_at  TEXT    NOT NULL,
                sent       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS people (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                details    TEXT    NOT NULL,
                updated_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT    NOT NULL,
                content    TEXT    NOT NULL,
                tags       TEXT    DEFAULT '',
                created_at TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mood_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                score      INTEGER NOT NULL,
                note       TEXT    DEFAULT '',
                logged_at  TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learnings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                category    TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                fingerprint TEXT    NOT NULL,
                recurrence  INTEGER DEFAULT 1,
                promoted    INTEGER DEFAULT 0,
                created_at  TEXT    DEFAULT (datetime('now')),
                last_seen   TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    try:
        _init_vector_db()
    except Exception as e:
        logger.warning(f"Vector DB init skipped (torch not available?): {e}")


def _init_vector_db():
    """Ensure Qdrant collection exists and sync any unindexed SQLite facts."""
    from qdrant_client import models as qm
    q = _get_qdrant()
    existing = {c.name for c in q.get_collections().collections}
    if COLLECTION not in existing:
        q.create_collection(
            COLLECTION,
            vectors_config=qm.VectorParams(size=384, distance=qm.Distance.COSINE),
        )
    _sync_missing_facts()


def _sync_missing_facts():
    """Upsert any SQLite facts that are not yet in the Qdrant index."""
    from qdrant_client import models as qm
    q = _get_qdrant()

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT id, content FROM facts ORDER BY id").fetchall()

    if not rows:
        return

    scroll_result, _ = q.scroll(
        COLLECTION, limit=10_000, with_payload=False, with_vectors=False
    )
    indexed_ids = {pt.id for pt in scroll_result}
    missing = [(id_, content) for id_, content in rows if id_ not in indexed_ids]

    if not missing:
        return

    logger.info(f"Syncing {len(missing)} facts to vector index…")
    embedder = _get_embedder()
    ids      = [r[0] for r in missing]
    contents = [r[1] for r in missing]
    vectors  = [v.tolist() for v in embedder.embed(contents)]

    q.upsert(
        collection_name=COLLECTION,
        points=[
            qm.PointStruct(id=id_, vector=vec, payload={"content": content})
            for id_, vec, content in zip(ids, vectors, contents)
        ],
    )
    logger.info(f"Vector sync complete ✓ ({len(missing)} facts indexed)")


# ── Facts (long-term memory) ──────────────────────────────────────────────────

def remember(content: str) -> str:
    """Save a fact to SQLite and immediately index it in Qdrant."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("INSERT INTO facts (content) VALUES (?)", (content,))
            new_id = cursor.lastrowid
            conn.commit()

        from qdrant_client import models as qm
        vector = next(_get_embedder().embed([content])).tolist()
        _get_qdrant().upsert(
            collection_name=COLLECTION,
            points=[qm.PointStruct(id=new_id, vector=vector, payload={"content": content})],
        )
        return f"Remembered: {content}"
    except Exception as e:
        return f"Couldn't remember that: {e}"


def _recall_text_fallback(query: str) -> str:
    """
    Simple SQLite keyword search — used when Qdrant is unavailable.
    Splits query into keywords and returns facts that match any of them.
    """
    try:
        keywords = [w for w in query.lower().split() if len(w) > 3][:5]
        if not keywords:
            facts = get_all_facts()
            return "\n".join(facts[:5]) if facts else ""

        with sqlite3.connect(DB_PATH) as conn:
            results = set()
            for kw in keywords:
                rows = conn.execute(
                    "SELECT content FROM facts WHERE LOWER(content) LIKE ? ORDER BY id DESC LIMIT 5",
                    (f"%{kw}%",)
                ).fetchall()
                for r in rows:
                    results.add(r[0])

        if results:
            return "\n".join(list(results)[:8])

        # Last resort: most recent facts
        facts = get_all_facts()
        return "\n".join(facts[:5]) if facts else ""
    except Exception:
        return ""


def recall(query: str) -> str:
    """
    Semantic search over long-term memory using vector similarity.
    Falls back to SQLite keyword search if Qdrant is unavailable.
    """
    try:
        q_vec = next(_get_embedder().embed([query])).tolist()
        hits = _get_qdrant().search(
            collection_name=COLLECTION,
            query_vector=q_vec,
            limit=8,
            score_threshold=0.3,
        )
        if hits:
            return "\n".join(h.payload["content"] for h in hits)

        # Vector search returned nothing — try recent facts
        recent = get_all_facts()
        if not recent:
            return ""
        return "\n".join(recent[:5])

    except Exception as e:
        logger.warning(f"Vector recall failed ({type(e).__name__}), using text fallback")
        return _recall_text_fallback(query)


def get_all_facts() -> list[str]:
    """Return all facts ordered newest-first."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute("SELECT content FROM facts ORDER BY id DESC")
        return [row[0] for row in cursor.fetchall()]


def delete_fact(fact_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        conn.commit()
    try:
        _get_qdrant().delete(collection_name=COLLECTION, points_selector=[fact_id])
    except Exception:
        pass


# ── Threads ───────────────────────────────────────────────────────────────────

def open_thread(summary: str) -> str:
    """Flag an unresolved topic as an open thread."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO threads (summary) VALUES (?)", (summary,))
            conn.commit()
        return f"Thread opened: {summary}"
    except Exception as e:
        return f"Couldn't open thread: {e}"


def close_thread(thread_id: int) -> str:
    """Mark a thread resolved by its ID."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE threads SET status='resolved', updated_at=datetime('now') WHERE id=?",
                (thread_id,),
            )
            conn.commit()
        return f"Thread {thread_id} resolved."
    except Exception as e:
        return f"Couldn't resolve thread: {e}"


def get_open_threads() -> list[dict]:
    """Return all open threads, newest first."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT id, summary, created_at FROM threads WHERE status='open' ORDER BY id DESC"
        )
        return [{"id": r[0], "summary": r[1], "created_at": r[2]} for r in cursor.fetchall()]


# ── Reminders ────────────────────────────────────────────────────────────────

def set_reminder(message: str, remind_at: str) -> str:
    """Save a reminder. remind_at is ISO datetime in Sri Lanka local time."""
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        # Normalise format: accept "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM:SS"
        remind_at = remind_at.strip().replace(" ", "T")
        if len(remind_at) == 16:
            remind_at += ":00"
        # Sanity-check it's parseable
        datetime.fromisoformat(remind_at)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO reminders (message, remind_at) VALUES (?, ?)",
                (message, remind_at)
            )
            conn.commit()
        display = remind_at.replace("T", " ")
        return f"Reminder set for {display} (Sri Lanka time): {message}"
    except Exception as e:
        return f"Couldn't set reminder: {e}"


def get_due_reminders() -> list[dict]:
    """Return all unsent reminders whose remind_at has passed."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Colombo")).strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT id, message, remind_at FROM reminders WHERE sent=0 AND remind_at <= ?",
            (now,)
        )
        return [{"id": r[0], "message": r[1], "remind_at": r[2]} for r in cursor.fetchall()]


def mark_reminder_sent(reminder_id: int):
    """Mark a reminder as delivered."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE reminders SET sent=1 WHERE id=?", (reminder_id,))
        conn.commit()


# ── Settings (key-value store) ────────────────────────────────────────────────

def save_setting(key: str, value: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value)
        )
        conn.commit()


def get_setting(key: str) -> str | None:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


# ── People (relationship memory) ──────────────────────────────────────────────

def remember_person(name: str, details: str) -> str:
    """Save or update what Tiff knows about a specific person."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT id, details FROM people WHERE LOWER(name)=LOWER(?)", (name,)
            ).fetchone()
            if existing:
                merged = existing[1] + "\n" + details
                conn.execute(
                    "UPDATE people SET details=?, updated_at=datetime('now') WHERE id=?",
                    (merged, existing[0])
                )
            else:
                conn.execute(
                    "INSERT INTO people (name, details) VALUES (?, ?)", (name, details)
                )
            conn.commit()
        return f"Remembered about {name}: {details}"
    except Exception as e:
        return f"Couldn't save person: {e}"


def get_person(name: str) -> str:
    """Retrieve everything Tiff knows about a named person."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT name, details FROM people WHERE LOWER(name)=LOWER(?)", (name,)
            ).fetchone()
        if row:
            return f"What I know about {row[0]}:\n{row[1]}"
        return f"I don't have any notes about {name} yet."
    except Exception as e:
        return f"Couldn't retrieve person: {e}"


# ── Notes ─────────────────────────────────────────────────────────────────────

def save_note(title: str, content: str, tags: str = "") -> str:
    """Save a note with optional tags (comma-separated)."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO notes (title, content, tags) VALUES (?, ?, ?)",
                (title, content, tags)
            )
            conn.commit()
        return f"Note saved: '{title}'"
    except Exception as e:
        return f"Couldn't save note: {e}"


def search_notes(query: str) -> str:
    """Search notes by title, content, or tags (simple text match)."""
    try:
        q = f"%{query}%"
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT title, content, tags, created_at FROM notes "
                "WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? ORDER BY id DESC LIMIT 5",
                (q, q, q)
            ).fetchall()
        if not rows:
            return f"No notes found matching '{query}'."
        lines = [f"Notes matching '{query}':"]
        for title, content, tags, created in rows:
            preview = content[:100] + ("…" if len(content) > 100 else "")
            tag_str = f" [{tags}]" if tags else ""
            lines.append(f"\n• {title}{tag_str} ({created[:10]})\n  {preview}")
        return "\n".join(lines)
    except Exception as e:
        return f"Couldn't search notes: {e}"


# ── Mood tracking ─────────────────────────────────────────────────────────────

def log_mood(score: int, note: str = "") -> str:
    """Log Sadeesha's mood (1-10 scale) with an optional note."""
    try:
        score = max(1, min(10, int(score)))
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO mood_log (score, note) VALUES (?, ?)", (score, note)
            )
            conn.commit()
        emoji = "😊" if score >= 7 else "😐" if score >= 4 else "😔"
        return f"Mood logged: {score}/10 {emoji}" + (f" — {note}" if note else "")
    except Exception as e:
        return f"Couldn't log mood: {e}"


def get_mood_trend(days: int = 7) -> str:
    """Return mood summary for the past N days."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT score, note, logged_at FROM mood_log "
                "WHERE logged_at >= datetime('now', ?) ORDER BY logged_at DESC",
                (f"-{days} days",)
            ).fetchall()
        if not rows:
            return ""
        avg = sum(r[0] for r in rows) / len(rows)
        latest = rows[0]
        trend = f"Mood trend ({days}d): avg {avg:.1f}/10 from {len(rows)} logs. Latest: {latest[0]}/10"
        if latest[1]:
            trend += f" — {latest[1]}"
        return trend
    except Exception:
        return ""


def get_reminders_for_today() -> list[dict]:
    """Return all unsent reminders due in the next 24 hours."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Colombo"))
    start = now.strftime("%Y-%m-%dT%H:%M:%S")
    end   = now.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT id, message, remind_at FROM reminders "
            "WHERE sent=0 AND remind_at BETWEEN ? AND ? ORDER BY remind_at",
            (start, end)
        )
        return [{"id": r[0], "message": r[1], "remind_at": r[2]} for r in cursor.fetchall()]


# ── Conversation history ──────────────────────────────────────────────────────

def save_message(role: str, content: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO messages (role, content) VALUES (?, ?)", (role, content))
        conn.commit()


def get_history(limit: int = 30) -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        )
        rows = cursor.fetchall()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]


def get_history_since(hours: int = 24) -> list[dict]:
    """Return all messages from the last N hours, chronological order."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE created_at >= datetime('now', ?) ORDER BY id",
            (f"-{hours} hours",),
        )
        return [{"role": r[0], "content": r[1]} for r in cursor.fetchall()]


def maybe_summarise_history() -> bool:
    """
    If the messages table has grown past HISTORY_SUMMARISE_THRESHOLD rows,
    compress all but the most recent MAX_HISTORY_TURNS messages into a single
    summary row, then delete the originals.

    The summary is generated by Claude (Haiku — cheap) and stored as a
    system-style 'assistant' message so it blends naturally into the history
    that get_history() returns.

    Returns True if a summary was written, False if nothing needed doing.
    Called from brain.py after each assistant reply.
    """
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    if total <= HISTORY_SUMMARISE_THRESHOLD:
        return False

    # Keep the newest MAX_HISTORY_TURNS rows; summarise everything older
    with sqlite3.connect(DB_PATH) as conn:
        keep_ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM messages ORDER BY id DESC LIMIT ?",
                (HISTORY_SUMMARISE_THRESHOLD // 2,)
            ).fetchall()
        ]
        old_rows = conn.execute(
            f"SELECT role, content FROM messages "
            f"WHERE id NOT IN ({','.join('?' * len(keep_ids))}) ORDER BY id",
            keep_ids
        ).fetchall()

    if not old_rows:
        return False

    # Build a compact transcript for Claude to summarise
    transcript = "\n".join(
        f"{role.upper()}: {content[:300]}" for role, content in old_rows
    )

    prompt_msg = (
        "Summarise this older portion of a conversation between Tiff (AI companion) "
        "and Sadeesha. Write 3-5 concise sentences covering key topics, decisions, "
        "mood, anything worth remembering. Third person, factual, brief.\n\n"
        f"TRANSCRIPT:\n{transcript}"
    )

    try:
        from config import AI_BACKEND, ANTHROPIC_API_KEY, FAST_MODEL
        from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_API_KEY

        # Use the active backend setting from DB (runtime override) or env default
        with sqlite3.connect(DB_PATH) as _c:
            _row = _c.execute("SELECT value FROM settings WHERE key='ai_backend'").fetchone()
        active_backend = (_row[0] if _row else None) or AI_BACKEND

        if active_backend == "claude" and ANTHROPIC_API_KEY:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            resp = client.messages.create(
                model=FAST_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt_msg}]
            )
            summary_text = resp.content[0].text.strip()
        else:
            # Ollama (or Claude key missing) — use OpenAI-compatible client
            import os
            from openai import OpenAI
            base_url = os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
            api_key  = os.getenv("OLLAMA_API_KEY",  OLLAMA_API_KEY) or "ollama"
            model    = os.getenv("OLLAMA_MODEL",    OLLAMA_MODEL)
            client   = OpenAI(base_url=base_url, api_key=api_key)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt_msg}],
                temperature=0.3,
            )
            summary_text = resp.choices[0].message.content.strip()

    except Exception as e:
        logger.warning(f"History summarisation failed: {e}")
        return False

    # Replace old rows with the summary
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            f"DELETE FROM messages WHERE id NOT IN ({','.join('?' * len(keep_ids))})",
            keep_ids
        )
        conn.execute(
            "INSERT INTO messages (role, content, created_at) VALUES (?, ?, datetime('now'))",
            ("assistant", f"[SUMMARY OF EARLIER CONVERSATION]\n{summary_text}")
        )
        conn.commit()

    logger.info(f"History summarised: {len(old_rows)} messages → 1 summary row")
    return True


def clear_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages")
        conn.commit()


def clear_all():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM facts")
        conn.execute("DELETE FROM threads")
        conn.commit()
    try:
        from qdrant_client import models as qm
        q = _get_qdrant()
        q.delete_collection(COLLECTION)
        q.create_collection(
            COLLECTION,
            vectors_config=qm.VectorParams(size=384, distance=qm.Distance.COSINE),
        )
    except Exception:
        pass


# ── Self-learning: signal extraction ─────────────────────────────────────────

_PREFERENCE_TRIGGERS = [
    "i like ", "i love ", "i enjoy ", "i adore ", "i prefer ",
    "my favorite ", "my favourite ", "i really like ", "i really love ",
]
_AVERSION_TRIGGERS = [
    "i hate ", "i dislike ", "i can't stand ", "i cant stand ",
    "i don't like ", "i dont like ", "i never want ",
]
_HABIT_TRIGGERS = [
    "i always ", "i usually ", "i typically ", "i normally ",
    "every morning ", "every night ", "every day ", "every week ",
]
_CORRECTION_STARTS = [
    "no,", "no.", "no!", "no that", "actually,", "actually.", "actually i",
    "that's wrong", "thats wrong", "you're wrong", "you got it wrong",
    "that's not right", "thats not right", "i said ",
]


def _extract_signals_from_text(text: str) -> list[tuple[str, str]]:
    """
    Pure Python signal extraction from a user message.
    Returns a list of (category, content) tuples.
    Zero cost — no LLM calls, no network.

    Categories: preference, aversion, habit, correction
    """
    tl   = text.lower().strip()
    results: list[tuple[str, str]] = []

    def _clip_after(trigger: str, max_len: int = 70) -> str | None:
        """Extract text after a trigger phrase, trimmed at sentence boundary."""
        idx = tl.find(trigger)
        if idx == -1:
            return None
        start   = idx + len(trigger)
        snippet = text[start : start + max_len]  # preserve original case
        for sep in [".", "!", "?", "\n"]:
            p = snippet.find(sep)
            if p > 4:
                snippet = snippet[:p]
                break
        return snippet.strip()

    for trigger in _PREFERENCE_TRIGGERS:
        part = _clip_after(trigger)
        if part and 4 < len(part) < 65:
            verb = trigger.strip().replace("my ", "his ").replace("i ", "he ")
            results.append(("preference", f"Sadeesha {verb} {part}"))

    for trigger in _AVERSION_TRIGGERS:
        part = _clip_after(trigger)
        if part and 4 < len(part) < 65:
            results.append(("aversion", f"Sadeesha dislikes: {part}"))

    for trigger in _HABIT_TRIGGERS:
        part = _clip_after(trigger)
        if part and 4 < len(part) < 65:
            results.append(("habit", f"Sadeesha's habit: {trigger.strip()} {part}"))

    if any(tl.startswith(t) for t in _CORRECTION_STARTS) and len(text) > 15:
        snippet = text[:100].replace("\n", " ")
        results.append(("correction", f"Correction from Sadeesha: {snippet}"))

    return results


# ── Self-learning: capture, deduplicate, promote ──────────────────────────────

_PROMOTION_THRESHOLD = 3  # recurrences before auto-promoting to core facts


def _fingerprint(content: str) -> str:
    """Normalised short key used for deduplication."""
    import re
    s = content.lower().strip()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return " ".join(s.split()[:8])  # first 8 words, normalised


def capture_learning(category: str, content: str) -> str:
    """
    Save a learning signal to the learnings table.
    If a similar entry already exists, increment its recurrence count.
    When recurrence hits _PROMOTION_THRESHOLD, auto-promote to core facts.
    Returns a short status string.
    """
    fp = _fingerprint(content)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT id, recurrence, promoted FROM learnings WHERE fingerprint=?",
                (fp,)
            ).fetchone()

            if existing:
                rec_id, old_rec, already_promoted = existing
                new_rec = old_rec + 1
                conn.execute(
                    "UPDATE learnings SET recurrence=?, last_seen=datetime('now') WHERE id=?",
                    (new_rec, rec_id)
                )
                conn.commit()
                if new_rec >= _PROMOTION_THRESHOLD and not already_promoted:
                    _promote_learning_by_id(rec_id, content)
                    return f"promoted ({new_rec}x)"
                return f"updated ({new_rec}x)"
            else:
                conn.execute(
                    "INSERT INTO learnings (category, content, fingerprint) VALUES (?,?,?)",
                    (category, content, fp)
                )
                conn.commit()
                return "new"
    except Exception as e:
        logger.warning(f"capture_learning error: {e}")
        return "error"


def _promote_learning_by_id(learning_id: int, content: str):
    """
    Promote a learning to core facts table.
    SQLite write always happens. Vector indexing is best-effort — if Qdrant
    is locked, the fact is still saved and will be picked up by _sync_missing_facts
    on the next startup.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute("INSERT INTO facts (content) VALUES (?)", (content,))
            new_id = cursor.lastrowid
            conn.execute("UPDATE learnings SET promoted=1 WHERE id=?", (learning_id,))
            conn.commit()
        logger.info(f"Learning promoted to fact (id={new_id}): {content[:60]}")
    except Exception as e:
        logger.warning(f"Learning promotion (SQLite) failed: {e}")
        return

    # Vector indexing — best-effort, don't block on Qdrant errors
    try:
        from qdrant_client import models as qm
        vector = next(_get_embedder().embed([content])).tolist()
        _get_qdrant().upsert(
            collection_name=COLLECTION,
            points=[qm.PointStruct(id=new_id, vector=vector, payload={"content": content})],
        )
    except Exception as e:
        logger.warning(f"Vector index for promoted fact deferred (Qdrant busy): {e}")


def get_learnings(category: str | None = None, limit: int = 30) -> list[dict]:
    """Return learnings, optionally filtered by category, ordered by recurrence."""
    with sqlite3.connect(DB_PATH) as conn:
        if category:
            rows = conn.execute(
                "SELECT id, category, content, recurrence, promoted, created_at "
                "FROM learnings WHERE category=? AND promoted=0 ORDER BY recurrence DESC LIMIT ?",
                (category, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, category, content, recurrence, promoted, created_at "
                "FROM learnings WHERE promoted=0 ORDER BY recurrence DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [
        {"id": r[0], "category": r[1], "content": r[2],
         "recurrence": r[3], "promoted": r[4], "created_at": r[5]}
        for r in rows
    ]


# ── Memory hygiene ────────────────────────────────────────────────────────────

def purge_facts_by_keyword(keyword: str) -> int:
    """
    Delete all facts whose content contains keyword (case-insensitive).
    Also removes from the Qdrant vector index.
    Returns the number of facts deleted.
    """
    with sqlite3.connect(DB_PATH) as conn:
        ids = [
            r[0] for r in conn.execute(
                "SELECT id FROM facts WHERE LOWER(content) LIKE ?",
                (f"%{keyword.lower()}%",)
            ).fetchall()
        ]
        if not ids:
            return 0
        conn.execute(
            f"DELETE FROM facts WHERE id IN ({','.join('?'*len(ids))})", ids
        )
        conn.commit()

    try:
        _get_qdrant().delete(
            collection_name=COLLECTION,
            points_selector=ids,
        )
    except Exception:
        pass

    return len(ids)


# ── Owner profile ─────────────────────────────────────────────────────────────

def get_owner_profile() -> str:
    """
    Build a structured, human-readable profile of what Tiff knows about Sadeesha.
    Used by the /profile command.
    """
    facts = get_all_facts()
    learnings = get_learnings(limit=50)

    by_category: dict[str, list[str]] = {}
    for l in learnings:
        by_category.setdefault(l["category"], []).append(
            f"{l['content']}  (seen {l['recurrence']}×)"
        )

    emoji_map = {
        "preference": "❤️",
        "aversion":   "❌",
        "habit":      "🔄",
        "correction": "📝",
        "interest":   "✨",
        "context":    "📍",
    }

    lines = ["what tiff knows about sadeesha 🤍", ""]

    # Core facts
    real_facts = [f for f in facts if "smoke test" not in f.lower()]
    if real_facts:
        lines.append(f"📌 core facts ({len(real_facts)}):")
        for f in real_facts[:20]:
            lines.append(f"  • {f}")
    else:
        lines.append("📌 core facts: none yet")

    # Learnings by category
    for cat, items in by_category.items():
        em = emoji_map.get(cat, "•")
        lines.append(f"\n{em} {cat}s ({len(items)}):")
        for item in items[:6]:
            lines.append(f"  • {item}")

    # Mood
    mood = get_mood_trend(30)
    if mood:
        lines.append(f"\n📊 {mood}")

    # Stats
    total_learnings = sum(len(v) for v in by_category.values())
    lines.append(f"\n━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"  {len(real_facts)} facts  |  {total_learnings} learnings pending")

    return "\n".join(lines)
