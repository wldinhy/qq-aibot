from __future__ import annotations

import sqlite3
import time
import hashlib
from pathlib import Path
from contextlib import closing


def persona_key(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class ConversationStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                persona_key TEXT NOT NULL DEFAULT ''
            )
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(messages)")}
        if "persona_key" not in columns:
            # sqlite.backup includes committed WAL pages; preserve all legacy rows.
            backup_dir = path.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup_path = backup_dir / f"{path.stem}.before-persona-{time.time_ns()}.db"
            with closing(sqlite3.connect(backup_path)) as backup:
                self.connection.backup(backup)
            self.connection.execute("ALTER TABLE messages ADD COLUMN persona_key TEXT NOT NULL DEFAULT ''")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id)"
        )
        self.connection.commit()

    def history(self, session_id: str, limit: int, persona: str | None = None) -> list[dict[str, str]]:
        predicate = "session_id = ?" + (" AND persona_key = ?" if persona is not None else "")
        params = [session_id] + ([persona] if persona is not None else []) + [max(0, limit)]
        rows = self.connection.execute(
            f"""
            SELECT role, content FROM (
                SELECT id, role, content FROM messages
                WHERE {predicate} ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            params,
        ).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def add(self, session_id: str, role: str, content: str, keep: int, persona: str = "") -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO messages(session_id, role, content, created_at, persona_key) VALUES (?, ?, ?, ?, ?)",
                (session_id, role, content, int(time.time()), persona),
            )
            self.connection.execute(
                """
                DELETE FROM messages WHERE session_id = ? AND persona_key = ? AND id NOT IN (
                    SELECT id FROM messages WHERE session_id = ? AND persona_key = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (session_id, persona, session_id, persona, max(1, keep)),
            )

    def clear(self, session_id: str | None = None) -> int:
        with self.connection:
            if session_id is None:
                cursor = self.connection.execute("DELETE FROM messages")
            else:
                cursor = self.connection.execute(
                    "DELETE FROM messages WHERE session_id = ?", (session_id,)
                )
        return cursor.rowcount

    def close(self) -> None:
        self.connection.close()
