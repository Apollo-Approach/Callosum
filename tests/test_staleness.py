"""Tests for callosum.staleness — stale drawer and engram drift detection."""

import os
import pytest
from datetime import datetime, timedelta

import chromadb


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def palace_with_drawers(tmp_path):
    """Create a ChromaDB palace with seeded drawers pointing to real temp files."""
    palace_path = str(tmp_path / "palace")
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    client = chromadb.PersistentClient(path=palace_path)
    col = client.get_or_create_collection("callosum_drawers")

    # Create a real file and set its mtime to "old"
    fresh_file = project_dir / "fresh.py"
    fresh_file.write_text("# fresh code")
    old_time = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(fresh_file, (old_time, old_time))

    stale_file = project_dir / "stale.py"
    stale_file.write_text("# stale code - modified after mining")

    # File drawers with a filed_at between the two mtimes
    filed_at = (datetime.now() - timedelta(hours=1)).isoformat()

    col.add(
        ids=["d_fresh"],
        documents=["fresh code content"],
        metadatas=[
            {
                "wing": "myproject",
                "room": "backend",
                "source_file": str(fresh_file),
                "filed_at": filed_at,
            }
        ],
    )
    col.add(
        ids=["d_stale"],
        documents=["stale code content"],
        metadatas=[
            {
                "wing": "myproject",
                "room": "backend",
                "source_file": str(stale_file),
                "filed_at": filed_at,
            }
        ],
    )
    col.add(
        ids=["d_missing"],
        documents=["content from deleted file"],
        metadatas=[
            {
                "wing": "myproject",
                "room": "backend",
                "source_file": str(project_dir / "deleted.py"),
                "filed_at": filed_at,
            }
        ],
    )
    col.add(
        ids=["d_other_wing"],
        documents=["other wing content"],
        metadatas=[
            {
                "wing": "other",
                "room": "general",
                "source_file": str(fresh_file),
                "filed_at": filed_at,
            }
        ],
    )

    return {
        "palace_path": palace_path,
        "project_dir": str(project_dir),
        "fresh_file": fresh_file,
        "stale_file": stale_file,
    }


# ── check_stale_drawers ─────────────────────────────────────────────────────


class TestCheckStaleDrawers:
    def test_detects_stale_file(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"])
        stale_paths = [s["file"] for s in result["stale_files"]]
        assert str(palace_with_drawers["stale_file"]) in stale_paths

    def test_fresh_file_not_stale(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"])
        stale_paths = [s["file"] for s in result["stale_files"]]
        assert str(palace_with_drawers["fresh_file"]) not in stale_paths
        assert result["up_to_date"] >= 1

    def test_missing_file_counted(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"])
        assert result["missing"] >= 1

    def test_wing_filter(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"], wing="other")
        # "other" wing only has the fresh file
        assert result["stale_count"] == 0

    def test_result_structure(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"])
        assert "stale_files" in result
        assert "stale_count" in result
        assert "up_to_date" in result
        assert "missing" in result
        assert "total_tracked" in result

    def test_stale_entry_has_drift_seconds(self, palace_with_drawers):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(palace_with_drawers["palace_path"])
        for entry in result["stale_files"]:
            assert "drift_seconds" in entry
            assert entry["drift_seconds"] > 0

    def test_invalid_palace_returns_error(self, tmp_path):
        from callosum.staleness import check_stale_drawers

        result = check_stale_drawers(str(tmp_path / "nonexistent_palace"))
        assert "error" in result


# ── check_engram_drift ───────────────────────────────────────────────────────


class TestCheckEngramDrift:
    def test_missing_engram_dir(self, tmp_path):
        from callosum.staleness import check_engram_drift

        result = check_engram_drift(
            palace_path=str(tmp_path / "palace"),
            engram_dir=str(tmp_path / "nonexistent"),
        )
        assert "error" in result

    def test_empty_engram_dir(self, tmp_path):
        from callosum.staleness import check_engram_drift

        engram_dir = tmp_path / "knowledge"
        engram_dir.mkdir()
        result = check_engram_drift(
            palace_path=str(tmp_path / "palace"),
            engram_dir=str(engram_dir),
        )
        assert result["engrams_found"] == 0
        assert result["files"] == []

    def test_finds_md_and_json_files(self, tmp_path):
        from callosum.staleness import check_engram_drift

        engram_dir = tmp_path / "knowledge"
        engram_dir.mkdir()
        (engram_dir / "facts.md").write_text("# facts")
        (engram_dir / "data.json").write_text("{}")
        (engram_dir / "ignore.txt").write_text("not an engram")

        result = check_engram_drift(
            palace_path=str(tmp_path / "palace"),
            engram_dir=str(engram_dir),
        )
        assert result["engrams_found"] == 2
        engram_names = [e["engram"] for e in result["files"]]
        assert "facts.md" in engram_names
        assert "data.json" in engram_names
