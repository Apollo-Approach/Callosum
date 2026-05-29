"""
test_isolation.py — Tests for wing isolation guard.

Covers: is_linked (same wing, unlinked, global wings), link_wings (create,
symmetry, self-link), unlink_wings, filter_allowed_wings, isolation_report.

Each test monkeypatches _WING_CONFIG_PATH to a temp file so tests are
fully isolated and never touch the real wing_config.json.
"""

import json
import os
from pathlib import Path

import pytest

import callosum.isolation as isolation


@pytest.fixture(autouse=True)
def _isolate_config(tmp_dir, monkeypatch):
    """Redirect wing_config.json to a temp directory for every test."""
    config_path = Path(os.path.join(tmp_dir, "wing_config.json"))
    monkeypatch.setattr(isolation, "_WING_CONFIG_PATH", config_path)
    yield


class TestIsLinked:
    def test_same_wing_returns_true(self):
        assert isolation.is_linked("wing_project", "wing_project") is True

    def test_unlinked_wings_returns_false(self):
        assert isolation.is_linked("wing_alpha", "wing_beta") is False

    def test_global_wing_general_always_linked(self):
        assert isolation.is_linked("wing_general", "wing_alpha") is True
        assert isolation.is_linked("wing_alpha", "wing_general") is True

    def test_global_wing_personal_always_linked(self):
        assert isolation.is_linked("wing_personal", "wing_secret_project") is True
        assert isolation.is_linked("wing_secret_project", "wing_personal") is True

    def test_global_wing_infra_always_linked(self):
        assert isolation.is_linked("wing_infra", "wing_anything") is True

    def test_linked_after_explicit_link(self):
        isolation.link_wings("wing_alpha", "wing_beta", reason="collaboration")
        assert isolation.is_linked("wing_alpha", "wing_beta") is True


class TestLinkWings:
    def test_link_wings_creates_link(self):
        result = isolation.link_wings("wing_project_a", "wing_apollo", reason="shared context")
        assert result["success"] is True
        assert "link" in result
        assert result["total_links"] == 1

    def test_link_wings_is_symmetric(self):
        isolation.link_wings("wing_a", "wing_b")
        # A→B and B→A should both be True
        assert isolation.is_linked("wing_a", "wing_b") is True
        assert isolation.is_linked("wing_b", "wing_a") is True

    def test_link_wings_self_link_rejected(self):
        result = isolation.link_wings("wing_same", "wing_same")
        assert result["success"] is False
        assert "itself" in result["reason"].lower()

    def test_link_wings_duplicate_rejected(self):
        isolation.link_wings("wing_x", "wing_y")
        result = isolation.link_wings("wing_x", "wing_y")
        assert result["success"] is False
        assert "already" in result["reason"].lower()

    def test_link_wings_duplicate_reversed_order_rejected(self):
        isolation.link_wings("wing_x", "wing_y")
        result = isolation.link_wings("wing_y", "wing_x")
        assert result["success"] is False

    def test_link_wings_multiple_pairs(self):
        isolation.link_wings("wing_a", "wing_b")
        result = isolation.link_wings("wing_a", "wing_c")
        assert result["success"] is True
        assert result["total_links"] == 2

    def test_link_wings_persists_to_config(self, tmp_dir):
        isolation.link_wings("wing_p", "wing_q", reason="test persistence")
        config_path = Path(os.path.join(tmp_dir, "wing_config.json"))
        with open(config_path, "r") as f:
            config = json.load(f)
        assert len(config["tunnel_links"]) == 1
        link = config["tunnel_links"][0]
        assert link["reason"] == "test persistence"


class TestUnlinkWings:
    def test_unlink_wings_removes_link(self):
        isolation.link_wings("wing_a", "wing_b")
        assert isolation.is_linked("wing_a", "wing_b") is True

        result = isolation.unlink_wings("wing_a", "wing_b")
        assert result["success"] is True
        assert isolation.is_linked("wing_a", "wing_b") is False

    def test_unlink_wings_reversed_order(self):
        isolation.link_wings("wing_a", "wing_b")
        result = isolation.unlink_wings("wing_b", "wing_a")
        assert result["success"] is True
        assert isolation.is_linked("wing_a", "wing_b") is False

    def test_unlink_nonexistent_returns_false(self):
        result = isolation.unlink_wings("wing_x", "wing_y")
        assert result["success"] is False
        assert "no link" in result["reason"].lower()

    def test_unlink_preserves_other_links(self):
        isolation.link_wings("wing_a", "wing_b")
        isolation.link_wings("wing_a", "wing_c")
        isolation.unlink_wings("wing_a", "wing_b")

        assert isolation.is_linked("wing_a", "wing_b") is False
        assert isolation.is_linked("wing_a", "wing_c") is True


class TestFilterAllowedWings:
    def test_filter_returns_only_linked_wings(self):
        isolation.link_wings("wing_src", "wing_allowed")
        candidates = ["wing_src", "wing_allowed", "wing_blocked"]
        allowed = isolation.filter_allowed_wings("wing_src", candidates)

        assert "wing_src" in allowed  # same wing → always allowed
        assert "wing_allowed" in allowed
        assert "wing_blocked" not in allowed

    def test_filter_global_wings_always_pass(self):
        candidates = ["wing_general", "wing_personal", "wing_infra", "wing_blocked"]
        allowed = isolation.filter_allowed_wings("wing_project", candidates)
        assert "wing_general" in allowed
        assert "wing_personal" in allowed
        assert "wing_infra" in allowed
        assert "wing_blocked" not in allowed

    def test_filter_empty_candidates(self):
        allowed = isolation.filter_allowed_wings("wing_src", [])
        assert allowed == []

    def test_filter_all_blocked(self):
        candidates = ["wing_x", "wing_y", "wing_z"]
        allowed = isolation.filter_allowed_wings("wing_src", candidates)
        assert allowed == []


class TestIsolationReport:
    def test_report_structure(self):
        report = isolation.isolation_report()
        expected_keys = {
            "total_wings",
            "project_wings",
            "tunnel_links",
            "isolated_projects",
            "linked_pairs",
            "global_wings",
        }
        assert set(report.keys()) == expected_keys
        assert isinstance(report["isolated_projects"], list)
        assert isinstance(report["linked_pairs"], list)
        assert isinstance(report["global_wings"], list)

    def test_report_global_wings_list(self):
        report = isolation.isolation_report()
        assert "wing_general" in report["global_wings"]
        assert "wing_personal" in report["global_wings"]
        assert "wing_infra" in report["global_wings"]

    def test_report_reflects_links(self):
        isolation.link_wings("wing_a", "wing_b", reason="test")
        report = isolation.isolation_report()
        assert report["tunnel_links"] == 1
        assert len(report["linked_pairs"]) == 1
        assert "wing_a" in report["linked_pairs"][0]["pair"]
        assert "wing_b" in report["linked_pairs"][0]["pair"]

    def test_report_empty_state(self):
        report = isolation.isolation_report()
        assert report["total_wings"] == 0
        assert report["project_wings"] == 0
        assert report["tunnel_links"] == 0
        assert report["isolated_projects"] == []
        assert report["linked_pairs"] == []

    def test_report_with_wing_config(self, tmp_dir):
        """Pre-seed a wing_config.json with wing metadata."""
        config_path = Path(os.path.join(tmp_dir, "wing_config.json"))
        config = {
            "default_wing": "wing_general",
            "wings": {
                "wing_alpha": {"type": "project"},
                "wing_beta": {"type": "project"},
                "wing_notes": {"type": "personal"},
            },
            "tunnel_links": [],
        }
        with open(config_path, "w") as f:
            json.dump(config, f)

        report = isolation.isolation_report()
        assert report["total_wings"] == 3
        assert report["project_wings"] == 2
        assert set(report["isolated_projects"]) == {"wing_alpha", "wing_beta"}
