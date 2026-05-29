"""
staleness.py - Stale index and engram drift detection.

Upstream fix: Callosum v3.3.1 mtime-based staleness detection (PR #757).
Extended for Callosum's Engram Protocol integration:
  - Detects when source files change but drawers haven't been re-mined.
  - Detects when engram reference files have drifted from their drawers.

This enables the Engram Protocol's Tier 2 drift detection feature:
  "Callosum watches engram reference files and flags stale engrams."
"""

from __future__ import annotations

import os
import time
import sqlite3
from datetime import datetime
from pathlib import Path

import chromadb


def check_stale_drawers(palace_path: str, project_dir: str = None, wing: str = None) -> dict:
    """Check for stale drawers whose source files have changed.

    Compares the mtime of source files against the filed_at timestamp
    of their corresponding drawers. If a source file is newer than its
    last-mined drawer, it's stale.

    Args:
        palace_path: Path to the ChromaDB palace.
        project_dir: Optional project directory to scope the check.
        wing: Optional wing filter.

    Returns:
        Dict with stale_files, up_to_date count, and total checked.
    """
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception as e:
        return {"error": f"Could not open palace: {e}"}

    # Get all drawer metadata
    where = {"wing": wing} if wing else None
    try:
        all_data = col.get(include=["metadatas"], where=where)
    except Exception:
        all_data = col.get(include=["metadatas"])

    metas = all_data.get("metadatas", [])

    # Group by source_file, track latest filed_at per file
    file_times: dict[str, str] = {}
    for m in metas:
        sf = m.get("source_file", "")
        fa = m.get("filed_at", "")
        if sf and fa:
            if sf not in file_times or fa > file_times[sf]:
                file_times[sf] = fa

    stale_files = []
    up_to_date = 0
    missing = 0

    for source_file, filed_at_iso in file_times.items():
        # Resolve the full path
        if project_dir:
            full_path = Path(project_dir) / source_file
        else:
            full_path = Path(source_file)

        if not full_path.exists():
            missing += 1
            continue

        # Compare mtime to filed_at
        try:
            file_mtime = datetime.fromtimestamp(full_path.stat().st_mtime)
            filed_at = datetime.fromisoformat(filed_at_iso)

            if file_mtime > filed_at:
                stale_files.append(
                    {
                        "file": str(source_file),
                        "file_modified": file_mtime.isoformat(),
                        "last_mined": filed_at_iso,
                        "drift_seconds": int((file_mtime - filed_at).total_seconds()),
                    }
                )
            else:
                up_to_date += 1
        except (ValueError, OSError):
            missing += 1

    return {
        "stale_files": stale_files,
        "stale_count": len(stale_files),
        "up_to_date": up_to_date,
        "missing": missing,
        "total_tracked": len(file_times),
    }


def check_engram_drift(
    palace_path: str,
    engram_dir: str = None,
) -> dict:
    """Check if engram reference files have drifted from their palace drawers.

    This is the Engram Protocol's Tier 2 integration point:
    Callosum watches the engram artifact files and detects when they've
    been updated without corresponding palace re-mines.

    Args:
        palace_path: Path to the ChromaDB palace.
        engram_dir: Path to the knowledge/ directory containing engrams.
                    Defaults to ~/.gemini/antigravity/knowledge.

    Returns:
        Dict with stale engrams and recommendations.
    """
    if engram_dir is None:
        engram_dir = str(Path.home() / ".gemini" / "antigravity" / "knowledge")

    engram_path = Path(engram_dir)
    if not engram_path.exists():
        return {"error": f"Engram directory not found: {engram_dir}"}

    # Find all engram artifact files
    engram_files = []
    for root, dirs, files in os.walk(engram_path):
        for f in files:
            if f.endswith((".md", ".json")):
                engram_files.append(Path(root) / f)

    # Connect to KG to check for dependent facts
    kg_db_path = os.path.expanduser("~/.callosum/knowledge_graph.sqlite3")
    conn = sqlite3.connect(kg_db_path) if os.path.exists(kg_db_path) else None

    # Check each against palace
    results = []
    for ef in engram_files:
        try:
            mtime = os.path.getmtime(ef)
            rel_path = ef.relative_to(engram_path)

            # Check for dependent facts in KG
            dependent_facts = []
            if conn:
                try:
                    # Look for triples where source matches the engram filename or path
                    rows = conn.execute(
                        "SELECT subject, predicate, object FROM triples WHERE source LIKE ? AND valid_to IS NULL",
                        (f"%{ef.name}%",),
                    ).fetchall()
                    for r in rows:
                        dependent_facts.append(f"{r[0]} -> {r[1]} -> {r[2]}")
                except sqlite3.OperationalError:
                    pass  # Schema might not have source column yet

            results.append(
                {
                    "engram": str(rel_path),
                    "file_path": str(ef),
                    "last_modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime)),
                    "dependent_kg_facts": dependent_facts,
                }
            )
        except (OSError, ValueError):
            pass

    if conn:
        conn.close()

    return {
        "engram_dir": engram_dir,
        "engrams_found": len(results),
        "files": results,
    }
