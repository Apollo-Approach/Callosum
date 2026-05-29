"""
test_blueprints.py — Tests for architectural blueprints storage.

Covers: save_blueprint (create & upsert), load_blueprint (found & not found),
list_blueprints (all & filtered by wing).
"""

import os
import time

from callosum.blueprints import Blueprints


def _make_blueprints(tmp_dir):
    """Create a Blueprints store backed by a temp SQLite file."""
    db_path = os.path.join(tmp_dir, "test_blueprints.sqlite3")
    return Blueprints(db_path=db_path)


class TestSaveBlueprint:
    def test_save_blueprint_creates_record(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp_id = bp.save_blueprint("project", "auth_flow", "JWT -> Refresh -> Rotate")
        assert bp_id == "bp_project_auth_flow"

        loaded = bp.load_blueprint("project", "auth_flow")
        assert loaded is not None
        assert loaded["content"] == "JWT -> Refresh -> Rotate"

    def test_save_blueprint_returns_deterministic_id(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        id1 = bp.save_blueprint("infra", "network", "VPC layout v1")
        id2 = bp.save_blueprint("infra", "network", "VPC layout v2")
        # Same wing+name → same ID (upsert)
        assert id1 == id2 == "bp_infra_network"

    def test_save_blueprint_upsert_overwrites_content(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "db_schema", "v1: users table")
        bp.save_blueprint("project", "db_schema", "v2: users + roles tables")

        loaded = bp.load_blueprint("project", "db_schema")
        assert loaded["content"] == "v2: users + roles tables"

    def test_save_blueprint_upsert_updates_timestamp(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "api_spec", "OpenAPI v1")
        loaded_v1 = bp.load_blueprint("project", "api_spec")

        time.sleep(0.05)
        bp.save_blueprint("project", "api_spec", "OpenAPI v2")
        loaded_v2 = bp.load_blueprint("project", "api_spec")

        assert loaded_v2["updated_at"] >= loaded_v1["updated_at"]

    def test_save_different_wings_same_name(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project_a", "overview", "Project A arch")
        bp.save_blueprint("project_b", "overview", "Project B arch")

        a = bp.load_blueprint("project_a", "overview")
        b = bp.load_blueprint("project_b", "overview")
        assert a["content"] == "Project A arch"
        assert b["content"] == "Project B arch"


class TestLoadBlueprint:
    def test_load_blueprint_returns_content(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("infra", "dns", "Route53 config details")
        loaded = bp.load_blueprint("infra", "dns")

        assert loaded is not None
        assert loaded["id"] == "bp_infra_dns"
        assert loaded["wing"] == "infra"
        assert loaded["name"] == "dns"
        assert loaded["content"] == "Route53 config details"
        assert "created_at" in loaded
        assert "updated_at" in loaded

    def test_load_blueprint_not_found_returns_none(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        result = bp.load_blueprint("nonexistent", "phantom")
        assert result is None

    def test_load_blueprint_wrong_wing_returns_none(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "auth", "JWT flow")
        result = bp.load_blueprint("notes", "auth")
        assert result is None


class TestListBlueprints:
    def test_list_blueprints_returns_all(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "auth", "JWT flow")
        bp.save_blueprint("infra", "dns", "Route53")
        bp.save_blueprint("notes", "ideas", "Brainstorm notes")

        items = bp.list_blueprints()
        assert len(items) == 3

    def test_list_blueprints_filtered_by_wing(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "auth", "JWT flow")
        bp.save_blueprint("project", "db", "Schema v1")
        bp.save_blueprint("infra", "dns", "Route53")

        project_items = bp.list_blueprints(wing="project")
        assert len(project_items) == 2
        assert all(i["wing"] == "project" for i in project_items)

        infra_items = bp.list_blueprints(wing="infra")
        assert len(infra_items) == 1
        assert infra_items[0]["name"] == "dns"

    def test_list_blueprints_empty(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        items = bp.list_blueprints()
        assert items == []

    def test_list_blueprints_no_content_field(self, tmp_dir):
        """list_blueprints returns metadata only, not content."""
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "auth", "Secret JWT flow details")
        items = bp.list_blueprints()
        assert len(items) == 1
        assert "content" not in items[0]
        expected_keys = {"id", "wing", "name", "created_at", "updated_at"}
        assert set(items[0].keys()) == expected_keys

    def test_list_blueprints_ordered_by_updated_at(self, tmp_dir):
        bp = _make_blueprints(tmp_dir)
        bp.save_blueprint("project", "first", "content 1")
        time.sleep(0.05)
        bp.save_blueprint("project", "second", "content 2")
        time.sleep(0.05)
        bp.save_blueprint("project", "third", "content 3")

        items = bp.list_blueprints()
        # ORDER BY updated_at DESC
        assert items[0]["name"] == "third"
        assert items[1]["name"] == "second"
        assert items[2]["name"] == "first"
