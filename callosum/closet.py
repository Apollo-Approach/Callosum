"""
closet.py - Compact topic-pointer index layer for Callosum.

Ported from Callosum v3.3.1 palace.py, adapted for Callosum's
isolated wing architecture.

Closets are the index. Drawers hold verbatim content.
Closets hold compact pointer lines:

    topic description|entity1;entity2|->drawer_id_1,drawer_id_2

Search hits the closet first (fast scan of short text), then opens
the referenced drawers for full verbatim content.

Key difference from upstream: closets enforce wing isolation.
Each closet carries its wing in metadata and search never crosses
wing boundaries unless explicitly requested via cross-wing tunnels.
"""

from __future__ import annotations

import re
from pathlib import Path

import chromadb

# -- Constants (upstream v3.3.1) ----------------------------------------

CLOSET_CHAR_LIMIT = 1500  # max chars per closet document
CLOSET_EXTRACT_WINDOW = 5000  # chars of source content to scan
MAX_TOPICS_PER_FILE = 12
MAX_QUOTES_PER_FILE = 3
MAX_ENTITIES_PER_POINTER = 5

# Entity words to ignore (common English stopwords + AI conversation noise)
_ENTITY_STOPLIST = frozenset(
    {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "When",
        "Where",
        "What",
        "Why",
        "Who",
        "Which",
        "How",
        "After",
        "Before",
        "Then",
        "Now",
        "Here",
        "There",
        "And",
        "But",
        "Or",
        "Yet",
        "So",
        "If",
        "Else",
        "Yes",
        "No",
        "Maybe",
        "Okay",
        "User",
        "Assistant",
        "System",
        "Tool",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
        "None",
        "True",
        "False",
        "TODO",
        "FIXME",
        "NOTE",
        "Import",
        "Return",
        "Class",
        "Function",
    }
)


# -- Collection access ---------------------------------------------------


def get_closets_collection(palace_path: str, create: bool = True):
    """Get the closets ChromaDB collection."""
    client = chromadb.PersistentClient(path=palace_path)
    if create:
        return client.get_or_create_collection("callosum_closets")
    return client.get_collection("callosum_closets")


# -- Entity extraction ---------------------------------------------------

_CAPITALIZED_WORD_RE = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
_CODE_IDENT_RE = re.compile(r"\b(?:[A-Z][a-z]+){2,}\b")  # CamelCase


def _extract_entities(text: str) -> list[str]:
    """Extract entity candidates from text.

    Finds capitalized words and CamelCase identifiers that appear
    2+ times. Filters through stoplist.
    """
    candidates = _CAPITALIZED_WORD_RE.findall(text)
    candidates.extend(_CODE_IDENT_RE.findall(text))

    freq: dict[str, int] = {}
    for w in candidates:
        if w in _ENTITY_STOPLIST:
            continue
        if len(w) < 3:
            continue
        freq[w] = freq.get(w, 0) + 1

    # Keep entities with 2+ occurrences, sorted by frequency
    entities = sorted(
        [w for w, c in freq.items() if c >= 2],
        key=lambda w: -freq[w],
    )
    return entities[:MAX_ENTITIES_PER_POINTER]


# -- Topic extraction ----------------------------------------------------

# Action verb patterns that indicate meaningful topics
_TOPIC_PATTERN = re.compile(
    r"(?:built|fixed|wrote|added|pushed|tested|created|decided|migrated|"
    r"reviewed|deployed|configured|removed|updated|implemented|refactored|"
    r"extracted|imported|exported|parsed|validated|computed|calculated|"
    r"connected|integrated|hardened|secured|encrypted|sanitized)\s+"
    r"[\w\s]{3,40}",
    re.IGNORECASE,
)

_HEADER_PATTERN = re.compile(r"^#{1,3}\s+(.{5,60})$", re.MULTILINE)
_QUOTE_PATTERN = re.compile(r'"([^"]{15,150})"')


def build_closet_lines(
    source_file: str,
    drawer_ids: list[str],
    content: str,
    wing: str,
    room: str,
) -> list[str]:
    """Build compact closet pointer lines from drawer content.

    Returns a list of lines. Each line is one atomic topic pointer,
    never split across closets.

    Format: topic|entities|->drawer_ids
    """
    drawer_ref = ",".join(drawer_ids[:3])
    window = content[:CLOSET_EXTRACT_WINDOW]

    # Extract entities
    entities = _extract_entities(window)
    entity_str = ";".join(entities) if entities else ""

    # Extract topics from action verbs
    topics = _TOPIC_PATTERN.findall(window)

    # Also grab section headers
    for header in _HEADER_PATTERN.findall(window):
        topics.append(header.strip())

    # Dedupe preserving order
    topics = list(dict.fromkeys(t.strip().lower() for t in topics))[:MAX_TOPICS_PER_FILE]

    # Extract quotes
    quotes = _QUOTE_PATTERN.findall(window)

    # Build pointer lines
    lines = []
    for topic in topics:
        lines.append(f"{topic}|{entity_str}|->{drawer_ref}")
    for quote in quotes[:MAX_QUOTES_PER_FILE]:
        lines.append(f'"{quote}"|{entity_str}|->{drawer_ref}')

    # Always have at least one line
    if not lines:
        name = Path(source_file).stem[:40]
        lines.append(f"{wing}/{room}/{name}|{entity_str}|->{drawer_ref}")

    return lines


# -- Closet CRUD ---------------------------------------------------------


def purge_file_closets(closets_col, source_file: str) -> None:
    """Delete every closet for a source file before rebuild.

    Call this before upsert_closet_lines on a re-mine so stale topics
    from a prior mine don't leak.
    """
    try:
        closets_col.delete(where={"source_file": source_file})
    except Exception:
        pass


def upsert_closet_lines(
    closets_col,
    closet_id_base: str,
    lines: list[str],
    metadata: dict,
) -> int:
    """Write topic lines to closets, packed greedily.

    Lines are never split across closets. If adding a line would exceed
    CLOSET_CHAR_LIMIT, a new closet is started.

    Returns the number of closets written.
    """
    closet_num = 1
    current_lines: list[str] = []
    current_chars = 0
    closets_written = 0

    def _flush():
        nonlocal closets_written, closet_num
        if not current_lines:
            return
        closet_id = f"{closet_id_base}_{closet_num:02d}"
        text = "\n".join(current_lines)
        closets_col.upsert(documents=[text], ids=[closet_id], metadatas=[metadata])
        closets_written += 1

    for line in lines:
        line_len = len(line)
        if current_chars > 0 and current_chars + line_len + 1 > CLOSET_CHAR_LIMIT:
            _flush()
            closet_num += 1
            current_lines = []
            current_chars = 0

        current_lines.append(line)
        current_chars += line_len + 1

    _flush()
    return closets_written


# -- Closet search -------------------------------------------------------

_DRAWER_PTR_RE = re.compile(r"->([a-zA-Z0-9_,]+)")


def _extract_drawer_ids_from_closet(closet_text: str) -> list[str]:
    """Parse ->drawer_a,drawer_b pointers from a closet document."""
    ids = []
    for match in _DRAWER_PTR_RE.findall(closet_text):
        ids.extend(match.split(","))
    return list(dict.fromkeys(ids))  # dedupe preserving order


def closet_search(
    query: str,
    palace_path: str,
    wing: str = None,
    room: str = None,
    n_results: int = 5,
    max_distance: float = 1.5,
) -> list[dict] | None:
    """Search closets first, then hydrate matching drawers.

    Returns list of hit dicts, or None to signal the caller should
    fall back to direct drawer search (e.g., no closets exist).

    Each hit has:
        text: verbatim drawer content
        wing, room, source_file, similarity: metadata
        matched_via: "closet"
        closet_preview: the closet line that surfaced this hit
    """
    try:
        closets_col = get_closets_collection(palace_path, create=False)
    except Exception:
        return None  # No closets collection -> fallback

    if closets_col.count() == 0:
        return None  # Empty -> fallback

    # Build wing/room filter (isolation boundary)
    where = {}
    if wing and room:
        where = {"$and": [{"wing": wing}, {"room": room}]}
    elif wing:
        where = {"wing": wing}
    elif room:
        where = {"room": room}

    kwargs = {
        "query_texts": [query],
        "n_results": min(n_results * 2, closets_col.count()),
        "include": ["documents", "metadatas", "distances"],
    }
    if where:
        kwargs["where"] = where

    try:
        results = closets_col.query(**kwargs)
    except Exception:
        return None

    closet_docs = results["documents"][0]
    closet_metas = results["metadatas"][0]
    closet_dists = results["distances"][0]

    if not closet_docs:
        return None

    # Collect drawer IDs from closet hits, respecting max_distance
    drawer_ids_to_fetch = []
    closet_previews = {}  # drawer_id -> closet preview line

    for doc, meta, dist in zip(closet_docs, closet_metas, closet_dists):
        if dist > max_distance:
            continue
        ids = _extract_drawer_ids_from_closet(doc)
        for did in ids:
            if did not in closet_previews:
                # Store a preview: first line that contains -> this drawer
                for line in doc.split("\n"):
                    if did in line:
                        closet_previews[did] = line.split("|")[0][:80]
                        break
                drawer_ids_to_fetch.append(did)

    if not drawer_ids_to_fetch:
        return None

    # Hydrate drawers
    try:
        client = chromadb.PersistentClient(path=palace_path)
        drawers_col = client.get_collection("callosum_drawers")
        hydrated = drawers_col.get(
            ids=drawer_ids_to_fetch[: n_results * 2],
            include=["documents", "metadatas"],
        )
    except Exception:
        return None

    hits = []
    for doc, meta, did in zip(
        hydrated["documents"],
        hydrated["metadatas"],
        hydrated["ids"],
    ):
        hits.append(
            {
                "text": doc,
                "wing": meta.get("wing", "unknown"),
                "room": meta.get("room", "unknown"),
                "source_file": Path(meta.get("source_file", "?")).name,
                "similarity": 0.0,  # closet-matched, not vector-scored
                "matched_via": "closet",
                "closet_preview": closet_previews.get(did, ""),
            }
        )

    return hits[:n_results] if hits else None
