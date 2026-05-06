"""
memory.py
---------
Handles all persistence for Tiff:
  - facts      → things she has explicitly remembered about Sadeesha
  - messages   → conversation history (text turns only, no tool calls)
"""

import sqlite3
from config import DB_PATH


# ── Schema ───────────────────────────────────────────────────────────────────

def init_db():
    """Create tables if they don't exist. Safe to call on every startup."""
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
        conn.commit()


# ── Facts (long-term memory) ──────────────────────────────────────────────────

def remember(content: str) -> str:
    """Save a fact to long-term memory. Returns a confirmation string."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("INSERT INTO facts (content) VALUES (?)", (content,))
            conn.commit()
        return f"Remembered: {content}"
    except Exception as e:
        return f"Couldn't remember that: {e}"


def recall(query: str) -> str:
    """
    Search long-term memory for facts matching the query.
    Uses simple keyword scoring — good enough for Phase 1.
    Returns up to 8 matching facts, or 5 most recent as fallback.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT content FROM facts ORDER BY id DESC"
            )
            all_facts = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        return f"Couldn't search memory: {e}"

    if not all_facts:
        return "No memories yet."

    query_words = [w.lower() for w in query.split() if len(w) > 2]

    if not query_words:
        return "\n".join(all_facts[:5])

    scored = []
    for fact in all_facts:
        score = sum(1 for w in query_words if w in fact.lower())
        if score > 0:
            scored.append((score, fact))

    scored.sort(key=lambda x: x[0], reverse=True)
    results = [f for _, f in scored[:8]]

    if not results:
        return "No matching memories. Recent facts:\n" + "\n".join(all_facts[:5])

    return "\n".join(results)


def get_all_facts() -> list[str]:
    """Return all facts ordered newest-first."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT content FROM facts ORDER BY created_at DESC"
        )
        return [row[0] for row in cursor.fetchall()]


def delete_fact(fact_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        conn.commit()


# ── Conversation history ──────────────────────────────────────────────────────

def save_message(role: str, content: str):
    """Persist a single conversation turn."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO messages (role, content) VALUES (?, ?)",
            (role, content)
        )
        conn.commit()


def get_history(limit: int = 30) -> list[dict]:
    """
    Return the last `limit` turns as a list of {role, content} dicts
    in chronological order (oldest first), ready to pass to the Claude API.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT role, content FROM messages ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()

    rows.reverse()  # Oldest first
    return [{"role": row[0], "content": row[1]} for row in rows]


def clear_history():
    """Wipe conversation history. Keeps long-term facts."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages")
        conn.commit()


def clear_all():
    """Wipe everything — history AND facts."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM facts")
        conn.commit()
