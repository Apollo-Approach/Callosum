#!/usr/bin/env python3
"""
Callosum -- Give your AI a memory. No API key required.

Two ways to ingest:
  Projects:      Callosum mine ~/projects/my_app          (code, docs, notes)
  Conversations: Callosum mine ~/chats/ --mode convos     (Claude, ChatGPT, Slack)

Same palace. Same search. Different ingest strategies.

Commands:
    Callosum init <dir>                  Detect rooms from folder structure
    Callosum split <dir>                 Split concatenated mega-files into per-session files
    Callosum mine <dir>                  Mine project files (default)
    Callosum mine <dir> --mode convos    Mine conversation exports
    Callosum search "query"              Find anything, exact words
    Callosum wake-up                     Show L0 + L1 wake-up context
    Callosum wake-up --wing my_app       Wake-up for a specific project
    Callosum status                      Show what's been filed

Examples:
    Callosum init ~/projects/my_app
    Callosum mine ~/projects/my_app
    Callosum mine ~/chats/claude-sessions --mode convos
    Callosum search "why did we switch to GraphQL"
    Callosum search "pricing discussion" --wing my_app --room costs
"""

import os
import logging

# Fix ChromaDB posthog telemetry noisy exception log
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)
os.environ["ANONYMIZED_TELEMETRY"] = "False"

import sys  # noqa: E402
import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

from .config import CallosumConfig  # noqa: E402


def cmd_init(args):
    import json
    from pathlib import Path
    from .entity_detector import scan_for_detection, detect_entities, confirm_entities
    from .room_detector_local import detect_rooms_local

    # Pass 1: auto-detect people and projects from file content
    print(f"\n  Scanning for entities in: {args.dir}")
    files = scan_for_detection(args.dir)
    if files:
        print(f"  Reading {len(files)} files...")
        detected = detect_entities(files)
        total = len(detected["people"]) + len(detected["projects"]) + len(detected["uncertain"])
        if total > 0:
            confirmed = confirm_entities(detected, yes=getattr(args, "yes", False))
            # Save confirmed entities to <project>/entities.json for the miner
            if confirmed["people"] or confirmed["projects"]:
                entities_path = Path(args.dir).expanduser().resolve() / "entities.json"
                with open(entities_path, "w") as f:
                    json.dump(confirmed, f, indent=2)
                print(f"  Entities saved: {entities_path}")
        else:
            print("  No entities detected -- proceeding with directory-based rooms.")

    # Pass 2: detect rooms from folder structure
    detect_rooms_local(project_dir=args.dir, yes=getattr(args, "yes", False))
    CallosumConfig().init()


def cmd_mine(args):
    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    include_ignored = []
    for raw in args.include_ignored or []:
        include_ignored.extend(part.strip() for part in raw.split(",") if part.strip())

    if args.mode == "convos":
        from .convo_miner import mine_convos

        mine_convos(
            convo_dir=args.dir,
            palace_path=palace_path,
            wing=args.wing,
            agent=args.agent,
            limit=args.limit,
            dry_run=args.dry_run,
            extract_mode=args.extract,
        )
    elif args.mode == "antigravity":
        from .antigravity_miner import mine_antigravity

        mine_antigravity(
            brain_path=args.dir,
            palace_path=palace_path,
            wing=args.wing,
            agent=args.agent,
            limit=args.limit,
            dry_run=args.dry_run,
            include_versions=getattr(args, "include_versions", False),
            include_steps=True,
        )
    else:
        from .miner import mine

        mine(
            project_dir=args.dir,
            palace_path=palace_path,
            wing_override=args.wing,
            agent=args.agent,
            limit=args.limit,
            dry_run=args.dry_run,
            respect_gitignore=not args.no_gitignore,
            include_ignored=include_ignored,
        )


def cmd_search(args):
    from .searcher import search

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    search(
        query=args.query,
        palace_path=palace_path,
        wing=args.wing,
        room=args.room,
        n_results=args.results,
    )


def cmd_wakeup(args):
    """Show L0 (identity) + L1 (essential story) -- the wake-up context."""
    from .layers import MemoryStack

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    stack = MemoryStack(palace_path=palace_path)

    text = stack.wake_up(wing=args.wing)
    tokens = len(text) // 4
    print(f"Wake-up text (~{tokens} tokens):")
    print("=" * 50)
    print(text)


def cmd_watch(args):
    """Start an ultra-lightweight filesystem watcher that auto-mines changes."""
    from .watcher import start_global_watcher, daemonize, stop_daemon, is_daemon_running

    if args.stop:
        stop_daemon()
        return

    if args.status:
        if is_daemon_running():
            print("  [+] Callosum Universal Daemon is ONLINE and actively polling.")
        else:
            print("  [-] Callosum Universal Daemon is OFFLINE.")
        return

    if args.install_startup:
        from .watcher import install_windows_startup

        install_windows_startup()
        return

    if args.all:
        if args.daemon:
            daemonize()
        else:
            start_global_watcher()
    else:
        print(
            "Legacy single-project watch mode is replaced. Please use 'callosum watch --all' to watch all registered projects."
        )


def cmd_split(args):
    """Split concatenated transcript mega-files into per-session files."""
    from .split_mega_files import main as split_main

    # Rebuild argv for split_mega_files argparse
    argv = ["--source", args.dir]
    if args.output_dir:
        argv += ["--output-dir", args.output_dir]
    if args.dry_run:
        argv.append("--dry-run")
    if args.min_sessions != 2:
        argv += ["--min-sessions", str(args.min_sessions)]

    old_argv = sys.argv
    sys.argv = ["Callosum split"] + argv
    try:
        split_main()
    finally:
        sys.argv = old_argv


def cmd_status(args):
    from .miner import status

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    status(palace_path=palace_path)


def cmd_repair(args):
    """Rebuild palace vector index from SQLite metadata."""
    import chromadb
    import shutil

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path

    if not os.path.isdir(palace_path):
        print(f"\n  No palace found at {palace_path}")
        return

    print(f"\n{'=' * 55}")
    print("  Callosum Repair")
    print(f"{'=' * 55}\n")
    print(f"  Palace: {palace_path}")

    # Try to read existing drawers
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
        total = col.count()
        print(f"  Drawers found: {total}")
    except Exception as e:
        print(f"  Error reading palace: {e}")
        print("  Cannot recover -- palace may need to be re-mined from source files.")
        return

    if total == 0:
        print("  Nothing to repair.")
        return

    # Extract all drawers in batches
    print("\n  Extracting drawers...")
    batch_size = 5000
    all_ids = []
    all_docs = []
    all_metas = []
    offset = 0
    while offset < total:
        batch = col.get(limit=batch_size, offset=offset, include=["documents", "metadatas"])
        all_ids.extend(batch["ids"])
        all_docs.extend(batch["documents"])
        all_metas.extend(batch["metadatas"])
        offset += batch_size
    print(f"  Extracted {len(all_ids)} drawers")

    # Backup and rebuild
    backup_path = palace_path + ".backup"
    if os.path.exists(backup_path):
        shutil.rmtree(backup_path)
    print(f"  Backing up to {backup_path}...")
    shutil.copytree(palace_path, backup_path)

    print("  Rebuilding collection...")
    client.delete_collection("callosum_drawers")
    new_col = client.create_collection("callosum_drawers")

    filed = 0
    for i in range(0, len(all_ids), batch_size):
        batch_ids = all_ids[i : i + batch_size]
        batch_docs = all_docs[i : i + batch_size]
        batch_metas = all_metas[i : i + batch_size]
        new_col.add(documents=batch_docs, ids=batch_ids, metadatas=batch_metas)
        filed += len(batch_ids)
        print(f"  Re-filed {filed}/{len(all_ids)} drawers...")

    print(f"\n  Repair complete. {filed} drawers rebuilt.")
    print(f"  Backup saved at {backup_path}")
    print(f"\n{'=' * 55}\n")


def cmd_gc(args):
    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path

    # Try to detect if it's an antigravity directory or a project directory
    target_path = Path(args.dir).expanduser().resolve()
    if target_path.name == "brain" and "antigravity" in str(target_path).lower():
        from .antigravity_miner import garbage_collect_antigravity

        garbage_collect_antigravity(
            palace_path=palace_path, brain_path=str(target_path), dry_run=args.dry_run
        )
    else:
        from .miner import garbage_collect, load_config

        try:
            config = load_config(str(target_path))
            wing = config["wing"]
            garbage_collect(
                palace_path=palace_path,
                project_dir=str(target_path),
                wing=wing,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"Error running GC: {e}")


def cmd_sweep(args):
    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    config = CallosumConfig()
    workspaces_dir = config.workspaces_dir
    from .scheduler import sweep_all

    sweep_all(palace_path=palace_path, workspaces_dir=str(workspaces_dir))


def cmd_schedule(args):
    from .scheduler import register_schedule

    register_schedule(interval_hours=args.interval)


def cmd_unschedule(args):
    from .scheduler import unregister_schedule

    unregister_schedule()


def cmd_schedules(args):
    from .scheduler import list_schedules

    list_schedules()


def cmd_health(args):
    from .maintain import print_health

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    config = CallosumConfig()
    print_health(palace_path, workspaces_dir=str(config.workspaces_dir))


def cmd_maintain(args):
    from .maintain import full_maintain

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path
    config = CallosumConfig()
    full_maintain(
        palace_path=palace_path,
        workspaces_dir=str(config.workspaces_dir),
        auto_fix=args.auto_fix,
        dry_run=args.dry_run,
    )


def cmd_migrate(args):
    from .maintain import migrate_chromadb, check_chromadb_version

    palace_path = os.path.expanduser(args.palace) if args.palace else CallosumConfig().palace_path

    status = check_chromadb_version(palace_path)
    if status["status"] == "compatible":
        print(f"\n  Palace is compatible with ChromaDB v{status['chromadb_version']}")
        print(f"  {status.get('drawer_count', 0)} drawers -- no migration needed.\n")
        return

    print(f"\n  ChromaDB v{status['chromadb_version']} -- status: {status['status']}")
    if not args.yes:
        print("  This will back up your palace and attempt migration.")
        confirm = input("  Proceed? [y/N] ").strip().lower()
        if confirm != "y":
            print("  Aborted.")
            return

    result = migrate_chromadb(palace_path, backup=not args.no_backup)
    if result["success"]:
        print(f"  Migration successful! {result.get('drawer_count', 0)} drawers.")
        if result.get("backup_path"):
            print(f"  Backup: {result['backup_path']}")
    else:
        print(f"  Migration FAILED: {result.get('error', '?')}")
        if result.get("backup_path"):
            print(f"  Restore from: {result['backup_path']}")
    print()


def cmd_setup(args):
    from .wizard import run_setup_wizard

    run_setup_wizard()


def main():
    from .version import __version__

    parser = argparse.ArgumentParser(
        description="Callosum -- Give your AI a memory. No API key required.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--palace",
        default=None,
        help="Where the palace lives (default: from ~/.callosum/config.json or ~/.callosum/palace)",
    )

    sub = parser.add_subparsers(dest="command")

    # setup
    sub.add_parser("setup", help="Interactive wizard to configure Callosum and smart routing")

    # init
    p_init = sub.add_parser("init", help="Detect rooms from your folder structure")
    p_init.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project directory to set up (default: current directory)",
    )
    p_init.add_argument(
        "--yes", action="store_true", help="Auto-accept all detected entities (non-interactive)"
    )

    # mine
    p_mine = sub.add_parser("mine", help="Mine files into the palace")
    p_mine.add_argument(
        "dir", nargs="?", default=".", help="Directory to mine (default: current directory)"
    )
    p_mine.add_argument(
        "--mode",
        choices=["projects", "convos", "antigravity"],
        default="projects",
        help="Ingest mode: 'projects' for code/docs (default), 'convos' for chat exports, 'antigravity' for Gemini CLI artifacts",
    )
    p_mine.add_argument("--wing", default=None, help="Wing name (default: directory name)")
    p_mine.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Don't respect .gitignore files when scanning project files",
    )
    p_mine.add_argument(
        "--include-ignored",
        action="append",
        default=[],
        help="Always scan these project-relative paths even if ignored; repeat or pass comma-separated paths",
    )
    p_mine.add_argument(
        "--agent",
        default="Callosum",
        help="Your name -- recorded on every drawer (default: Callosum)",
    )
    p_mine.add_argument("--limit", type=int, default=0, help="Max files to process (0 = all)")
    p_mine.add_argument(
        "--dry-run", action="store_true", help="Show what would be filed without filing"
    )
    p_mine.add_argument(
        "--extract",
        choices=["exchange", "general"],
        default="exchange",
        help="Extraction strategy for convos mode: 'exchange' (default) or 'general' (5 memory types)",
    )

    # search
    p_search = sub.add_parser("search", help="Find anything, exact words")
    p_search.add_argument("query", help="What to search for")
    p_search.add_argument("--wing", default=None, help="Limit to one project")
    p_search.add_argument("--room", default=None, help="Limit to one room")
    p_search.add_argument("--results", type=int, default=5, help="Number of results")

    # wake-up
    p_wakeup = sub.add_parser("wake-up", help="Show L0 + L1 wake-up context (~600-900 tokens)")
    p_wakeup.add_argument("--wing", default=None, help="Wake-up for a specific project/wing")

    # watch
    p_watch = sub.add_parser(
        "watch", help="Start or manage the Universal background daemon to auto-mine all projects"
    )
    p_watch.add_argument(
        "--all", action="store_true", help="Watch ALL active Callosum projects on this machine"
    )
    p_watch.add_argument(
        "--daemon",
        action="store_true",
        help="Launch the watcher silently in the background (Windows detached process)",
    )
    p_watch.add_argument(
        "--stop", action="store_true", help="Kill the background daemon if it is running"
    )
    p_watch.add_argument(
        "--status", action="store_true", help="Check if the daemon is currently ONLINE or OFFLINE"
    )
    p_watch.add_argument(
        "--install-startup",
        action="store_true",
        help="Register the daemon to launch silently on Windows boot via Registry",
    )
    # split
    p_split = sub.add_parser(
        "split",
        help="Split concatenated transcript mega-files into per-session files (run before mine)",
    )
    p_split.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Directory containing transcript files (default: current directory)",
    )
    p_split.add_argument(
        "--output-dir",
        default=None,
        help="Write split files here (default: same directory as source files)",
    )
    p_split.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be split without writing files",
    )
    p_split.add_argument(
        "--min-sessions",
        type=int,
        default=2,
        help="Only split files containing at least N sessions (default: 2)",
    )

    # repair
    sub.add_parser(
        "repair",
        help="Rebuild palace vector index from stored data (fixes segfaults after corruption)",
    )

    # gc
    p_gc = sub.add_parser("gc", help="Clean up stale drawers for deleted files")
    p_gc.add_argument(
        "dir",
        nargs="?",
        default=".",
        help="Project directory or antigravity brain directory to GC (default: current directory)",
    )
    p_gc.add_argument(
        "--dry-run", action="store_true", help="Show what would be removed without removing it"
    )

    # sweep & scheduling
    sub.add_parser("sweep", help="Auto-mine and GC all discovered projects")

    p_schedule = sub.add_parser(
        "schedule", help="Register a Windows scheduled task to run 'sweep' periodically"
    )
    p_schedule.add_argument(
        "--interval", type=int, default=4, help="Run every N hours (default: 4)"
    )

    sub.add_parser("unschedule", help="Remove the auto-sweep scheduled task")
    sub.add_parser("schedules", help="Show active Callosum scheduled tasks")

    # health & maintain
    sub.add_parser(
        "health", help="Full system health check: drawers, closets, isolation, staleness, schedule"
    )

    p_maintain = sub.add_parser(
        "maintain", help="Run automated maintenance: stale fix, GC, coverage report"
    )
    p_maintain.add_argument(
        "--auto-fix",
        action="store_true",
        help="Automatically remediate stale files and migrate ChromaDB",
    )
    p_maintain.add_argument(
        "--dry-run", action="store_true", help="Show what would be done without doing it"
    )

    # migrate
    p_migrate = sub.add_parser("migrate", help="Migrate ChromaDB palace data to current version")
    p_migrate.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p_migrate.add_argument("--no-backup", action="store_true", help="Skip backup (not recommended)")

    # status
    sub.add_parser("status", help="Show what's been filed")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dispatch = {
        "setup": cmd_setup,
        "init": cmd_init,
        "mine": cmd_mine,
        "split": cmd_split,
        "search": cmd_search,
        "wake-up": cmd_wakeup,
        "watch": cmd_watch,
        "repair": cmd_repair,
        "gc": cmd_gc,
        "sweep": cmd_sweep,
        "schedule": cmd_schedule,
        "unschedule": cmd_unschedule,
        "schedules": cmd_schedules,
        "health": cmd_health,
        "maintain": cmd_maintain,
        "migrate": cmd_migrate,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
