"""
maintain.py - Automated maintenance for Callosum.

Single entry point for all housekeeping:
  - Stale index detection + auto re-mine
  - Garbage collection for orphaned drawers
  - Closet coverage reporting
  - Isolation posture check
  - ChromaDB migration safety check
  - Health summary

Designed to run unattended via scheduled task or manually.
"""

import os
import time
from datetime import datetime
from pathlib import Path

import chromadb

from .config import CallosumConfig
from .miner import mine, garbage_collect
from .staleness import check_stale_drawers
from .isolation import isolation_report


# ---------------------------------------------------------------------------
# Stale detection + auto re-mine
# ---------------------------------------------------------------------------


def auto_remediate_stale(
    palace_path: str, workspaces_dir: str = None, dry_run: bool = False
) -> dict:
    """Find stale drawers and automatically re-mine their source files.

    Returns dict with counts of stale files found and re-mined.
    """
    config = CallosumConfig(workspaces_dir)
    dirs = config.build_dynamic_wing_keywords()

    total_stale = 0
    total_remined = 0
    errors = []

    for project_dir_str, wing in dirs.items():
        project_dir = Path(project_dir_str)
        if not project_dir.exists():
            continue

        stale_result = check_stale_drawers(
            palace_path,
            project_dir=str(project_dir),
            wing=wing,
        )
        stale_files = stale_result.get("stale_files", [])
        if not stale_files:
            continue

        total_stale += len(stale_files)
        print(f"  [{wing}] {len(stale_files)} stale files detected")

        if dry_run:
            for sf in stale_files:
                print(f"    [DRY RUN] Would re-mine: {sf.get('source_file', '?')}")
            continue

        # Re-mine the project (mine() handles incremental updates via content_hash)
        try:
            mine(
                project_dir=str(project_dir),
                palace_path=palace_path,
                agent="callosum_auto_remediate",
                dry_run=False,
            )
            total_remined += len(stale_files)
        except Exception as e:
            errors.append({"wing": wing, "error": str(e)})
            print(f"  ! Error re-mining {wing}: {e}")

    return {
        "stale_found": total_stale,
        "remined": total_remined,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Closet coverage report
# ---------------------------------------------------------------------------


def closet_coverage(palace_path: str) -> dict:
    """Report how many drawers have closet coverage.

    Returns stats on callosum_drawers vs callosum_closets.
    """
    try:
        client = chromadb.PersistentClient(path=palace_path)
    except Exception:
        return {"error": "No palace found"}

    drawer_count = 0
    closet_count = 0

    try:
        drawers = client.get_collection("callosum_drawers")
        drawer_count = drawers.count()
    except Exception:
        pass

    try:
        closets = client.get_collection("callosum_closets")
        closet_count = closets.count()
    except Exception:
        pass

    coverage_pct = (closet_count / max(drawer_count, 1)) * 100 if drawer_count > 0 else 0

    return {
        "drawers": drawer_count,
        "closets": closet_count,
        "coverage_pct": round(coverage_pct, 1),
        "has_closets": closet_count > 0,
    }


# ---------------------------------------------------------------------------
# ChromaDB migration check
# ---------------------------------------------------------------------------


def check_chromadb_version(palace_path: str) -> dict:
    """Check if the ChromaDB version matches the palace data format.

    Returns migration status and any recommended actions.
    """
    import chromadb

    current_version = chromadb.__version__

    try:
        client = chromadb.PersistentClient(path=palace_path)
        # Try to access the collection -- if this works, the format is compatible
        col = client.get_collection("callosum_drawers")
        count = col.count()

        return {
            "chromadb_version": current_version,
            "palace_path": palace_path,
            "status": "compatible",
            "drawer_count": count,
            "action": None,
        }
    except Exception as e:
        error_str = str(e).lower()
        if "migration" in error_str or "version" in error_str or "upgrade" in error_str:
            return {
                "chromadb_version": current_version,
                "palace_path": palace_path,
                "status": "migration_needed",
                "error": str(e),
                "action": "Run: callosum migrate",
            }
        elif "does not exist" in error_str or "not found" in error_str:
            return {
                "chromadb_version": current_version,
                "palace_path": palace_path,
                "status": "empty",
                "action": "Run: callosum sweep (or mine a project)",
            }
        return {
            "chromadb_version": current_version,
            "palace_path": palace_path,
            "status": "error",
            "error": str(e),
            "action": "Check ChromaDB installation",
        }


def migrate_chromadb(palace_path: str, backup: bool = True) -> dict:
    """Attempt to migrate ChromaDB data to current version.

    Creates a backup first if backup=True.
    """
    import shutil

    if backup:
        backup_path = f"{palace_path}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists(palace_path):
            shutil.copytree(palace_path, backup_path)
            print(f"  Backup created: {backup_path}")
        else:
            return {"success": False, "error": "No palace directory to migrate"}

    try:
        # ChromaDB handles migration automatically on PersistentClient creation
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_or_create_collection("callosum_drawers")
        count = col.count()

        return {
            "success": True,
            "chromadb_version": chromadb.__version__,
            "drawer_count": count,
            "backup_path": backup_path if backup else None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "backup_path": backup_path if backup else None,
            "hint": "Restore from backup if migration failed",
        }


# ---------------------------------------------------------------------------
# Full health check
# ---------------------------------------------------------------------------


def health_check(palace_path: str, workspaces_dir: str = None) -> dict:
    """Comprehensive health check for the Callosum memory engine.

    Checks:
      1. ChromaDB version compatibility
      2. Drawer + closet counts
      3. Wing isolation posture
      4. Stale index count
      5. Scheduled task status
    """
    config = CallosumConfig(workspaces_dir)
    results = {
        "timestamp": datetime.now().isoformat(),
        "palace_path": palace_path,
    }

    # 1. ChromaDB status
    results["chromadb"] = check_chromadb_version(palace_path)

    # 2. Coverage
    results["coverage"] = closet_coverage(palace_path)

    # 3. Wing breakdown
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
        all_meta = col.get(include=["metadatas"])["metadatas"]
        wings = {}
        for m in all_meta:
            w = m.get("wing", "?")
            wings[w] = wings.get(w, 0) + 1
        results["wings"] = wings
    except Exception:
        results["wings"] = {}

    # 4. Isolation
    results["isolation"] = isolation_report()

    # 5. Stale count
    stale_total = 0
    dirs = config.build_dynamic_wing_keywords()
    for project_dir_str, wing in dirs.items():
        if not Path(project_dir_str).exists():
            continue
        try:
            stale = check_stale_drawers(palace_path, project_dir=project_dir_str, wing=wing)
            stale_total += len(stale.get("stale_files", []))
        except Exception:
            pass
    results["stale_files"] = stale_total

    # 6. Schedule status
    try:
        import subprocess

        result = subprocess.run(
            ["schtasks", "/query", "/tn", "Callosum_AutoSweep", "/fo", "CSV", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            results["schedule"] = {
                "active": True,
                "next_run": parts[1].strip('"') if len(parts) > 1 else "unknown",
                "status": parts[2].strip('"') if len(parts) > 2 else "unknown",
            }
        else:
            results["schedule"] = {"active": False}
    except Exception:
        results["schedule"] = {"active": False}

    # 7. Daemon Status
    try:
        from .watcher import is_daemon_running

        daemon_online = is_daemon_running()

        # Check last log activity
        log_file = Path(palace_path) / "global_watcher.log"
        last_mine = None
        if log_file.exists():
            try:
                # Read backwards to find the last cycle
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    for line in reversed(lines):
                        if (
                            "Watcher cycle complete" in line
                            or "Starting Universal Daemon" in line
                            or "Mining " in line
                        ):
                            last_mine = line[:19]
                            break
            except Exception:
                pass

        results["daemon"] = {"online": daemon_online, "last_activity": last_mine}
    except Exception:
        results["daemon"] = {"online": False, "last_activity": None}

    return results


def print_health(palace_path: str, workspaces_dir: str = None):
    """Pretty-print the health check results."""
    report = health_check(palace_path, workspaces_dir)

    print(f"\n{'=' * 60}")
    print("  Callosum Health Report")
    print(f"  {report['timestamp']}")
    print(f"{'=' * 60}\n")

    # ChromaDB
    cdb = report["chromadb"]
    status_icon = {"compatible": "+", "empty": "~", "migration_needed": "!", "error": "!"}
    icon = status_icon.get(cdb["status"], "?")
    print(f"  [{icon}] ChromaDB: v{cdb['chromadb_version']} ({cdb['status']})")
    if cdb.get("action"):
        print(f"      Action: {cdb['action']}")

    # Coverage
    cov = report["coverage"]
    print(
        f"  [{'+' if cov.get('has_closets') else '~'}] Drawers: {cov.get('drawers', 0)}  |  Closets: {cov.get('closets', 0)}  |  Coverage: {cov.get('coverage_pct', 0)}%"
    )

    # Wings
    wings = report.get("wings", {})
    if wings:
        print(f"\n  Wings ({len(wings)}):")
        for w, count in sorted(wings.items(), key=lambda x: -x[1]):
            print(f"    {w:30} {count:5} drawers")
    else:
        print("\n  [~] No drawers filed yet (run: callosum sweep)")

    # Isolation
    iso = report["isolation"]
    print("\n  Isolation:")
    print(f"    Project wings: {iso['project_wings']}")
    print(f"    Tunnel links:  {iso['tunnel_links']}")
    if iso["isolated_projects"]:
        print(f"    Isolated:      {', '.join(iso['isolated_projects'])}")
    for pair in iso.get("linked_pairs", []):
        print(f"    Linked:        {pair['pair']} ({pair.get('reason', '')})")

    # Staleness
    stale = report.get("stale_files", 0)
    icon = "+" if stale == 0 else "!"
    print(f"\n  [{icon}] Stale files: {stale}")
    if stale > 0:
        print("      Run: callosum maintain --auto-fix")

    # Schedule / Daemon
    sched = report.get("schedule", {})
    daemon = report.get("daemon", {})

    if daemon.get("online"):
        print(
            f"  [+] Universal Daemon: ONLINE (Last activity: {daemon.get('last_activity', 'unknown')})"
        )
    else:
        print("  [-] Universal Daemon: OFFLINE")
        print("      Run: callosum watch --all --daemon")

    if sched.get("active"):
        print(f"  [+] Auto-sweep task:  ACTIVE (next: {sched.get('next_run', '?')})")

    print(f"\n{'=' * 60}\n")


# ---------------------------------------------------------------------------
# Full maintenance cycle
# ---------------------------------------------------------------------------


def full_maintain(
    palace_path: str,
    workspaces_dir: str = None,
    auto_fix: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run a complete maintenance cycle.

    Steps:
      1. Health check
      2. Auto-remediate stale files (if auto_fix)
      3. GC orphaned drawers across all projects
      4. Report closet coverage
      5. Check ChromaDB version

    Designed to run unattended via scheduled task.
    """
    config = CallosumConfig(workspaces_dir)
    start = time.time()

    print(f"\n{'=' * 60}")
    print("  Callosum Maintenance")
    print(f"  {datetime.now().isoformat()}")
    if dry_run:
        print("  DRY RUN -- no changes will be made")
    print(f"{'=' * 60}\n")

    results = {"timestamp": datetime.now().isoformat()}

    # Step 1: ChromaDB check
    print("  [1/4] ChromaDB version check...")
    cdb = check_chromadb_version(palace_path)
    results["chromadb"] = cdb
    if cdb["status"] == "migration_needed":
        print(f"  ! Migration needed: {cdb.get('error', '')}")
        if auto_fix:
            print("  Attempting auto-migration...")
            migrate_result = migrate_chromadb(palace_path, backup=True)
            results["migration"] = migrate_result
            print(f"  Migration: {'success' if migrate_result['success'] else 'FAILED'}")
    elif cdb["status"] == "compatible":
        print(f"  + ChromaDB v{cdb['chromadb_version']} OK")
    elif cdb["status"] == "empty":
        print("  ~ Palace empty (run: callosum sweep)")

    # Step 2: Stale remediation
    print("\n  [2/4] Stale index check...")
    if auto_fix:
        stale_result = auto_remediate_stale(
            palace_path,
            workspaces_dir=workspaces_dir,
            dry_run=dry_run,
        )
        results["stale"] = stale_result
        if stale_result["stale_found"] == 0:
            print("  + No stale files")
        else:
            print(f"  Remediated: {stale_result['remined']} / {stale_result['stale_found']}")
    else:
        # Just count stale
        dirs = config.build_dynamic_wing_keywords()
        stale_total = 0
        for project_dir_str, wing in dirs.items():
            if not Path(project_dir_str).exists():
                continue
            try:
                stale = check_stale_drawers(palace_path, project_dir=project_dir_str, wing=wing)
                stale_total += len(stale.get("stale_files", []))
            except Exception:
                pass
        results["stale"] = {"stale_found": stale_total}
        if stale_total > 0:
            print(f"  ! {stale_total} stale files (run with --auto-fix to remediate)")
        else:
            print("  + No stale files")

    # Step 3: GC across all projects
    print("\n  [3/4] Garbage collection...")
    dirs = config.build_dynamic_wing_keywords()
    gc_total = {"files_removed": 0, "drawers_removed": 0}
    for project_dir_str, wing in dirs.items():
        if not Path(project_dir_str).exists():
            continue
        try:
            gc_result = garbage_collect(
                palace_path=palace_path,
                project_dir=project_dir_str,
                wing=wing,
                dry_run=dry_run,
            )
            gc_total["files_removed"] += gc_result.get("files_removed", 0)
            gc_total["drawers_removed"] += gc_result.get("drawers_removed", 0)
        except Exception as e:
            print(f"  ! GC error for {wing}: {e}")
    results["gc"] = gc_total
    if gc_total["files_removed"] == 0:
        print("  + No orphaned drawers")
    else:
        print(
            f"  Purged: {gc_total['drawers_removed']} drawers from {gc_total['files_removed']} missing files"
        )

    # Step 4: Coverage report
    print("\n  [4/4] Closet coverage...")
    cov = closet_coverage(palace_path)
    results["coverage"] = cov
    print(
        f"  Drawers: {cov.get('drawers', 0)}  |  Closets: {cov.get('closets', 0)}  |  Coverage: {cov.get('coverage_pct', 0)}%"
    )

    elapsed = round(time.time() - start, 1)
    results["elapsed_seconds"] = elapsed

    print(f"\n{'=' * 60}")
    print(f"  Maintenance complete in {elapsed}s")
    print(f"{'=' * 60}\n")

    return results
