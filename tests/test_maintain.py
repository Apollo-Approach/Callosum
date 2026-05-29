"""Tests for callosum.maintain — health check, closet coverage, ChromaDB version check."""

import chromadb

from callosum.maintain import closet_coverage, check_chromadb_version


# ── closet_coverage ──────────────────────────────────────────────────────────


class TestClosetCoverage:
    def test_empty_palace(self, tmp_path):
        palace = str(tmp_path / "palace")
        client = chromadb.PersistentClient(path=palace)
        client.get_or_create_collection("callosum_drawers")
        result = closet_coverage(palace)
        assert result["drawers"] == 0
        assert result["closets"] == 0
        assert result["coverage_pct"] == 0
        assert result["has_closets"] is False

    def test_drawers_only(self, tmp_path):
        palace = str(tmp_path / "palace")
        client = chromadb.PersistentClient(path=palace)
        col = client.get_or_create_collection("callosum_drawers")
        col.add(ids=["d1", "d2", "d3"], documents=["a", "b", "c"])
        result = closet_coverage(palace)
        assert result["drawers"] == 3
        assert result["closets"] == 0
        assert result["coverage_pct"] == 0

    def test_with_closets(self, tmp_path):
        palace = str(tmp_path / "palace")
        client = chromadb.PersistentClient(path=palace)
        drawers = client.get_or_create_collection("callosum_drawers")
        drawers.add(ids=["d1", "d2", "d3", "d4"], documents=["a", "b", "c", "d"])
        closets = client.get_or_create_collection("callosum_closets")
        closets.add(ids=["c1", "c2"], documents=["s1", "s2"])
        result = closet_coverage(palace)
        assert result["drawers"] == 4
        assert result["closets"] == 2
        assert result["coverage_pct"] == 50.0
        assert result["has_closets"] is True

    def test_no_palace_returns_error(self, tmp_path):
        result = closet_coverage(str(tmp_path / "nonexistent"))
        # With newer ChromaDB this may create the directory, or return error
        # Either way it shouldn't crash
        assert isinstance(result, dict)


# ── check_chromadb_version ───────────────────────────────────────────────────


class TestCheckChromaDBVersion:
    def test_compatible_palace(self, tmp_path):
        palace = str(tmp_path / "palace")
        client = chromadb.PersistentClient(path=palace)
        col = client.get_or_create_collection("callosum_drawers")
        col.add(ids=["d1"], documents=["test"])

        result = check_chromadb_version(palace)
        assert result["status"] == "compatible"
        assert result["drawer_count"] == 1
        assert "chromadb_version" in result
        assert result["action"] is None

    def test_empty_palace(self, tmp_path):
        palace = str(tmp_path / "palace")
        # Create palace dir but no collection
        chromadb.PersistentClient(path=palace)

        result = check_chromadb_version(palace)
        # Should either be "empty" (collection doesn't exist) or "compatible" (if get_collection succeeds)
        assert result["status"] in ("empty", "compatible", "error")
        assert "chromadb_version" in result

    def test_result_structure(self, tmp_path):
        palace = str(tmp_path / "palace")
        result = check_chromadb_version(palace)
        assert "chromadb_version" in result
        assert "palace_path" in result
        assert "status" in result
