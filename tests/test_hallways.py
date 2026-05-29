"""Tests for callosum.hallways — within-wing entity-to-entity connectors."""

import pytest
from unittest.mock import MagicMock

import callosum.hallways as hallways_mod
from callosum.hallways import (
    _parse_entities,
    _hallway_id,
    compute_hallways_for_wing,
    list_hallways,
    delete_hallway,
)


@pytest.fixture(autouse=True)
def _isolate_hallway_file(tmp_path, monkeypatch):
    """Redirect _HALLWAY_FILE to a temp path so tests never touch real data."""
    monkeypatch.setattr(hallways_mod, "_HALLWAY_FILE", str(tmp_path / "hallways.json"))


# ── _parse_entities ──────────────────────────────────────────────────────────


class TestParseEntities:
    def test_semicolon_string(self):
        assert _parse_entities("Aya;Lumi;Devon") == ["Aya", "Lumi", "Devon"]

    def test_list_input(self):
        assert _parse_entities(["Aya", "Lumi"]) == ["Aya", "Lumi"]

    def test_deduplication(self):
        assert _parse_entities("Aya;Lumi;Aya") == ["Aya", "Lumi"]

    def test_empty_string(self):
        assert _parse_entities("") == []

    def test_none(self):
        assert _parse_entities(None) == []

    def test_whitespace_stripping(self):
        assert _parse_entities("  Aya ; Lumi  ;  Devon  ") == ["Aya", "Lumi", "Devon"]

    def test_empty_segments_filtered(self):
        assert _parse_entities("Aya;;Lumi;;;") == ["Aya", "Lumi"]

    def test_non_string_non_list(self):
        assert _parse_entities(42) == []


# ── _hallway_id ──────────────────────────────────────────────────────────────


class TestHallwayId:
    def test_symmetric(self):
        """(Aya, Lumi) and (Lumi, Aya) produce the same ID."""
        assert _hallway_id("diary", "Aya", "Lumi") == _hallway_id("diary", "Lumi", "Aya")

    def test_deterministic(self):
        id1 = _hallway_id("diary", "Aya", "Lumi")
        id2 = _hallway_id("diary", "Aya", "Lumi")
        assert id1 == id2

    def test_different_wings_different_ids(self):
        assert _hallway_id("diary", "Aya", "Lumi") != _hallway_id("work", "Aya", "Lumi")

    def test_contains_entities(self):
        hid = _hallway_id("diary", "Aya", "Lumi")
        assert "Aya" in hid and "Lumi" in hid


# ── compute_hallways_for_wing ────────────────────────────────────────────────


def _make_mock_collection(metadatas):
    """Build a MagicMock ChromaDB collection returning the given metadatas."""
    col = MagicMock()
    col.get.return_value = {"metadatas": metadatas}
    return col


class TestComputeHallways:
    def test_no_collection_returns_empty(self):
        assert compute_hallways_for_wing("diary", col=None) == []

    def test_basic_co_occurrence(self):
        metas = [
            {"wing": "diary", "entities": "Aya;Lumi", "room": "letters"},
            {"wing": "diary", "entities": "Aya;Lumi", "room": "ideas"},
        ]
        col = _make_mock_collection(metas)
        result = compute_hallways_for_wing("diary", col=col, min_count=2)
        assert len(result) == 1
        assert result[0]["entity_a"] == "Aya"
        assert result[0]["entity_b"] == "Lumi"
        assert result[0]["co_occurrence_count"] == 2
        assert set(result[0]["rooms"]) == {"letters", "ideas"}

    def test_min_count_filtering(self):
        metas = [
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r1"},
        ]
        col = _make_mock_collection(metas)
        # Only 1 co-occurrence, min_count=2 should filter it out
        assert compute_hallways_for_wing("diary", col=col, min_count=2) == []
        # min_count=1 should include it
        result = compute_hallways_for_wing("diary", col=col, min_count=1)
        assert len(result) == 1

    def test_single_entity_skipped(self):
        metas = [{"wing": "diary", "entities": "Aya", "room": "r1"}]
        col = _make_mock_collection(metas)
        assert compute_hallways_for_wing("diary", col=col, min_count=1) == []

    def test_sentinel_drawers_skipped(self):
        metas = [
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r1", "is_sentinel": True},
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r2", "is_sentinel": True},
        ]
        col = _make_mock_collection(metas)
        assert compute_hallways_for_wing("diary", col=col, min_count=1) == []

    def test_dynamics_fields_initialized(self):
        metas = [
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r1"},
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r2"},
        ]
        col = _make_mock_collection(metas)
        result = compute_hallways_for_wing("diary", col=col, min_count=2)
        h = result[0]
        assert "strength" in h
        assert "stability" in h
        assert "last_activated" in h
        assert "access_count" in h

    def test_preserves_other_wings(self):
        # Pre-seed a hallway for wing "work"
        from callosum.hallways import _save_hallways

        _save_hallways([{"id": "hw_work_1", "wing": "work", "entity_a": "X", "entity_b": "Y"}])

        metas = [
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r1"},
            {"wing": "diary", "entities": "Aya;Lumi", "room": "r2"},
        ]
        col = _make_mock_collection(metas)
        compute_hallways_for_wing("diary", col=col, min_count=2)

        # The "work" hallway should still be there
        all_h = list_hallways()
        wings = {h["wing"] for h in all_h}
        assert "work" in wings
        assert "diary" in wings


# ── list_hallways / delete_hallway ───────────────────────────────────────────


class TestListAndDelete:
    def _seed(self):
        from callosum.hallways import _save_hallways

        records = [
            {"id": "h1", "wing": "diary", "entity_a": "Aya", "entity_b": "Lumi"},
            {"id": "h2", "wing": "diary", "entity_a": "Aya", "entity_b": "Devon"},
            {"id": "h3", "wing": "work", "entity_a": "Alice", "entity_b": "Bob"},
        ]
        _save_hallways(records)

    def test_list_all(self):
        self._seed()
        assert len(list_hallways()) == 3

    def test_list_filtered_by_wing(self):
        self._seed()
        assert len(list_hallways(wing="diary")) == 2
        assert len(list_hallways(wing="work")) == 1

    def test_list_empty(self):
        assert list_hallways() == []

    def test_delete_existing(self):
        self._seed()
        assert delete_hallway("h2") is True
        assert len(list_hallways()) == 2

    def test_delete_nonexistent(self):
        self._seed()
        assert delete_hallway("not_a_real_id") is False
        assert len(list_hallways()) == 3


# ── Persistence roundtrip ────────────────────────────────────────────────────


class TestPersistence:
    def test_save_load_roundtrip(self):
        from callosum.hallways import _save_hallways, _load_hallways

        records = [{"id": "h1", "wing": "diary", "entity_a": "A", "entity_b": "B"}]
        _save_hallways(records)
        loaded = _load_hallways()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "h1"

    def test_corrupt_file_returns_empty(self):
        with open(hallways_mod._HALLWAY_FILE, "w") as f:
            f.write("NOT JSON {{{")
        from callosum.hallways import _load_hallways

        assert _load_hallways() == []
