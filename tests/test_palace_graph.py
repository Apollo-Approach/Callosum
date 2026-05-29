"""Tests for callosum.palace_graph — graph traversal, tunnels, fuzzy matching."""

from unittest.mock import MagicMock, patch

from callosum.palace_graph import build_graph, traverse, find_tunnels, graph_stats, _fuzzy_match


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_collection(drawers):
    """Build a mock ChromaDB collection from a list of metadata dicts."""
    col = MagicMock()
    col.count.return_value = len(drawers)

    def mock_get(limit=1000, offset=0, include=None, **kwargs):
        batch = drawers[offset : offset + limit]
        return {"ids": [f"d{i}" for i in range(offset, offset + len(batch))], "metadatas": batch}

    col.get = mock_get
    return col


# ── build_graph ──────────────────────────────────────────────────────────────


class TestBuildGraph:
    def test_empty_collection(self):
        col = _make_collection([])
        nodes, edges = build_graph(col=col)
        assert nodes == {}
        assert edges == []

    def test_nodes_from_metadata(self):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": "2024-01-01"},
                {"room": "auth", "wing": "webapp", "hall": "", "date": "2024-01-02"},
                {"room": "db-setup", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        nodes, edges = build_graph(col=col)
        assert "auth" in nodes
        assert "db-setup" in nodes
        assert nodes["auth"]["count"] == 2
        assert nodes["db-setup"]["count"] == 1

    def test_general_room_excluded(self):
        col = _make_collection(
            [
                {"room": "general", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        nodes, _ = build_graph(col=col)
        assert "general" not in nodes

    def test_edges_from_multi_wing_rooms(self):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "security", "date": ""},
                {"room": "auth", "wing": "mobile", "hall": "security", "date": ""},
            ]
        )
        nodes, edges = build_graph(col=col)
        assert len(edges) == 1
        assert edges[0]["room"] == "auth"
        assert set([edges[0]["wing_a"], edges[0]["wing_b"]]) == {"mobile", "webapp"}

    def test_no_edges_for_single_wing_rooms(self):
        col = _make_collection(
            [
                {"room": "backend", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        _, edges = build_graph(col=col)
        assert edges == []

    def test_none_collection_returns_empty(self):
        nodes, edges = build_graph(col=None, config=None)
        assert nodes == {}
        assert edges == []


# ── _fuzzy_match ─────────────────────────────────────────────────────────────


class TestFuzzyMatch:
    def test_exact_substring(self):
        nodes = {"auth-module": {}, "db-setup": {}, "frontend-views": {}}
        result = _fuzzy_match("auth", nodes)
        assert "auth-module" in result

    def test_partial_word_match(self):
        nodes = {"auth-module": {}, "db-setup": {}, "frontend-views": {}}
        result = _fuzzy_match("db-setup", nodes)
        assert "db-setup" in result

    def test_no_match(self):
        nodes = {"auth-module": {}, "db-setup": {}}
        result = _fuzzy_match("zzzznotreal", nodes)
        assert result == []

    def test_limit(self):
        nodes = {f"room-{i}": {} for i in range(20)}
        result = _fuzzy_match("room", nodes, n=3)
        assert len(result) <= 3


# ── traverse ─────────────────────────────────────────────────────────────────


class TestTraverse:
    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_unknown_room_returns_suggestions(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        result = traverse("nonexistent", col=col)
        assert isinstance(result, dict)
        assert "error" in result
        assert "suggestions" in result

    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_start_room_at_hop_zero(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        result = traverse("auth", col=col)
        assert isinstance(result, list)
        assert result[0]["room"] == "auth"
        assert result[0]["hop"] == 0

    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_respects_max_hops(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
                {"room": "db", "wing": "webapp", "hall": "", "date": ""},
                {"room": "cache", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        result = traverse("auth", col=col, max_hops=1)
        hops = [r["hop"] for r in result]
        assert max(hops) <= 1

    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_finds_connected_rooms_via_shared_wing(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
                {"room": "db", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        result = traverse("auth", col=col, max_hops=2)
        rooms = [r["room"] for r in result]
        assert "auth" in rooms
        assert "db" in rooms

    @patch("callosum.palace_graph.is_linked", return_value=False)
    def test_isolation_blocks_unlinked_wings(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
                {"room": "secret", "wing": "classified", "hall": "", "date": ""},
            ]
        )
        result = traverse("auth", col=col, max_hops=2)
        rooms = [r["room"] for r in result]
        # "secret" is in a different wing and is_linked returns False
        assert "secret" not in rooms


# ── find_tunnels ─────────────────────────────────────────────────────────────


class TestFindTunnels:
    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_finds_multi_wing_rooms(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
                {"room": "auth", "wing": "mobile", "hall": "", "date": ""},
                {"room": "backend", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        tunnels = find_tunnels(col=col)
        tunnel_rooms = [t["room"] for t in tunnels]
        assert "auth" in tunnel_rooms
        assert "backend" not in tunnel_rooms  # single wing

    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_filters_by_wing(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "", "date": ""},
                {"room": "auth", "wing": "mobile", "hall": "", "date": ""},
            ]
        )
        tunnels = find_tunnels(wing_a="webapp", col=col)
        assert len(tunnels) == 1
        assert "webapp" in tunnels[0]["wings"]

    @patch("callosum.palace_graph.is_linked", return_value=False)
    def test_unlinked_wings_returns_error(self, mock_linked):
        result = find_tunnels(wing_a="a", wing_b="b", col=_make_collection([]))
        assert "error" in result


# ── graph_stats ──────────────────────────────────────────────────────────────


class TestGraphStats:
    @patch("callosum.palace_graph.is_linked", return_value=True)
    def test_stats_structure(self, mock_linked):
        col = _make_collection(
            [
                {"room": "auth", "wing": "webapp", "hall": "sec", "date": ""},
                {"room": "auth", "wing": "mobile", "hall": "sec", "date": ""},
                {"room": "backend", "wing": "webapp", "hall": "", "date": ""},
            ]
        )
        stats = graph_stats(col=col)
        assert "total_rooms" in stats
        assert "tunnel_rooms" in stats
        assert "total_edges" in stats
        assert "rooms_per_wing" in stats
        assert "top_tunnels" in stats
        assert stats["total_rooms"] == 2
        assert stats["tunnel_rooms"] == 1  # only "auth" spans 2 wings
