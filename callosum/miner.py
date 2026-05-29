#!/usr/bin/env python3
"""
miner.py -- Files everything into the palace.

Reads callosum.yaml from the project directory to know the wing + rooms.
Routes each file to the right room based on content.
Stores verbatim chunks as drawers. No summaries. Ever.
"""

import os
import hashlib
import fnmatch
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from .closet import (
    build_closet_lines,
    get_closets_collection,
    purge_file_closets,
    upsert_closet_lines,
)

os.environ["ANONYMIZED_TELEMETRY"] = "False"
import chromadb

READABLE_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".sh",
    ".csv",
    ".sql",
    ".toml",
}

SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    ".next",
    "coverage",
    ".callosum",
    ".ruff_cache",
    ".mypy_cache",
    ".pytest_cache",
    ".cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".ipynb_checkpoints",
    ".eggs",
    "htmlcov",
    "target",
}

SKIP_FILENAMES = {
    "callosum.yaml",
    "Callosum.yml",
    "mempal.yaml",
    "mempal.yml",
    ".gitignore",
    "package-lock.json",
}

CHUNK_SIZE = 2500  # chars per drawer
CHUNK_OVERLAP = 300  # overlap between chunks
MIN_CHUNK_SIZE = 50  # skip tiny chunks


# =============================================================================
# IGNORE MATCHING
# =============================================================================


class GitignoreMatcher:
    """Lightweight matcher for one directory's .gitignore patterns."""

    def __init__(self, base_dir: Path, rules: list):
        self.base_dir = base_dir
        self.rules = rules

    @classmethod
    def from_dir(cls, dir_path: Path):
        gitignore_path = dir_path / ".gitignore"
        if not gitignore_path.is_file():
            return None

        try:
            lines = gitignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return None

        rules = []
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            if line.startswith("\\#") or line.startswith("\\!"):
                line = line[1:]
            elif line.startswith("#"):
                continue

            negated = line.startswith("!")
            if negated:
                line = line[1:]

            anchored = line.startswith("/")
            if anchored:
                line = line.lstrip("/")

            dir_only = line.endswith("/")
            if dir_only:
                line = line.rstrip("/")

            if not line:
                continue

            rules.append(
                {
                    "pattern": line,
                    "anchored": anchored,
                    "dir_only": dir_only,
                    "negated": negated,
                }
            )

        if not rules:
            return None

        return cls(dir_path, rules)

    def matches(self, path: Path, is_dir: bool = None):
        try:
            relative = path.relative_to(self.base_dir).as_posix().strip("/")
        except ValueError:
            return None

        if not relative:
            return None

        if is_dir is None:
            is_dir = path.is_dir()

        ignored = None
        for rule in self.rules:
            if self._rule_matches(rule, relative, is_dir):
                ignored = not rule["negated"]
        return ignored

    def _rule_matches(self, rule: dict, relative: str, is_dir: bool) -> bool:
        pattern = rule["pattern"]
        parts = relative.split("/")
        pattern_parts = pattern.split("/")

        if rule["dir_only"]:
            target_parts = parts if is_dir else parts[:-1]
            if not target_parts:
                return False
            if rule["anchored"] or len(pattern_parts) > 1:
                return self._match_from_root(target_parts, pattern_parts)
            return any(fnmatch.fnmatch(part, pattern) for part in target_parts)

        if rule["anchored"] or len(pattern_parts) > 1:
            return self._match_from_root(parts, pattern_parts)

        return any(fnmatch.fnmatch(part, pattern) for part in parts)

    def _match_from_root(self, target_parts: list, pattern_parts: list) -> bool:
        def matches(path_index: int, pattern_index: int) -> bool:
            if pattern_index == len(pattern_parts):
                return True

            if path_index == len(target_parts):
                return all(part == "**" for part in pattern_parts[pattern_index:])

            pattern_part = pattern_parts[pattern_index]
            if pattern_part == "**":
                return matches(path_index, pattern_index + 1) or matches(
                    path_index + 1, pattern_index
                )

            if not fnmatch.fnmatch(target_parts[path_index], pattern_part):
                return False

            return matches(path_index + 1, pattern_index + 1)

        return matches(0, 0)


def load_gitignore_matcher(dir_path: Path, cache: dict):
    """Load and cache one directory's .gitignore matcher."""
    if dir_path not in cache:
        cache[dir_path] = GitignoreMatcher.from_dir(dir_path)
    return cache[dir_path]


def is_gitignored(path: Path, matchers: list, is_dir: bool = False) -> bool:
    """Apply active .gitignore matchers in ancestor order; last match wins."""
    ignored = False
    for matcher in matchers:
        decision = matcher.matches(path, is_dir=is_dir)
        if decision is not None:
            ignored = decision
    return ignored


def should_skip_dir(dirname: str) -> bool:
    """Skip known generated/cache directories before gitignore matching."""
    return dirname in SKIP_DIRS or dirname.endswith(".egg-info")


def normalize_include_paths(include_ignored: list) -> set:
    """Normalize comma-parsed include paths into project-relative POSIX strings."""
    normalized = set()
    for raw_path in include_ignored or []:
        candidate = str(raw_path).strip().strip("/")
        if candidate:
            normalized.add(Path(candidate).as_posix())
    return normalized


def is_exact_force_include(path: Path, project_path: Path, include_paths: set) -> bool:
    """Return True when a path exactly matches an explicit include override."""
    if not include_paths:
        return False

    try:
        relative = path.relative_to(project_path).as_posix().strip("/")
    except ValueError:
        return False

    return relative in include_paths


def is_force_included(path: Path, project_path: Path, include_paths: set) -> bool:
    """Return True when a path or one of its ancestors/descendants was explicitly included."""
    if not include_paths:
        return False

    try:
        relative = path.relative_to(project_path).as_posix().strip("/")
    except ValueError:
        return False

    if not relative:
        return False

    for include_path in include_paths:
        if relative == include_path:
            return True
        if relative.startswith(f"{include_path}/"):
            return True
        if include_path.startswith(f"{relative}/"):
            return True

    return False


# =============================================================================
# CONFIG
# =============================================================================


def load_config(project_dir: str) -> dict:
    """Load callosum.yaml from project directory (falls back to mempal.yaml)."""
    import yaml

    config_path = Path(project_dir).expanduser().resolve() / "callosum.yaml"
    if not config_path.exists():
        # Fallback to legacy name
        legacy_path = Path(project_dir).expanduser().resolve() / "mempal.yaml"
        if legacy_path.exists():
            config_path = legacy_path
        else:
            raise FileNotFoundError(
                f"No callosum.yaml found in {project_dir}. Run: Callosum init {project_dir}"
            )
    with open(config_path) as f:
        return yaml.safe_load(f)


# =============================================================================
# FILE ROUTING -- which room does this file belong to?
# =============================================================================


def detect_room(filepath: Path, content: str, rooms: list, project_path: Path) -> str:
    """
    Route a file to the right room.
    Priority:
    1. Folder path matches a room name
    2. Filename matches a room name or keyword
    3. Content keyword scoring
    4. Fallback: "general"
    """
    relative = str(filepath.relative_to(project_path)).lower()
    filename = filepath.stem.lower()
    content_lower = content[:2000].lower()

    # Priority 1: folder path matches room name or keywords
    path_parts = relative.replace("\\", "/").split("/")
    for part in path_parts[:-1]:  # skip filename itself
        for room in rooms:
            candidates = [room["name"].lower()] + [k.lower() for k in room.get("keywords", [])]
            if any(part == c or c in part or part in c for c in candidates):
                return room["name"]

    # Priority 2: filename matches room name
    for room in rooms:
        if room["name"].lower() in filename or filename in room["name"].lower():
            return room["name"]

    # Priority 3: keyword scoring from room keywords + name
    scores = defaultdict(int)
    for room in rooms:
        keywords = room.get("keywords", []) + [room["name"]]
        for kw in keywords:
            count = content_lower.count(kw.lower())
            scores[room["name"]] += count

    if scores:
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best

    return "general"


# =============================================================================
# CHUNKING
# =============================================================================


def chunk_text(content: str, source_file: str) -> list:
    """
    Split content into drawer-sized chunks.
    Tries to split on paragraph/line boundaries.
    Returns list of {"content": str, "chunk_index": int}
    """
    # Clean up
    content = content.strip()
    if not content:
        return []

    chunks = []
    start = 0
    chunk_index = 0

    while start < len(content):
        end = min(start + CHUNK_SIZE, len(content))

        # Try to break at paragraph boundary
        if end < len(content):
            newline_pos = content.rfind("\n\n", start, end)
            if newline_pos > start + CHUNK_SIZE // 2:
                end = newline_pos
            else:
                newline_pos = content.rfind("\n", start, end)
                if newline_pos > start + CHUNK_SIZE // 2:
                    end = newline_pos

        chunk = content[start:end].strip()
        if len(chunk) >= MIN_CHUNK_SIZE:
            chunks.append(
                {
                    "content": chunk,
                    "chunk_index": chunk_index,
                }
            )
            chunk_index += 1

        start = end - CHUNK_OVERLAP if end < len(content) else end

    return chunks


# =============================================================================
# PALACE -- ChromaDB operations
# =============================================================================


def get_collection(palace_path: str):
    os.makedirs(palace_path, exist_ok=True)
    client = chromadb.PersistentClient(path=palace_path)
    try:
        return client.get_collection("callosum_drawers")
    except Exception:
        return client.create_collection("callosum_drawers")


def compute_file_hash(content: str) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def file_needs_update(collection, source_file: str, content_hash: str) -> str:
    """
    Check if a file needs mining.
    Returns: "new", "unchanged", or "changed"
    """
    try:
        results = collection.get(where={"source_file": source_file}, limit=1, include=["metadatas"])
        if not results.get("ids", []):
            return "new"

        metadata = results["metadatas"][0]
        if metadata.get("content_hash") == content_hash:
            return "unchanged"
        else:
            return "changed"
    except Exception:
        return "new"


def delete_drawers_for_file(collection, source_file: str):
    """Purge all drawers for a given source file."""
    try:
        collection.delete(where={"source_file": source_file})
    except Exception:
        pass


def add_drawer(
    collection,
    wing: str,
    room: str,
    content: str,
    source_file: str,
    chunk_index: int,
    agent: str,
    content_hash: str,
):
    """Add one drawer to the palace. Returns the drawer_id on success, None on skip. (Legacy)"""
    drawer_id = f"drawer_{wing}_{room}_{hashlib.md5((source_file + str(chunk_index)).encode(), usedforsecurity=False).hexdigest()[:16]}"
    try:
        collection.add(
            documents=[content],
            ids=[drawer_id],
            metadatas=[
                {
                    "wing": wing,
                    "room": room,
                    "source_file": source_file,
                    "chunk_index": chunk_index,
                    "added_by": agent,
                    "filed_at": datetime.now().isoformat(),
                    "content_hash": content_hash,
                }
            ],
        )
        return drawer_id
    except Exception as e:
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            return None
        raise


# =============================================================================
# PROCESS ONE FILE
# =============================================================================


def process_file(
    filepath: Path,
    project_path: Path,
    collection,
    wing: str,
    rooms: list,
    agent: str,
    dry_run: bool,
    closets_col=None,
) -> tuple[int, str]:
    """Read, chunk, route, and file one file. Returns (drawer_count, status_string).

    If closets_col is provided, also builds closet pointer lines for the file.
    """

    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0, "skipped"

    content = content.strip()
    if len(content) < MIN_CHUNK_SIZE:
        return 0, "skipped"

    source_file = str(filepath)
    content_hash = compute_file_hash(content)

    if not dry_run:
        status = file_needs_update(collection, source_file, content_hash)
        if status == "unchanged":
            return 0, "unchanged"
        if status == "changed":
            delete_drawers_for_file(collection, source_file)
            # Also purge stale closets for this file
            if closets_col is not None:
                purge_file_closets(closets_col, source_file)
    else:
        status = "new"

    room = detect_room(filepath, content, rooms, project_path)
    chunks = chunk_text(content, source_file)

    if dry_run:
        print(f"    [DRY RUN] {filepath.name} -> room:{room} ({len(chunks)} drawers)")
        return len(chunks), "new"

    drawers_added = 0
    drawer_ids = []

    batch_docs = []
    batch_ids = []
    batch_metas = []

    for chunk in chunks:
        drawer_id = f"drawer_{wing}_{room}_{hashlib.md5((source_file + str(chunk['chunk_index'])).encode(), usedforsecurity=False).hexdigest()[:16]}"
        batch_docs.append(chunk["content"])
        batch_ids.append(drawer_id)
        batch_metas.append(
            {
                "wing": wing,
                "room": room,
                "source_file": source_file,
                "chunk_index": chunk["chunk_index"],
                "added_by": agent,
                "filed_at": datetime.now().isoformat(),
                "content_hash": content_hash,
            }
        )
        drawer_ids.append(drawer_id)

    if batch_docs and not dry_run:
        try:
            collection.add(
                documents=batch_docs,
                ids=batch_ids,
                metadatas=batch_metas,
            )
            drawers_added = len(batch_ids)
        except Exception as e:
            if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
                print(f"    Error filing batch for {filepath.name}: {e}")
            else:
                # If there's a mix of existing/new, we just skip adding to drawer_ids
                # In a real environment, we've already done delete_drawers_for_file if it changed
                drawers_added = len(batch_ids)

    # Build closet index for this file
    if closets_col is not None and drawer_ids:
        closet_lines = build_closet_lines(
            source_file=source_file,
            drawer_ids=drawer_ids,
            content=content,
            wing=wing,
            room=room,
        )
        closet_id_base = f"closet_{wing}_{room}_{hashlib.md5(source_file.encode(), usedforsecurity=False).hexdigest()[:16]}"
        closet_meta = {
            "wing": wing,
            "room": room,
            "source_file": source_file,
            "filed_at": datetime.now().isoformat(),
        }
        upsert_closet_lines(closets_col, closet_id_base, closet_lines, closet_meta)

    return drawers_added, status


# =============================================================================
# SCAN PROJECT
# =============================================================================


def scan_project(
    project_dir: str,
    respect_gitignore: bool = True,
    include_ignored: list = None,
) -> list:
    """Return list of all readable file paths."""
    project_path = Path(project_dir).expanduser().resolve()
    files = []
    active_matchers = []
    matcher_cache = {}
    include_paths = normalize_include_paths(include_ignored)

    for root, dirs, filenames in os.walk(project_path):
        root_path = Path(root)

        if respect_gitignore:
            active_matchers = [
                matcher
                for matcher in active_matchers
                if root_path == matcher.base_dir or matcher.base_dir in root_path.parents
            ]
            current_matcher = load_gitignore_matcher(root_path, matcher_cache)
            if current_matcher is not None:
                active_matchers.append(current_matcher)

        dirs[:] = [
            d
            for d in dirs
            if is_force_included(root_path / d, project_path, include_paths)
            or not should_skip_dir(d)
        ]
        if respect_gitignore and active_matchers:
            dirs[:] = [
                d
                for d in dirs
                if is_force_included(root_path / d, project_path, include_paths)
                or not is_gitignored(root_path / d, active_matchers, is_dir=True)
            ]

        for filename in filenames:
            filepath = root_path / filename
            force_include = is_force_included(filepath, project_path, include_paths)
            exact_force_include = is_exact_force_include(filepath, project_path, include_paths)

            if not force_include and filename in SKIP_FILENAMES:
                continue
            if filepath.suffix.lower() not in READABLE_EXTENSIONS and not exact_force_include:
                continue
            if respect_gitignore and active_matchers and not force_include:
                if is_gitignored(filepath, active_matchers, is_dir=False):
                    continue
            files.append(filepath)
    return files


# =============================================================================
# MAIN: MINE
# =============================================================================


def mine(
    project_dir: str,
    palace_path: str,
    wing_override: str = None,
    agent: str = "Callosum",
    limit: int = 0,
    dry_run: bool = False,
    respect_gitignore: bool = True,
    include_ignored: list = None,
):
    """Mine a project directory into the palace."""

    project_path = Path(project_dir).expanduser().resolve()
    config = load_config(project_dir)

    wing = wing_override or config["wing"]
    rooms = config.get("rooms", [{"name": "general", "description": "All project files"}])

    files = scan_project(
        project_dir,
        respect_gitignore=respect_gitignore,
        include_ignored=include_ignored,
    )
    if limit > 0:
        files = files[:limit]

    print(f"\n{'=' * 55}")
    print("  Callosum Mine")
    print(f"{'=' * 55}")
    print(f"  Wing:    {wing}")
    print(f"  Rooms:   {', '.join(r['name'] for r in rooms)}")
    print(f"  Files:   {len(files)}")
    print(f"  Palace:  {palace_path}")
    if dry_run:
        print("  DRY RUN -- nothing will be filed")
    if not respect_gitignore:
        print("  .gitignore: DISABLED")
    if include_ignored:
        print(f"  Include: {', '.join(sorted(normalize_include_paths(include_ignored)))}")
    print(f"{'-' * 55}\n")

    if not dry_run:
        collection = get_collection(palace_path)
        closets_col = get_closets_collection(palace_path)
    else:
        collection = None
        closets_col = None

    total_drawers = 0
    files_skipped = 0
    files_updated = 0
    files_new = 0
    room_counts = defaultdict(int)

    for i, filepath in enumerate(files, 1):
        drawers, status = process_file(
            filepath=filepath,
            project_path=project_path,
            collection=collection,
            wing=wing,
            rooms=rooms,
            agent=agent,
            dry_run=dry_run,
            closets_col=closets_col,
        )
        if status == "unchanged" or (status == "skipped" and drawers == 0):
            files_skipped += 1
        elif status == "changed":
            files_updated += 1
            total_drawers += drawers
        else:
            files_new += 1
            total_drawers += drawers

        if status in ("new", "changed") and not dry_run:
            room = detect_room(filepath, "", rooms, project_path)
            room_counts[room] += 1
            action_tag = "Updated" if status == "changed" else "Added"
            print(f"  [+] [{i:4}/{len(files)}] {filepath.name[:40]:40} | {action_tag:7} +{drawers}")

    print(f"\n{'=' * 55}")
    print("  Done.")
    print(f"  Files processed: {files_new + files_updated}")
    print(f"  Files skipped (unchanged): {files_skipped}")
    print(f"  Files updated (changed): {files_updated}")
    print(f"  Drawers filed: {total_drawers}")
    print("\n  By room:")
    for room, count in sorted(room_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"    {room:20} {count} files")
    print('\n  Next: Callosum search "what you\'re looking for"')
    print(f"{'=' * 55}\n")


# =============================================================================
# STATUS
# =============================================================================


def status(palace_path: str):
    """Show what's been filed in the palace."""
    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception:
        print(f"\n  No palace found at {palace_path}")
        print("  Run: Callosum init <dir> then Callosum mine <dir>")
        return

    # Count by wing and room
    r = col.get(limit=10000, include=["metadatas"])
    metas = r["metadatas"]

    wing_rooms = defaultdict(lambda: defaultdict(int))
    for m in metas:
        wing_rooms[m.get("wing", "?")][m.get("room", "?")] += 1

    print(f"\n{'=' * 55}")
    print(f"  Callosum Status -- {len(metas)} drawers")
    print(f"{'=' * 55}\n")
    for wing, rooms in sorted(wing_rooms.items()):
        print(f"  WING: {wing}")
        for room, count in sorted(rooms.items(), key=lambda x: x[1], reverse=True):
            print(f"    ROOM: {room:20} {count:5} drawers")
        print()
    print(f"{'=' * 55}\n")


# =============================================================================
# GARBAGE COLLECTION
# =============================================================================


def garbage_collect(palace_path: str, project_dir: str, wing: str, dry_run: bool = False) -> dict:
    """Find and remove drawers for files that no longer exist."""
    print(f"\n{'=' * 55}")
    print("  Callosum GC")
    print(f"{'=' * 55}")
    print(f"  Project: {project_dir}")
    print(f"  Wing:    {wing}")
    if dry_run:
        print("  DRY RUN -- nothing will be deleted")
    print(f"{'-' * 55}\n")

    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception:
        print("  No palace found.")
        return {"files_removed": 0, "drawers_removed": 0}

    # Query all metadata for the wing
    try:
        results = col.get(where={"wing": wing}, include=["metadatas"])
        metadatas = results.get("metadatas", [])
    except Exception as e:
        print(f"  Error querying palace: {e}")
        return {"files_removed": 0, "drawers_removed": 0}

    if not metadatas:
        print("  No drawers found for this wing.")
        return {"files_removed": 0, "drawers_removed": 0}

    # Find unique source files and map to drawer IDs
    source_files = set()
    for m in metadatas:
        if "source_file" in m:
            source_files.add(m["source_file"])

    files_removed = 0
    drawers_removed = 0

    for source_file in source_files:
        if not os.path.exists(source_file):
            if dry_run:
                print(f"    [DRY RUN] Would delete drawers for missing file: {source_file}")
                # We can't actually count dry run drawers removed efficiently without another query
                # but we can just say "1 file"
                files_removed += 1
            else:
                try:
                    # Count how many we're deleting
                    count = len(col.get(where={"source_file": source_file}).get("ids", []))
                    col.delete(where={"source_file": source_file})
                    print(f"  [+] Purged {count} orphaned drawers for: {Path(source_file).name}")
                    drawers_removed += count
                    files_removed += 1
                except Exception as e:
                    print(f"  ! Error deleting {source_file}: {e}")

    print(f"\n{'=' * 55}")
    print("  GC Complete.")
    print(f"  Files pruned:   {files_removed}")
    print(f"  Drawers purged: {drawers_removed}")
    print(f"{'=' * 55}\n")
    return {"files_removed": files_removed, "drawers_removed": drawers_removed}
