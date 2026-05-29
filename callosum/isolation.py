"""
isolation.py - Wing isolation guard for Callosum.

Enforces project isolation by gatekeeping cross-wing operations.
Wings are isolated by default. Cross-wing tunnels require explicit
opt-in via the tunnel_links allowlist in wing_config.json.

Design principle: data from Project A should never pollute Project B's
memory space. If you want shared context between two projects, you
must explicitly link them.

Examples:
    # Allow project_a and project_b to share context
    link_wings("wing_project_a", "wing_project_b")

    # Check if traversal between two wings is allowed
    is_linked("wing_project_a", "wing_project_c")  # False by default

    # Revoke a link
    unlink_wings("wing_project_a", "wing_project_b")
"""

from __future__ import annotations

import json
import os
from pathlib import Path


_WING_CONFIG_PATH = Path(os.path.expanduser("~/.callosum/wing_config.json"))


def _load_config() -> dict:
    """Load wing_config.json."""
    if not _WING_CONFIG_PATH.exists():
        return {"default_wing": "wing_general", "wings": {}, "tunnel_links": []}
    try:
        with open(_WING_CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        # Ensure tunnel_links exists
        if "tunnel_links" not in config:
            config["tunnel_links"] = []
        return config
    except Exception:
        return {"default_wing": "wing_general", "wings": {}, "tunnel_links": []}


def _save_config(config: dict) -> None:
    """Write wing_config.json."""
    _WING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_WING_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _normalize_pair(wing_a: str, wing_b: str) -> tuple[str, str]:
    """Sort a wing pair for consistent storage."""
    return tuple(sorted([wing_a, wing_b]))


def get_tunnel_links() -> list[dict]:
    """Return all active tunnel links."""
    config = _load_config()
    return config.get("tunnel_links", [])


def is_linked(wing_a: str, wing_b: str) -> bool:
    """Check if two wings have an explicit opt-in tunnel link.

    Returns True if:
      - The wings are the same (intra-wing is always allowed)
      - A tunnel_link entry exists for this pair
      - Either wing is wing_general or wing_personal (personal/infra always bridges)
    """
    if wing_a == wing_b:
        return True

    # Personal and infrastructure wings are always bridgeable
    _GLOBAL_WINGS = {"wing_general", "wing_personal", "wing_infra"}
    if wing_a in _GLOBAL_WINGS or wing_b in _GLOBAL_WINGS:
        return True

    a, b = _normalize_pair(wing_a, wing_b)
    links = get_tunnel_links()
    for link in links:
        la, lb = _normalize_pair(link.get("wing_a", ""), link.get("wing_b", ""))
        if la == a and lb == b:
            return True
    return False


def link_wings(wing_a: str, wing_b: str, reason: str = "") -> dict:
    """Create an explicit opt-in tunnel link between two wings.

    This allows traverse, find_tunnels, and cross-wing search
    to cross the boundary between these two projects.
    """
    if wing_a == wing_b:
        return {"success": False, "reason": "Cannot link a wing to itself"}

    if is_linked(wing_a, wing_b):
        return {"success": False, "reason": f"Already linked: {wing_a} <-> {wing_b}"}

    config = _load_config()
    a, b = _normalize_pair(wing_a, wing_b)

    from datetime import datetime

    config["tunnel_links"].append(
        {
            "wing_a": a,
            "wing_b": b,
            "reason": reason,
            "created_at": datetime.now().isoformat(),
        }
    )
    _save_config(config)

    return {
        "success": True,
        "link": f"{a} <-> {b}",
        "reason": reason,
        "total_links": len(config["tunnel_links"]),
    }


def unlink_wings(wing_a: str, wing_b: str) -> dict:
    """Revoke a tunnel link between two wings."""
    a, b = _normalize_pair(wing_a, wing_b)
    config = _load_config()

    original_count = len(config.get("tunnel_links", []))
    config["tunnel_links"] = [
        link
        for link in config.get("tunnel_links", [])
        if _normalize_pair(link.get("wing_a", ""), link.get("wing_b", "")) != (a, b)
    ]
    _save_config(config)

    removed = original_count - len(config["tunnel_links"])
    if removed == 0:
        return {"success": False, "reason": f"No link found: {a} <-> {b}"}

    return {
        "success": True,
        "unlinked": f"{a} <-> {b}",
        "remaining_links": len(config["tunnel_links"]),
    }


def filter_allowed_wings(source_wing: str, candidate_wings: list[str]) -> list[str]:
    """Filter a list of wings to only those linked to source_wing.

    Used by traverse() and find_tunnels() to enforce isolation.
    """
    return [w for w in candidate_wings if is_linked(source_wing, w)]


def isolation_report() -> dict:
    """Summary of the current isolation posture."""
    config = _load_config()
    wings = list(config.get("wings", {}).keys())
    links = config.get("tunnel_links", [])

    # Count isolated wings (no explicit links)
    linked_wings = set()
    for link in links:
        linked_wings.add(link.get("wing_a", ""))
        linked_wings.add(link.get("wing_b", ""))

    project_wings = [w for w in wings if config["wings"].get(w, {}).get("type") == "project"]
    isolated_projects = [w for w in project_wings if w not in linked_wings]

    return {
        "total_wings": len(wings),
        "project_wings": len(project_wings),
        "tunnel_links": len(links),
        "isolated_projects": isolated_projects,
        "linked_pairs": [
            {"pair": f"{link['wing_a']} <-> {link['wing_b']}", "reason": link.get("reason", "")}
            for link in links
        ],
        "global_wings": ["wing_general", "wing_personal", "wing_infra"],
    }
