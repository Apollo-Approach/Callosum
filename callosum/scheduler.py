import subprocess
from pathlib import Path

from .config import CallosumConfig
from .miner import mine, garbage_collect
from .antigravity_miner import mine_antigravity, garbage_collect_antigravity

TASK_NAME = "Callosum_AutoSweep"


def sweep_all(palace_path: str, workspaces_dir: str, agent: str = "callosum_sweep"):
    """Scan workspaces for all callosum projects, mine and GC each one."""
    print(f"\n{'=' * 55}")
    print("  Callosum Auto-Sweep")
    print(f"{'=' * 55}")

    # 1. Discover all projects
    config = CallosumConfig(workspaces_dir)

    # We can fetch discovered keywords which is a dict of dir -> wing
    dirs = config.build_dynamic_wing_keywords()

    for project_dir_str, wing in dirs.items():
        project_dir = Path(project_dir_str)
        if not project_dir.exists():
            continue

        print(f"\n  Sweeping: {wing} ({project_dir})")
        try:
            # Mine project files (now includes closet building)
            mine(project_dir=str(project_dir), palace_path=palace_path, agent=agent, dry_run=False)
            # GC stale project files
            garbage_collect(
                palace_path=palace_path, project_dir=str(project_dir), wing=wing, dry_run=False
            )
        except Exception as e:
            print(f"  ! Error sweeping {wing}: {e}")

    # 2. Sweep antigravity artifacts
    brain_path = Path.home() / ".gemini" / "antigravity" / "brain"
    if brain_path.exists():
        print(f"\n  Sweeping Antigravity Brain: {brain_path}")
        try:
            mine_antigravity(
                brain_path=str(brain_path), palace_path=palace_path, agent=agent, dry_run=False
            )
            garbage_collect_antigravity(
                palace_path=palace_path, brain_path=str(brain_path), dry_run=False
            )
        except Exception as e:
            print(f"  ! Error sweeping Antigravity: {e}")

    # 3. Post-sweep report
    print(f"\n{'-' * 55}")
    try:
        from .maintain import closet_coverage
        from .isolation import isolation_report

        cov = closet_coverage(palace_path)
        iso = isolation_report()
        print(
            f"  Drawers: {cov.get('drawers', 0)}  |  Closets: {cov.get('closets', 0)}  |  Coverage: {cov.get('coverage_pct', 0)}%"
        )
        print(
            f"  Project wings: {iso['project_wings']}  |  Tunnel links: {iso['tunnel_links']}  |  Isolated: {len(iso['isolated_projects'])}"
        )
    except Exception:
        pass

    print(f"\n{'=' * 55}")
    print("  Sweep Complete.")
    print(f"{'=' * 55}\n")


def register_schedule(interval_hours: int = 4):
    """Register the Windows Scheduled Task for sweeping."""
    print(f"Registering scheduled task '{TASK_NAME}' to run every {interval_hours} hours...")

    command = "cmd.exe /c callosum sweep"
    minutes = interval_hours * 60

    cmd = [
        "schtasks",
        "/create",
        "/sc",
        "minute",
        "/mo",
        str(minutes),
        "/tn",
        TASK_NAME,
        "/tr",
        command,
        "/f",  # force overwrite if exists
    ]

    try:
        _result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"  [+] Task '{TASK_NAME}' registered successfully.")
        print("  You can view it in the Windows Task Scheduler.")
    except subprocess.CalledProcessError as e:
        print(f"  ! Failed to register task: {e.stderr}")


def unregister_schedule():
    """Remove the scheduled task."""
    print(f"Removing scheduled task '{TASK_NAME}'...")

    cmd = ["schtasks", "/delete", "/tn", TASK_NAME, "/f"]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"  [+] Task '{TASK_NAME}' removed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"  ! Failed to remove task: {e.stderr}")


def list_schedules():
    """List the schedule status."""
    cmd = ["schtasks", "/query", "/tn", TASK_NAME, "/fo", "LIST"]

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("\n  Callosum Scheduled Tasks")
        print("  ------------------------")
        for line in result.stdout.splitlines():
            if line.strip():
                print(f"  {line.strip()}")
        print()
    except subprocess.CalledProcessError:
        print("\n  No active schedule found for 'Callosum_AutoSweep'.")
        print("  Run: callosum schedule\n")
