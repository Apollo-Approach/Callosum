#!/usr/bin/env python3
"""
publish.py — Safe publish pipeline for Callosum.

Copies allowed files to dist/github/, scans for PII, and optionally
commits and pushes. Hard-fails if any blocklist pattern is found.

Usage:
    python scripts/publish.py              # Scan only (dry run)
    python scripts/publish.py --commit     # Scan + commit
    python scripts/publish.py --push       # Scan + commit + push
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "github"
BLOCKLIST_FILE = ROOT / ".pii-blocklist"
MANIFEST_FILE = ROOT / ".publish-manifest"

# File extensions to scan (skip binary files)
TEXT_EXTENSIONS = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".json",
    ".ini", ".sh", ".bat", ".ps1", ".html", ".css", ".js", ".ts",
    ".rst", ".csv", ".xml", ".gitignore", "",
}

# Always exclude these from the copy
EXCLUDE_PATTERNS = {
    "__pycache__", ".pyc", ".pyo", ".egg-info", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", ".git",
}


def load_blocklist() -> list[re.Pattern]:
    """Load PII blocklist patterns from .pii-blocklist."""
    if not BLOCKLIST_FILE.exists():
        print(f"  [!] No blocklist found at {BLOCKLIST_FILE}")
        sys.exit(1)

    patterns = []
    for line in BLOCKLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error as e:
            print(f"  [!] Invalid regex in blocklist: {line!r} — {e}")
            sys.exit(1)

    return patterns


def load_manifest() -> list[str]:
    """Load publish manifest entries."""
    if not MANIFEST_FILE.exists():
        print(f"  [!] No manifest found at {MANIFEST_FILE}")
        sys.exit(1)

    entries = []
    for line in MANIFEST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entries.append(line)
    return entries


def should_exclude(path: Path) -> bool:
    """Check if a path should be excluded."""
    for part in path.parts:
        for pattern in EXCLUDE_PATTERNS:
            if part == pattern or part.endswith(pattern):
                return True
    return False


def copy_manifest(manifest: list[str]) -> int:
    """Copy files from manifest to dist/github/. Returns file count."""
    if DIST.exists():
        # Preserve .git directory
        git_dir = DIST / ".git"
        git_backup = None
        if git_dir.exists():
            git_backup = ROOT / "dist" / ".git-backup"
            if git_backup.exists():
                shutil.rmtree(git_backup)
            shutil.move(str(git_dir), str(git_backup))

        # Clean dist
        shutil.rmtree(DIST)
        DIST.mkdir(parents=True)

        # Restore .git
        if git_backup and git_backup.exists():
            shutil.move(str(git_backup), str(git_dir))
    else:
        DIST.mkdir(parents=True)

    file_count = 0
    for entry in manifest:
        src = ROOT / entry
        dst = DIST / entry

        if not src.exists():
            print(f"  [?] Manifest entry not found, skipping: {entry}")
            continue

        if src.is_dir():
            for item in src.rglob("*"):
                if item.is_file() and not should_exclude(item.relative_to(ROOT)):
                    rel = item.relative_to(ROOT)
                    target = DIST / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item), str(target))
                    file_count += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))
            file_count += 1

    return file_count


def scan_for_pii(patterns: list[re.Pattern]) -> list[tuple[str, int, str, str]]:
    """Scan all text files in dist/github/ for PII. Returns list of violations."""
    violations = []

    for filepath in DIST.rglob("*"):
        if not filepath.is_file():
            continue
        if should_exclude(filepath.relative_to(DIST)):
            continue

        # Skip binary files
        suffix = filepath.suffix.lower()
        if suffix not in TEXT_EXTENSIONS:
            continue

        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        rel_path = str(filepath.relative_to(DIST))
        for line_num, line in enumerate(content.splitlines(), 1):
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    violations.append((rel_path, line_num, match.group(), line.strip()[:120]))

    return violations


def main():
    parser = argparse.ArgumentParser(description="Publish Callosum to public repo")
    parser.add_argument("--commit", action="store_true", help="Commit after successful scan")
    parser.add_argument("--push", action="store_true", help="Commit and push after successful scan")
    parser.add_argument("--message", "-m", default="Update public release", help="Commit message")
    args = parser.parse_args()

    if args.push:
        args.commit = True

    print()
    print("=" * 55)
    print("  Callosum Publish Pipeline")
    print("=" * 55)

    # Load config
    print("\n  Loading blocklist...")
    patterns = load_blocklist()
    print(f"  [{len(patterns)} patterns loaded]")

    print("  Loading manifest...")
    manifest = load_manifest()
    print(f"  [{len(manifest)} entries]")

    # Copy files
    print("\n  Copying files to dist/github/...")
    file_count = copy_manifest(manifest)
    print(f"  [{file_count} files copied]")

    # Scan for PII
    print("\n  Scanning for PII violations...")
    violations = scan_for_pii(patterns)

    if violations:
        print(f"\n  !!! BLOCKED: {len(violations)} PII violation(s) found !!!\n")
        for filepath, line_num, matched, context in violations:
            print(f"  {filepath}:{line_num}")
            print(f"    Pattern: {matched!r}")
            print(f"    Context: {context}")
            print()
        print("  Fix these before publishing.")
        print("=" * 55)
        sys.exit(1)

    print("  [CLEAN — no PII found]")

    if not args.commit:
        print("\n  Dry run complete. Use --commit to commit, --push to push.")
        print("=" * 55)
        return

    # Commit
    print(f"\n  Committing: {args.message}")
    git_dir = DIST / ".git"
    if not git_dir.exists():
        print("  [!] No .git in dist/github/ — run 'git init' there first.")
        print("  [!] Then: git remote add origin <repo-url>")
        sys.exit(1)

    subprocess.run(["git", "add", "-A"], cwd=str(DIST), check=True)

    # Check if there are changes to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=str(DIST),
        capture_output=True,
    )
    if result.returncode == 0:
        print("  [No changes to commit]")
    else:
        subprocess.run(
            ["git", "commit", "-m", args.message],
            cwd=str(DIST),
            check=True,
        )
        print("  [Committed]")

    if args.push:
        print("  Pushing...")
        subprocess.run(["git", "push"], cwd=str(DIST), check=True)
        print("  [Pushed]")

    print("\n" + "=" * 55)
    print("  Publish complete.")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
