"""
backlog.py -- Agentic Backlog and Open Loops
===========================================

Tracks unresolved tasks and deferred ideas across sessions.
"""

import os
import sqlite3
import threading
import hashlib
from datetime import datetime
from pathlib import Path

DEFAULT_BACKLOG_PATH = os.path.expanduser("~/.callosum/backlog.sqlite3")


class Backlog:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_BACKLOG_PATH
        self._lock = threading.Lock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS loops (
                id TEXT PRIMARY KEY,
                wing TEXT NOT NULL,
                room TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_loops_wing ON loops(wing);
            CREATE INDEX IF NOT EXISTS idx_loops_status ON loops(status);
        """)
        conn.commit()
        conn.close()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def add_loop(self, wing: str, room: str, title: str, description: str = "") -> str:
        """Add a new open loop."""
        loop_id = f"loop_{hashlib.md5((wing + title + datetime.now().isoformat()).encode()).hexdigest()[:12]}"
        conn = self._conn()
        conn.execute(
            """INSERT INTO loops (id, wing, room, title, description, status)
               VALUES (?, ?, ?, ?, ?, 'open')""",
            (loop_id, wing, room, title, description),
        )
        conn.commit()
        conn.close()
        return loop_id

    def get_backlog(self, wing: str = None, status: str = "open") -> list:
        """Retrieve backlog items, optionally filtered by wing and status."""
        conn = self._conn()

        query = "SELECT id, wing, room, title, description, status, created_at, resolved_at FROM loops WHERE 1=1"
        params = []

        if status != "all":
            query += " AND status = ?"
            params.append(status)

        if wing:
            query += " AND wing = ?"
            params.append(wing)

        query += " ORDER BY created_at DESC"

        results = []
        for row in conn.execute(query, params).fetchall():
            results.append(
                {
                    "id": row[0],
                    "wing": row[1],
                    "room": row[2],
                    "title": row[3],
                    "description": row[4],
                    "status": row[5],
                    "created_at": row[6],
                    "resolved_at": row[7],
                }
            )

        conn.close()
        return results

    def resolve_loop(self, loop_id: str) -> bool:
        """Mark a loop as resolved."""
        conn = self._conn()
        now = datetime.now().isoformat()
        cursor = conn.execute(
            "UPDATE loops SET status = 'resolved', resolved_at = ? WHERE id = ? AND status = 'open'",
            (now, loop_id),
        )
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
