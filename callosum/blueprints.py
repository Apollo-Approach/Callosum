"""
blueprints.py -- Architectural Blueprints and System Snapshots
==============================================================

Stores macroscopic topology maps, system architectures, and JSON state trees.
"""

import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_BLUEPRINTS_PATH = os.path.expanduser("~/.callosum/blueprints.sqlite3")


class Blueprints:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_BLUEPRINTS_PATH
        self._lock = threading.Lock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS blueprints (
                id TEXT PRIMARY KEY,
                wing TEXT NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_blueprints_wing ON blueprints(wing);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_blueprints_wing_name ON blueprints(wing, name);
        """)
        conn.commit()
        conn.close()

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def save_blueprint(self, wing: str, name: str, content: str) -> str:
        """Save or overwrite an architectural blueprint."""
        blueprint_id = f"bp_{wing}_{name}"
        now = datetime.now().isoformat()
        conn = self._conn()
        conn.execute(
            """INSERT INTO blueprints (id, wing, name, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(wing, name) DO UPDATE SET
               content=excluded.content, updated_at=excluded.updated_at""",
            (blueprint_id, wing, name, content, now, now),
        )
        conn.commit()
        conn.close()
        return blueprint_id

    def load_blueprint(self, wing: str, name: str) -> dict:
        """Retrieve a specific blueprint."""
        conn = self._conn()
        row = conn.execute(
            "SELECT id, content, created_at, updated_at FROM blueprints WHERE wing = ? AND name = ?",
            (wing, name),
        ).fetchone()
        conn.close()

        if row:
            return {
                "id": row[0],
                "wing": wing,
                "name": name,
                "content": row[1],
                "created_at": row[2],
                "updated_at": row[3],
            }
        return None

    def list_blueprints(self, wing: str = None) -> list:
        """List all available blueprints, optionally filtered by wing."""
        conn = self._conn()

        query = "SELECT id, wing, name, created_at, updated_at FROM blueprints WHERE 1=1"
        params = []

        if wing:
            query += " AND wing = ?"
            params.append(wing)

        query += " ORDER BY updated_at DESC"

        results = []
        for row in conn.execute(query, params).fetchall():
            results.append(
                {
                    "id": row[0],
                    "wing": row[1],
                    "name": row[2],
                    "created_at": row[3],
                    "updated_at": row[4],
                }
            )

        conn.close()
        return results
