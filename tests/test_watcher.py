"""Tests for callosum.watcher — utility functions (no daemon/process tests)."""

import os
import time
from unittest.mock import patch, MagicMock

from callosum.watcher import get_latest_mtime, IGNORE_DIRS


# ── get_latest_mtime ─────────────────────────────────────────────────────────


class TestGetLatestMtime:
    def test_empty_directory(self, tmp_path):
        assert get_latest_mtime(str(tmp_path)) == 0.0

    def test_nonexistent_directory(self):
        assert get_latest_mtime("/nonexistent/path/12345") == 0.0

    def test_returns_newest_mtime(self, tmp_path):
        old = tmp_path / "old.py"
        old.write_text("old")
        old_time = time.time() - 100
        os.utime(old, (old_time, old_time))

        new = tmp_path / "new.py"
        new.write_text("new")
        # new.py has the current mtime (newer)

        result = get_latest_mtime(str(tmp_path))
        assert result >= new.stat().st_mtime

    def test_ignores_git_directory(self, tmp_path):
        # Create a .git directory with a very new file
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        git_file = git_dir / "HEAD"
        git_file.write_text("ref: refs/heads/main")

        # Create a regular file that is older
        src = tmp_path / "main.py"
        src.write_text("code")
        old_time = time.time() - 3600
        os.utime(src, (old_time, old_time))

        result = get_latest_mtime(str(tmp_path))
        # Should match the src file, NOT the .git/HEAD file
        assert abs(result - old_time) < 2

    def test_ignores_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.json").write_text("{}")

        src = tmp_path / "app.js"
        src.write_text("code")
        old_time = time.time() - 3600
        os.utime(src, (old_time, old_time))

        result = get_latest_mtime(str(tmp_path))
        assert abs(result - old_time) < 2

    def test_ignores_pycache(self, tmp_path):
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "mod.pyc").write_text("bytes")

        assert get_latest_mtime(str(tmp_path)) == 0.0  # Only ignored dirs, no real files

    def test_scans_nested_directories(self, tmp_path):
        nested = tmp_path / "src" / "core"
        nested.mkdir(parents=True)
        deep_file = nested / "engine.py"
        deep_file.write_text("deep code")

        result = get_latest_mtime(str(tmp_path))
        assert result >= deep_file.stat().st_mtime

    def test_handles_all_ignore_dirs(self):
        """Verify the IGNORE_DIRS constant contains expected entries."""
        expected = {
            ".git",
            "node_modules",
            "__pycache__",
            "venv",
            ".venv",
            ".env",
            ".idea",
            ".vscode",
        }
        assert IGNORE_DIRS == expected


# ── PID file management ─────────────────────────────────────────────────────


class TestPidFileManagement:
    @patch("callosum.watcher.CallosumConfig")
    def test_write_and_read_pid(self, mock_config_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.palace_path = str(tmp_path)
        mock_config_cls.return_value = mock_config

        from callosum.watcher import write_pid_file, get_pid_file

        write_pid_file()
        pid_file = get_pid_file()
        assert pid_file.exists()
        assert pid_file.read_text().strip() == str(os.getpid())

    @patch("callosum.watcher.CallosumConfig")
    def test_remove_pid_file(self, mock_config_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.palace_path = str(tmp_path)
        mock_config_cls.return_value = mock_config

        from callosum.watcher import write_pid_file, remove_pid_file, get_pid_file

        write_pid_file()
        assert get_pid_file().exists()

        remove_pid_file()
        assert not get_pid_file().exists()

    @patch("callosum.watcher.CallosumConfig")
    def test_remove_nonexistent_is_safe(self, mock_config_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.palace_path = str(tmp_path)
        mock_config_cls.return_value = mock_config

        from callosum.watcher import remove_pid_file

        # Should not raise
        remove_pid_file()

    @patch("callosum.watcher.CallosumConfig")
    def test_is_daemon_running_no_pid_file(self, mock_config_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.palace_path = str(tmp_path)
        mock_config_cls.return_value = mock_config

        from callosum.watcher import is_daemon_running

        assert is_daemon_running() is False

    @patch("callosum.watcher.CallosumConfig")
    def test_get_pid_file_path(self, mock_config_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.palace_path = str(tmp_path)
        mock_config_cls.return_value = mock_config

        from callosum.watcher import get_pid_file

        result = get_pid_file()
        assert result == tmp_path / "watcher.pid"
