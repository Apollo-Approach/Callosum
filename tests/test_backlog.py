"""
test_backlog.py — Tests for the agentic backlog / open-loop tracker.

Covers: add_loop, get_backlog (filtering by wing, status), resolve_loop,
ordering, and edge cases (nonexistent ID, re-resolve).
"""

import os
import time

from callosum.backlog import Backlog


def _make_backlog(tmp_dir):
    """Create a Backlog backed by a temp SQLite file."""
    db_path = os.path.join(tmp_dir, "test_backlog.sqlite3")
    return Backlog(db_path=db_path)


class TestAddLoop:
    def test_add_loop_returns_id(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        loop_id = bl.add_loop("project", "backend", "Fix auth bug")
        assert loop_id.startswith("loop_")
        assert len(loop_id) > len("loop_")

    def test_add_loop_unique_ids(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "Task A")
        # Small delay to ensure different timestamp in hash
        time.sleep(0.01)
        id2 = bl.add_loop("project", "backend", "Task B")
        assert id1 != id2

    def test_add_loop_with_description(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        loop_id = bl.add_loop(
            "notes", "planning", "Review docs", description="Need to review API docs"
        )
        items = bl.get_backlog()
        match = [i for i in items if i["id"] == loop_id]
        assert len(match) == 1
        assert match[0]["description"] == "Need to review API docs"


class TestGetBacklog:
    def test_get_backlog_returns_open_loops(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        bl.add_loop("project", "backend", "Open task 1")
        bl.add_loop("project", "frontend", "Open task 2")
        items = bl.get_backlog()
        assert len(items) == 2
        assert all(i["status"] == "open" for i in items)

    def test_get_backlog_filters_by_wing(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        bl.add_loop("project", "backend", "Backend task")
        bl.add_loop("notes", "planning", "Planning task")
        bl.add_loop("project", "frontend", "Frontend task")

        project_items = bl.get_backlog(wing="project")
        assert len(project_items) == 2
        assert all(i["wing"] == "project" for i in project_items)

        notes_items = bl.get_backlog(wing="notes")
        assert len(notes_items) == 1
        assert notes_items[0]["title"] == "Planning task"

    def test_get_backlog_filters_by_status_open(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "Task A")
        bl.add_loop("project", "backend", "Task B")
        bl.resolve_loop(id1)

        open_items = bl.get_backlog(status="open")
        assert len(open_items) == 1
        assert open_items[0]["title"] == "Task B"

    def test_get_backlog_filters_by_status_resolved(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "Task A")
        bl.add_loop("project", "backend", "Task B")
        bl.resolve_loop(id1)

        resolved_items = bl.get_backlog(status="resolved")
        assert len(resolved_items) == 1
        assert resolved_items[0]["title"] == "Task A"
        assert resolved_items[0]["resolved_at"] is not None

    def test_get_backlog_status_all(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "Task A")
        bl.add_loop("project", "backend", "Task B")
        bl.resolve_loop(id1)

        all_items = bl.get_backlog(status="all")
        assert len(all_items) == 2

    def test_get_backlog_empty(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        items = bl.get_backlog()
        assert items == []

    def test_get_backlog_combined_wing_and_status(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "Task A")
        bl.add_loop("notes", "planning", "Task B")
        bl.add_loop("project", "frontend", "Task C")
        bl.resolve_loop(id1)

        # Only resolved items in 'project' wing
        items = bl.get_backlog(wing="project", status="resolved")
        assert len(items) == 1
        assert items[0]["title"] == "Task A"


class TestResolveLoop:
    def test_resolve_loop_marks_resolved(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        loop_id = bl.add_loop("project", "backend", "Fix auth bug")
        result = bl.resolve_loop(loop_id)
        assert result is True

        items = bl.get_backlog(status="resolved")
        assert len(items) == 1
        assert items[0]["status"] == "resolved"
        assert items[0]["resolved_at"] is not None

    def test_resolve_loop_nonexistent_returns_false(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        result = bl.resolve_loop("loop_nonexistent_id")
        assert result is False

    def test_resolve_loop_already_resolved_returns_false(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        loop_id = bl.add_loop("project", "backend", "Fix auth bug")
        bl.resolve_loop(loop_id)
        # Second resolve should fail (already resolved, not open)
        result = bl.resolve_loop(loop_id)
        assert result is False


class TestOrdering:
    def test_multiple_loops_newest_first(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        id1 = bl.add_loop("project", "backend", "First task")
        id2 = bl.add_loop("project", "backend", "Second task")
        id3 = bl.add_loop("project", "backend", "Third task")

        # SQLite CURRENT_TIMESTAMP has 1-second resolution, so set
        # explicit timestamps to guarantee deterministic ordering.
        import sqlite3

        conn = sqlite3.connect(bl.db_path)
        conn.execute("UPDATE loops SET created_at = '2026-01-01T00:00:01' WHERE id = ?", (id1,))
        conn.execute("UPDATE loops SET created_at = '2026-01-01T00:00:02' WHERE id = ?", (id2,))
        conn.execute("UPDATE loops SET created_at = '2026-01-01T00:00:03' WHERE id = ?", (id3,))
        conn.commit()
        conn.close()

        items = bl.get_backlog()
        assert len(items) == 3
        # ORDER BY created_at DESC — newest first
        assert items[0]["title"] == "Third task"
        assert items[1]["title"] == "Second task"
        assert items[2]["title"] == "First task"


class TestRecordStructure:
    def test_loop_record_has_expected_keys(self, tmp_dir):
        bl = _make_backlog(tmp_dir)
        bl.add_loop("project", "backend", "Fix auth bug", description="JWT issue")
        items = bl.get_backlog()
        assert len(items) == 1
        item = items[0]
        expected_keys = {
            "id",
            "wing",
            "room",
            "title",
            "description",
            "status",
            "created_at",
            "resolved_at",
        }
        assert set(item.keys()) == expected_keys
        assert item["wing"] == "project"
        assert item["room"] == "backend"
        assert item["title"] == "Fix auth bug"
        assert item["description"] == "JWT issue"
        assert item["status"] == "open"
        assert item["resolved_at"] is None
