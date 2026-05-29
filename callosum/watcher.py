import os
import time
import subprocess
import logging
import sys
from pathlib import Path
from typing import Dict
from logging.handlers import RotatingFileHandler

from .config import CallosumConfig

# Directories we should never scan to keep things ultra fast
IGNORE_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv", ".env", ".idea", ".vscode"}
PID_FILE_NAME = "watcher.pid"


def get_pid_file() -> Path:
    config = CallosumConfig()
    path = Path(config.palace_path) / PID_FILE_NAME
    return path


def is_daemon_running() -> bool:
    """Checks if the daemon is currently running via the PID file."""
    pid_file = get_pid_file()
    if not pid_file.exists():
        return False
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())
        output = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}", "/NH"], text=True)
        return str(pid) in output
    except Exception:
        return False


def write_pid_file():
    """Write current process ID to the lock file."""
    pid_file = get_pid_file()
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove the PID file upon clean exit."""
    pid_file = get_pid_file()
    if pid_file.exists():
        try:
            pid_file.unlink()
        except OSError:
            pass


def stop_daemon():
    """Kill the background daemon if it's running."""
    if not is_daemon_running():
        print("Daemon is not running.")
        return False

    pid_file = get_pid_file()
    try:
        with open(pid_file, "r") as f:
            pid = int(f.read().strip())

        # Native Windows OS kill to avoid psutil dependency
        res = subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True)

        if res.returncode == 0:
            print(f"Callosum background daemon (PID {pid}) stopped.")
            remove_pid_file()
            return True
        else:
            if "not found" in res.stderr.lower():
                print("Process no longer exists, cleaning up PID file...")
                remove_pid_file()
            else:
                print(f"Failed to stop daemon: {res.stderr}")
    except Exception as e:
        print(f"Error while attempting to stop daemon: {e}")
    return False


def setup_global_logger() -> logging.Logger:
    """Sets up a robust logger to track errors natively via Callosum config with log rotation."""
    log_dir = Path(CallosumConfig().palace_path)
    log_file = log_dir / "global_watcher.log"

    logger = logging.getLogger("callosum_watcher")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers
    if not logger.handlers:
        # Rotating file handler: 5 MB limit, keep 3 backup logs
        fh = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        fh.setFormatter(formatter)
        logger.addHandler(fh)

        # Also print to terminal for the user (only valid if running in foreground)
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(ch)

    return logger


def get_latest_mtime(directory: str) -> float:
    """Scans the directory and returns the most recent modification time of any relevant file."""
    latest = 0.0
    if not os.path.isdir(directory):
        return 0.0

    for root, dirs, files in os.walk(directory):
        # Mutating dirs in-place to prune ignored directories from the walk tree
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

        for file in files:
            file_path = os.path.join(root, file)
            try:
                mtime = os.stat(file_path).st_mtime
                if mtime > latest:
                    latest = mtime
            except (OSError, FileNotFoundError):
                # Ignore files that disappear during the scan
                pass
    return latest


def start_global_watcher(run_once: bool = False):
    """
    Infinite polling loop that checks for file modifications every 30 seconds across ALL active projects.
    """
    if is_daemon_running():
        print(
            "Warning: A daemon process is already running. Please stop it first via `callosum watch --stop`."
        )
        return

    logger = setup_global_logger()

    # Discover all registered projects
    config = CallosumConfig()
    try:
        project_dict = config.get_registered_workspaces()
    except Exception as e:
        logger.error(f"Failed to find workspaces configuration: {e}")
        return

    write_pid_file()
    logger.info(f"Starting Universal Daemon. Tracking {len(project_dict)} active projects.")

    # Compute base modification times for all folders
    base_mtimes: Dict[str, float] = {}
    for proj_dir, wing in project_dict.items():
        base_mtimes[proj_dir] = get_latest_mtime(proj_dir)
        logger.info(f"Watching: {wing} ({proj_dir})")

    try:
        while True:
            # 30 seconds polling interval
            time.sleep(30)

            # Re-discover in case the user added a new workspace folder
            project_dict = config.get_registered_workspaces()

            for proj_dir, wing in project_dict.items():
                try:
                    if proj_dir not in base_mtimes:
                        base_mtimes[proj_dir] = get_latest_mtime(proj_dir)

                    current_mtime = get_latest_mtime(proj_dir)
                    if current_mtime > base_mtimes[proj_dir]:
                        logger.info(f"Changes detected in {wing}! Triggering incremental Miner...")

                        # Exec miner subprocess safely
                        cmd = [
                            sys.executable,
                            "-m",
                            "callosum.cli",
                            "mine",
                            proj_dir,
                            "--wing",
                            wing,
                        ]

                        parent_dir = os.path.dirname(os.path.dirname(__file__))
                        env = os.environ.copy()
                        env["PYTHONPATH"] = parent_dir

                        result = subprocess.run(
                            cmd, env=env, cwd=parent_dir, capture_output=True, text=True
                        )

                        if result.returncode == 0:
                            logger.info(f"Miner ({wing}) completed successfully.")
                        else:
                            logger.error(
                                f"Miner ({wing}) failed with exit code: {result.returncode}"
                            )
                            logger.error(f"Miner stderror: {result.stderr}")

                        # Update base_mtime post-mine
                        base_mtimes[proj_dir] = get_latest_mtime(proj_dir)
                except Exception as e:
                    # Isolate exceptions per-project so a failing project doesn't kill the whole daemon
                    logger.error(f"Watcher error scanning {proj_dir}: {e}")

            # For testing hooks to kill the infinite loop
            if run_once:
                break

    except KeyboardInterrupt:
        logger.info("Watcher interrupted. Shutting down gracefully.")
    finally:
        remove_pid_file()


def daemonize():
    """Forks the watcher into a detached headless Windows process."""
    if is_daemon_running():
        print("Daemon is already running!")
        return

    print("Launching Callosum Universal Daemon in the background...")
    pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_path):
        pythonw_path = sys.executable  # Fallback

    # Run as a module so relative imports inside callosum work properly
    cmd = [pythonw_path, "-m", "callosum.cli", "watch", "--all"]

    # We must explicitly add the parent dir to PYTHONPATH so it can find 'callosum'
    parent_dir = os.path.dirname(os.path.dirname(__file__))
    env = os.environ.copy()
    env["PYTHONPATH"] = parent_dir

    # Detach process
    subprocess.Popen(
        cmd,
        env=env,
        cwd=parent_dir,
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print(
        "Daemon successfully detached. Logs can be found in `~/.gemini/antigravity/knowledge/global_watcher.log`."
    )


def install_windows_startup():
    """Register the Callosum Daemon to run on Windows Startup."""
    import sys
    import os

    try:
        import winreg
    except ImportError:
        print("  [-] winreg module not available. This feature is for Windows only.")
        return

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    pythonw_path = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_path):
        pythonw_path = sys.executable

    parent_dir = os.path.dirname(os.path.dirname(__file__))

    config = CallosumConfig()
    vbs_path = Path(config.palace_path) / "callosum_startup.vbs"

    # We use VBS to silently set PYTHONPATH, cwd, and launch pythonw
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Environment("PROCESS")("PYTHONPATH") = "{parent_dir}"
WshShell.CurrentDirectory = "{parent_dir}"
WshShell.Run """{pythonw_path}"" -m callosum.cli watch --all --daemon", 0, False
'''

    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)

        registry_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
        winreg.SetValueEx(
            registry_key, "CallosumDaemon", 0, winreg.REG_SZ, f'wscript.exe "{vbs_path}"'
        )
        winreg.CloseKey(registry_key)
        print("  [+] Successfully registered Callosum Universal Daemon in Windows Startup.")
        print(f"  [+] Launcher script written to: {vbs_path}")
    except Exception as e:
        print(f"  [-] Failed to register startup item: {e}")
