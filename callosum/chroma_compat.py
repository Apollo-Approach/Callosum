"""
chroma_compat.py -- Pre-open ChromaDB compatibility repairs.

Ported from upstream mempalace ``_fix_missing_collection_type`` and
``_fix_blob_seq_ids`` (mempalace/backends/chroma.py).

These must run BEFORE ``chromadb.PersistentClient`` is constructed:

1. ``_fix_missing_collection_type`` -- chromadb <= 1.5.8 writes
   ``config_json_str = '{}'`` (empty JSON).  chromadb 1.5.9+ requires
   a ``_type`` key and crashes with ``KeyError: '_type'`` without it.

2. ``_fix_blob_seq_ids`` -- chromadb 0.6.x stored ``seq_id`` as
   big-endian 8-byte BLOBs in the ``embeddings`` table.  chromadb
   1.5.x expects INTEGER.  The auto-migration doesn't convert existing
   rows, causing the Rust compactor to crash.

Both functions are idempotent and write a marker file on success so
subsequent opens skip the sqlite3 connection entirely.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_BLOB_FIX_MARKER = ".blob_seq_ids_migrated"
_COLLECTION_TYPE_MARKER = ".collection_type_fixed"


# ---------------------------------------------------------------------------
# Individual repairs (ported from upstream)
# ---------------------------------------------------------------------------


def _fix_missing_collection_type(palace_path: str) -> None:
    """Add ``_type`` to ``collections.config_json_str`` where absent.

    chromadb <= 1.5.8 writes ``config_json_str = '{}'`` (empty JSON) when
    creating collections.  chromadb 1.5.9 switched from the permissive
    ``load_collection_configuration_from_json_str`` to
    ``CollectionConfigurationInternal.from_json`` which requires a ``_type``
    key -- its absence raises ``KeyError: '_type'`` on palace open.

    This migration adds the missing marker so both old and new chromadb
    versions can load the collection.  The value
    ``"CollectionConfigurationInternal"`` matches what ``to_json()`` writes
    for freshly-created collections.

    Must run BEFORE ``PersistentClient`` is created.
    """
    db_path = os.path.join(palace_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return
    marker = os.path.join(palace_path, _COLLECTION_TYPE_MARKER)
    if os.path.isfile(marker):
        return
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute("SELECT id, config_json_str FROM collections").fetchall()
        except sqlite3.OperationalError:
            return
        updates = []
        for coll_id, config_str in rows:
            if not config_str:
                config_str = "{}"
            try:
                config = json.loads(config_str)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(config, dict):
                continue
            if "_type" not in config:
                config["_type"] = "CollectionConfigurationInternal"
                updates.append((json.dumps(config), coll_id))
        if updates:
            conn.executemany(
                "UPDATE collections SET config_json_str = ? WHERE id = ?",
                updates,
            )
            conn.commit()
            logger.info(
                "Fixed %d collection(s) missing _type in config_json_str",
                len(updates),
            )
    except Exception:
        logger.exception("Could not fix collection config_json_str in %s", db_path)
        return
    finally:
        conn.close()
    try:
        Path(marker).touch()
    except OSError:
        logger.exception("Could not write migration marker %s", marker)


def _fix_blob_seq_ids(palace_path: str) -> None:
    """Fix ChromaDB 0.6.x -> 1.5.x migration bug: BLOB seq_ids -> INTEGER.

    ChromaDB 0.6.x stored seq_id as big-endian 8-byte BLOBs. ChromaDB 1.5.x
    expects INTEGER. The auto-migration doesn't convert existing rows, causing
    the Rust compactor to crash with "mismatched types; Rust type u64 (as SQL
    type INTEGER) is not compatible with SQL type BLOB".

    Scoped to the ``embeddings`` table only.  The ``max_seq_id`` table is
    left alone because chromadb 1.5.x writes its own BLOB format there.

    Defense-in-depth: rows with the sysdb-10 ``b'\\x11\\x11'`` prefix in
    ``embeddings`` are skipped rather than converted.

    Must run BEFORE ``PersistentClient`` is created (the compactor fires
    on init).

    A marker file is written after migration so subsequent opens skip
    the sqlite3 connection entirely.
    """
    db_path = os.path.join(palace_path, "chroma.sqlite3")
    if not os.path.isfile(db_path):
        return
    marker = os.path.join(palace_path, _BLOB_FIX_MARKER)
    if os.path.isfile(marker):
        return
    try:
        with sqlite3.connect(db_path) as conn:
            try:
                rows = conn.execute(
                    "SELECT rowid, seq_id FROM embeddings WHERE typeof(seq_id) = 'blob'"
                ).fetchall()
            except sqlite3.OperationalError:
                return
            safe_rows = [
                (rowid, blob) for rowid, blob in rows if not blob.startswith(b"\x11\x11")
            ]
            skipped = len(rows) - len(safe_rows)
            if skipped:
                logger.warning(
                    "Skipped %d sysdb-10-format BLOB seq_id(s) in embeddings (not converting)",
                    skipped,
                )
            if safe_rows:
                updates = [
                    (int.from_bytes(blob, byteorder="big"), rowid) for rowid, blob in safe_rows
                ]
                conn.executemany("UPDATE embeddings SET seq_id = ? WHERE rowid = ?", updates)
                logger.info("Fixed %d BLOB seq_ids in embeddings", len(updates))
                conn.commit()
    except Exception:
        logger.exception("Could not fix BLOB seq_ids in %s", db_path)
        return
    # Write marker whether or not rows needed migration -- the palace is now
    # confirmed to be in the INTEGER-seq_id state.
    try:
        Path(marker).touch()
    except OSError:
        logger.exception("Could not write migration marker %s", marker)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def fix_palace_before_open(palace_path: str) -> None:
    """Run all pre-open ChromaDB compatibility repairs.

    Call this **before** every ``chromadb.PersistentClient(path=...)``
    construction.  The individual repairs are idempotent and marker-gated,
    so repeated calls are cheap (stat two marker files, return).

    Steps:
      1. ``_fix_missing_collection_type`` -- adds the ``_type`` marker to
         ``collections.config_json_str`` that chromadb 1.5.9+ requires
         but <= 1.5.8 never wrote.
      2. ``_fix_blob_seq_ids`` -- repairs the BLOB seq_id quirk that bites
         certain chromadb 0.6.x -> 1.5.x migrations.
    """
    try:
        _fix_missing_collection_type(palace_path)
    except Exception:
        logger.exception("_fix_missing_collection_type failed for %s", palace_path)
    try:
        _fix_blob_seq_ids(palace_path)
    except Exception:
        logger.exception("_fix_blob_seq_ids failed for %s", palace_path)


# ---------------------------------------------------------------------------
# FTS5 / SQLite integrity validation (ported from upstream repair.py +
# palace.py #1537)
# ---------------------------------------------------------------------------


class MineValidationError(RuntimeError):
    """Raised at end of mine when PRAGMA quick_check reports errors."""

    def __init__(self, palace_path: str, errors: list[str]) -> None:
        if not errors:
            raise ValueError("MineValidationError requires at least one error string")
        if not palace_path:
            raise ValueError("MineValidationError requires a non-empty palace_path")
        super().__init__(f"FTS5/SQLite quick_check failed: {len(errors)} issue(s)")
        self.palace_path = palace_path
        self.errors: tuple[str, ...] = tuple(errors)


def sqlite_integrity_errors(palace_path: str) -> list[str]:
    """Return SQLite quick_check errors for chroma.sqlite3.

    Runs a direct ``PRAGMA quick_check`` against the palace's SQLite
    database.  Returns an empty list when the palace is healthy, or a
    list of error strings when corruption is detected (e.g. FTS5 shadow
    table corruption, index inconsistency).

    Ported from upstream ``repair.sqlite_integrity_errors``.
    """
    sqlite_path = os.path.join(palace_path, "chroma.sqlite3")
    if not os.path.exists(sqlite_path):
        return []

    try:
        with sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True) as conn:
            rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.Error as e:
        return [f"PRAGMA quick_check failed: {e}"]

    errors: list[str] = []
    for row in rows:
        if not row:
            continue
        message = str(row[0])
        if message.lower() != "ok":
            errors.append(message)

    return errors


def validate_palace_fts5(palace_path: str) -> None:
    """Raise MineValidationError if PRAGMA quick_check reports errors.

    Call this at the end of a mine operation to detect silent FTS5 or
    SQLite corruption before it causes opaque search failures later.

    Ported from upstream ``palace._validate_palace_fts5_after_mine``.
    """
    errors = sqlite_integrity_errors(palace_path)
    if errors:
        raise MineValidationError(palace_path, errors)
