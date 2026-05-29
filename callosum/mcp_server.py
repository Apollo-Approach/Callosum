#!/usr/bin/env python3
"""
Callosum MCP Server -- read/write palace access for Claude Code
================================================================
Install: claude mcp add Callosum -- python -m Callosum.mcp_server

Tools (read):
  Callosum_status          -- total drawers, wing/room breakdown
  Callosum_list_wings      -- all wings with drawer counts
  Callosum_list_rooms      -- rooms within a wing
  Callosum_get_taxonomy    -- full wing -> room -> count tree
  Callosum_search          -- semantic search, optional wing/room filter
  Callosum_check_duplicate -- check if content already exists before filing

Tools (write):
  Callosum_add_drawer      -- file verbatim content into a wing/room
  Callosum_delete_drawer   -- remove a drawer by ID
"""

import sys
import io
import json
import os
import logging
import hashlib
from datetime import datetime

# --- Upstream fix: MCP stdout redirect (Callosum PR #739) ---
# Redirect stdout to stderr BEFORE any library imports.
# ChromaDB and other libs may print() to stdout, which corrupts
# the JSON-RPC channel. We capture the real stdout for protocol
# output, then redirect the default to stderr so library noise
# goes there instead.
_real_stdout = sys.stdout
sys.stdout = sys.stderr

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from .config import CallosumConfig  # noqa: E402
from .version import __version__  # noqa: E402
from .searcher import search_memories  # noqa: E402
from .palace_graph import traverse, find_tunnels, graph_stats  # noqa: E402
import chromadb  # noqa: E402

from .knowledge_graph import KnowledgeGraph  # noqa: E402
from .backlog import Backlog  # noqa: E402
from .blueprints import Blueprints  # noqa: E402
from .staleness import check_stale_drawers, check_engram_drift  # noqa: E402
from .isolation import link_wings, unlink_wings, isolation_report  # noqa: E402

_kg = None
_backlog = None
_blueprints = None


def _get_kg():
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph()
    return _kg


def _get_backlog():
    global _backlog
    if _backlog is None:
        _backlog = Backlog()
    return _backlog


def _get_blueprints():
    global _blueprints
    if _blueprints is None:
        _blueprints = Blueprints()
    return _blueprints


logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("Callosum_mcp")

_config = CallosumConfig()


def _clean(text: str) -> str:
    """Remove lone surrogates that break UTF-8 encoding (issue #1235).

    MCP clients can emit lone surrogates (\udc00-\udfff) when relaying
    binary-in-Unicode or corrupted text. Python's str.encode('utf-8')
    raises UnicodeEncodeError on these; ChromaDB's add() / upsert()
    then crashes with -32000 Internal Error.

    Replace lone surrogates with U+FFFD (REPLACEMENT CHARACTER) so
    the string is legal UTF-8 while preserving as much content as
    possible.
    """
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")


_chroma_client = None
_chroma_collection = None


def _get_collection(create=False):
    """Return the ChromaDB collection, or None on failure."""
    global _chroma_client, _chroma_collection
    try:
        if _chroma_client is None:
            _chroma_client = chromadb.PersistentClient(path=_config.palace_path)
        if create:
            _chroma_collection = _chroma_client.get_or_create_collection(_config.collection_name)
        elif _chroma_collection is None:
            _chroma_collection = _chroma_client.get_collection(_config.collection_name)
        return _chroma_collection
    except Exception:
        return None


def _no_palace():
    return {
        "error": "No palace found",
        "palace_path": _config.palace_path,
        "hint": "Run: Callosum init <dir> && Callosum mine <dir>",
    }


# ==================== READ TOOLS ====================


def tool_status():
    col = _get_collection()
    if not col:
        return _no_palace()
    count = col.count()
    wings = {}
    rooms = {}
    try:
        all_meta = col.get(include=["metadatas"])["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            wings[w] = wings.get(w, 0) + 1
            rooms[r] = rooms.get(r, 0) + 1
    except Exception:
        pass
    return {
        "total_drawers": count,
        "wings": wings,
        "rooms": rooms,
        "palace_path": _config.palace_path,
        "protocol": PALACE_PROTOCOL,
    }


# -- Palace Protocol ----------------------------------------------------------

PALACE_PROTOCOL = """IMPORTANT -- Callosum Memory Protocol:
1. ON WAKE-UP: Call Callosum_status to load palace overview.
2. BEFORE RESPONDING about any person, project, or past event: call Callosum_kg_query or Callosum_search FIRST. Never guess -- verify.
3. IF UNSURE about a fact (name, decision, timeline): say "let me check" and query the palace. Wrong is worse than slow.
4. AFTER EACH SESSION: call Callosum_diary_write to record what happened, what you learned, what matters.
5. WHEN FACTS CHANGE: call Callosum_kg_invalidate on the old fact, Callosum_kg_add for the new one.

This protocol ensures the AI KNOWS before it speaks. Storage is not memory -- but storage + this protocol = memory."""


def tool_list_wings():
    col = _get_collection()
    if not col:
        return _no_palace()
    wings = {}
    try:
        all_meta = col.get(include=["metadatas"])["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            wings[w] = wings.get(w, 0) + 1
    except Exception:
        pass
    return {"wings": wings}


def tool_list_rooms(wing: str = None):
    col = _get_collection()
    if not col:
        return _no_palace()
    rooms = {}
    try:
        kwargs = {"include": ["metadatas"]}
        if wing:
            kwargs["where"] = {"wing": wing}
        all_meta = col.get(**kwargs)["metadatas"]
        for m in all_meta:
            r = m.get("room", "unknown")
            rooms[r] = rooms.get(r, 0) + 1
    except Exception:
        pass
    return {"wing": wing or "all", "rooms": rooms}


def tool_get_taxonomy():
    col = _get_collection()
    if not col:
        return _no_palace()
    taxonomy = {}
    try:
        all_meta = col.get(include=["metadatas"])["metadatas"]
        for m in all_meta:
            w = m.get("wing", "unknown")
            r = m.get("room", "unknown")
            if w not in taxonomy:
                taxonomy[w] = {}
            taxonomy[w][r] = taxonomy[w].get(r, 0) + 1
    except Exception:
        pass
    return {"taxonomy": taxonomy}


def tool_search(query: str, limit: int = 5, wing: str = None, room: str = None):
    if not wing:
        return {
            "error": "Iron Curtain enforced: 'wing' must be explicitly provided for semantic searches to prevent cross-project bleed."
        }
    return search_memories(
        query,
        palace_path=_config.palace_path,
        wing=wing,
        room=room,
        n_results=limit,
    )


def tool_check_duplicate(content: str, threshold: float = 0.9):
    col = _get_collection()
    if not col:
        return _no_palace()
    try:
        results = col.query(
            query_texts=[_clean(content)],
            n_results=5,
            include=["metadatas", "documents", "distances"],
        )
        duplicates = []
        if results["ids"] and results["ids"][0]:
            for i, drawer_id in enumerate(results["ids"][0]):
                dist = results["distances"][0][i]
                similarity = round(1 - dist, 3)
                if similarity >= threshold:
                    meta = results["metadatas"][0][i]
                    doc = results["documents"][0][i]
                    duplicates.append(
                        {
                            "id": drawer_id,
                            "wing": meta.get("wing", "?"),
                            "room": meta.get("room", "?"),
                            "similarity": similarity,
                            "content": doc[:200] + "..." if len(doc) > 200 else doc,
                        }
                    )
        return {
            "is_duplicate": len(duplicates) > 0,
            "matches": duplicates,
        }
    except Exception as e:
        return {"error": str(e)}


def tool_traverse_graph(start_room: str, max_hops: int = 2):
    """Walk the palace graph from a room. Find connected ideas across wings."""
    col = _get_collection()
    if not col:
        return _no_palace()
    return traverse(start_room, col=col, max_hops=max_hops)


def tool_find_tunnels(wing_a: str = None, wing_b: str = None):
    """Find rooms that bridge two wings -- the hallways connecting domains."""
    col = _get_collection()
    if not col:
        return _no_palace()
    return find_tunnels(wing_a, wing_b, col=col)


def tool_graph_stats():
    """Palace graph overview: nodes, tunnels, edges, connectivity."""
    col = _get_collection()
    if not col:
        return _no_palace()
    return graph_stats(col=col)


def tool_list_hallways(wing: str = None):
    """List entity-to-entity connections (hallways) within a wing."""
    from .hallways import list_hallways

    return {"wing": wing or "all", "hallways": list_hallways(wing)}


def tool_compute_hallways(wing: str):
    """Compute and update intra-wing hallways for a specific wing."""
    from .hallways import compute_hallways_for_wing

    col = _get_collection()
    if not col:
        return _no_palace()
    created = compute_hallways_for_wing(wing, col=col)
    return {"success": True, "wing": wing, "hallways_computed": len(created)}


# ==================== WRITE TOOLS ====================


def tool_add_drawer(
    wing: str, room: str, content: str, source_file: str = None, added_by: str = "mcp"
):
    """File verbatim content into a wing/room. Checks for duplicates first. Chunks if oversized."""
    content = _clean(content)
    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    # Duplicate check
    dup = tool_check_duplicate(content, threshold=0.9)
    if dup.get("is_duplicate"):
        return {
            "success": False,
            "reason": "duplicate",
            "matches": dup["matches"],
        }

    drawer_id_base = f"drawer_{wing}_{room}_{hashlib.md5((content[:100] + datetime.now().isoformat()).encode('utf-8', 'surrogatepass')).hexdigest()[:16]}"

    from .miner import chunk_text

    chunks = chunk_text(content, source_file or "mcp_added")
    if not chunks:
        # fallback to single insertion if chunking yielded nothing
        chunks = [{"content": content, "chunk_index": 0}]

    if len(chunks) == 1:
        drawer_id = drawer_id_base
        try:
            col.add(
                ids=[drawer_id],
                documents=[chunks[0]["content"]],
                metadatas=[
                    {
                        "wing": wing,
                        "room": room,
                        "source_file": source_file or "",
                        "chunk_index": 0,
                        "added_by": added_by,
                        "filed_at": datetime.now().isoformat(),
                    }
                ],
            )
            logger.info(f"Filed drawer: {drawer_id} -> {wing}/{room}")
            return {
                "success": True,
                "drawer_id": drawer_id,
                "wing": wing,
                "room": room,
                "chunks": 1,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        # Oversized chunking logic
        batch_docs = []
        batch_ids = []
        batch_metas = []
        for i, c in enumerate(chunks):
            chunk_id = f"{drawer_id_base}_{i}"
            batch_docs.append(c["content"])
            batch_ids.append(chunk_id)
            batch_metas.append(
                {
                    "wing": wing,
                    "room": room,
                    "source_file": source_file or "",
                    "chunk_index": i,
                    "parent_drawer_id": drawer_id_base,
                    "added_by": added_by,
                    "filed_at": datetime.now().isoformat(),
                }
            )
        try:
            col.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            logger.info(
                f"Filed oversized drawer: {drawer_id_base} into {len(chunks)} chunks -> {wing}/{room}"
            )
            return {
                "success": True,
                "drawer_id": drawer_id_base,
                "wing": wing,
                "room": room,
                "chunks": len(chunks),
                "chunk_ids": batch_ids,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


def tool_delete_drawer(drawer_id: str):
    """Delete a single drawer by ID."""
    col = _get_collection()
    if not col:
        return _no_palace()
    existing = col.get(ids=[drawer_id])
    if not existing["ids"]:
        return {"success": False, "error": f"Drawer not found: {drawer_id}"}
    try:
        col.delete(ids=[drawer_id])
        logger.info(f"Deleted drawer: {drawer_id}")
        return {"success": True, "drawer_id": drawer_id}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ==================== KNOWLEDGE GRAPH ====================


def tool_kg_query(entity: str, as_of: str = None, direction: str = "both"):
    """Query the knowledge graph for an entity's relationships."""
    results = _get_kg().query_entity(entity, as_of=as_of, direction=direction)
    return {"entity": entity, "as_of": as_of, "facts": results, "count": len(results)}


def tool_kg_add(
    subject: str,
    predicate: str,
    object: str,
    valid_from: str = None,
    source_closet: str = None,
    source: str = None,
    confidence_score: float = 1.0,
):
    """Add a relationship to the knowledge graph."""
    triple_id = _get_kg().add_triple(
        subject,
        predicate,
        object,
        valid_from=valid_from,
        source_closet=source_closet,
        source=source,
        confidence=confidence_score,
    )
    return {
        "success": True,
        "triple_id": triple_id,
        "fact": f"{subject} -> {predicate} -> {object}",
    }


def tool_kg_invalidate(subject: str, predicate: str, object: str, ended: str = None):
    """Mark a fact as no longer true (set end date)."""
    _get_kg().invalidate(subject, predicate, object, ended=ended)
    return {
        "success": True,
        "fact": f"{subject} -> {predicate} -> {object}",
        "ended": ended or "today",
    }


def tool_kg_timeline(entity: str = None):
    """Get chronological timeline of facts, optionally for one entity."""
    results = _get_kg().timeline(entity)
    return {"entity": entity or "all", "timeline": results, "count": len(results)}


def tool_kg_stats():
    """Knowledge graph overview: entities, triples, relationship types."""
    return _get_kg().stats()


# ==================== AGENT DIARY ====================


def tool_diary_write(agent_name: str, entry: str, topic: str = "general"):
    """
    Write a diary entry for this agent. Each agent gets its own wing
    with a diary room. Entries are timestamped and accumulate over time.

    This is the agent's personal journal -- observations, thoughts,
    what it worked on, what it noticed, what it thinks matters.
    """
    wing = f"wing_{agent_name.lower().replace(' ', '_')}"
    room = "diary"
    entry = _clean(entry)
    col = _get_collection(create=True)
    if not col:
        return _no_palace()

    now = datetime.now()
    entry_id_base = f"diary_{wing}_{now.strftime('%Y%m%d_%H%M%S')}_{hashlib.md5(entry.encode('utf-8', 'surrogatepass')).hexdigest()[:8]}"

    from .miner import chunk_text

    chunks = chunk_text(entry, "diary_write")
    if not chunks:
        chunks = [{"content": entry, "chunk_index": 0}]

    if len(chunks) == 1:
        entry_id = entry_id_base
        try:
            col.add(
                ids=[entry_id],
                documents=[chunks[0]["content"]],
                metadatas=[
                    {
                        "wing": wing,
                        "room": room,
                        "hall": "hall_diary",
                        "topic": topic,
                        "type": "diary_entry",
                        "agent": agent_name,
                        "chunk_index": 0,
                        "filed_at": now.isoformat(),
                        "date": now.strftime("%Y-%m-%d"),
                    }
                ],
            )
            logger.info(f"Diary entry: {entry_id} -> {wing}/diary/{topic}")
            return {
                "success": True,
                "entry_id": entry_id,
                "agent": agent_name,
                "topic": topic,
                "chunks": 1,
                "timestamp": now.isoformat(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        # Oversized chunking logic
        batch_docs = []
        batch_ids = []
        batch_metas = []
        for i, c in enumerate(chunks):
            chunk_id = f"{entry_id_base}_{i}"
            batch_docs.append(c["content"])
            batch_ids.append(chunk_id)
            batch_metas.append(
                {
                    "wing": wing,
                    "room": room,
                    "hall": "hall_diary",
                    "topic": topic,
                    "type": "diary_entry",
                    "agent": agent_name,
                    "chunk_index": i,
                    "parent_entry_id": entry_id_base,
                    "filed_at": now.isoformat(),
                    "date": now.strftime("%Y-%m-%d"),
                }
            )
        try:
            col.add(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            logger.info(
                f"Filed oversized diary entry: {entry_id_base} into {len(chunks)} chunks -> {wing}/diary/{topic}"
            )
            return {
                "success": True,
                "entry_id": entry_id_base,
                "agent": agent_name,
                "topic": topic,
                "chunks": len(chunks),
                "chunk_ids": batch_ids,
                "timestamp": now.isoformat(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}


def tool_diary_read(agent_name: str, last_n: int = 10):
    """
    Read an agent's recent diary entries. Returns the last N entries
    in chronological order -- the agent's personal journal.
    """
    wing = f"wing_{agent_name.lower().replace(' ', '_')}"
    col = _get_collection()
    if not col:
        return _no_palace()

    try:
        results = col.get(
            where={"$and": [{"wing": wing}, {"room": "diary"}]},
            include=["documents", "metadatas"],
        )

        if not results["ids"]:
            return {"agent": agent_name, "entries": [], "message": "No diary entries yet."}

        # Combine and sort by timestamp
        entries = []
        for doc, meta in zip(results["documents"], results["metadatas"]):
            entries.append(
                {
                    "date": meta.get("date", ""),
                    "timestamp": meta.get("filed_at", ""),
                    "topic": meta.get("topic", ""),
                    "content": doc,
                }
            )

        entries.sort(key=lambda x: x["timestamp"], reverse=True)
        entries = entries[:last_n]

        return {
            "agent": agent_name,
            "entries": entries,
            "total": len(results["ids"]),
            "showing": len(entries),
        }
    except Exception as e:
        return {"error": str(e)}


# ==================== BACKLOG ====================


def tool_add_open_loop(wing: str, room: str, title: str, description: str = ""):
    """Add a new open loop to the backlog."""
    loop_id = _get_backlog().add_loop(wing, room, title, description)
    return {"success": True, "loop_id": loop_id, "wing": wing, "room": room}


def tool_get_backlog(wing: str = None, status: str = "open"):
    """Retrieve backlog items."""
    results = _get_backlog().get_backlog(wing=wing, status=status)
    return {"wing": wing or "all", "status": status, "items": results, "count": len(results)}


def tool_resolve_open_loop(loop_id: str):
    """Mark a loop as resolved."""
    success = _get_backlog().resolve_loop(loop_id)
    return {"success": success, "loop_id": loop_id}


# ==================== BLUEPRINTS ====================


def tool_save_blueprint(wing: str, name: str, content: str):
    """Save or overwrite an architectural blueprint."""
    blueprint_id = _get_blueprints().save_blueprint(wing, name, content)
    return {"success": True, "blueprint_id": blueprint_id, "wing": wing, "name": name}


def tool_load_blueprint(wing: str, name: str):
    """Retrieve a specific blueprint."""
    result = _get_blueprints().load_blueprint(wing, name)
    if not result:
        return {"error": "Blueprint not found"}
    return result


def tool_list_blueprints(wing: str = None):
    """List all available blueprints."""
    results = _get_blueprints().list_blueprints(wing=wing)
    return {"wing": wing or "all", "blueprints": results, "count": len(results)}


# ==================== MCP PROTOCOL ====================


def tool_check_stale(**kwargs):
    """Check for stale drawers whose source files have changed."""
    wing = kwargs.get("wing")
    project_dir = kwargs.get("project_dir")
    return check_stale_drawers(_config.palace_path, project_dir=project_dir, wing=wing)


def tool_check_engram_drift(**kwargs):
    """Check if engram reference files have drifted."""
    engram_dir = kwargs.get("engram_dir")
    return check_engram_drift(_config.palace_path, engram_dir=engram_dir)


def tool_link_wings(**kwargs):
    """Create an explicit opt-in tunnel link between two wings."""
    wing_a = kwargs.get("wing_a")
    wing_b = kwargs.get("wing_b")
    reason = kwargs.get("reason", "")
    if not wing_a or not wing_b:
        return {"error": "Both wing_a and wing_b are required"}
    return link_wings(wing_a, wing_b, reason=reason)


def tool_unlink_wings(**kwargs):
    """Revoke a tunnel link between two wings."""
    wing_a = kwargs.get("wing_a")
    wing_b = kwargs.get("wing_b")
    if not wing_a or not wing_b:
        return {"error": "Both wing_a and wing_b are required"}
    return unlink_wings(wing_a, wing_b)


def tool_isolation_report():
    """Show the current wing isolation posture."""
    return isolation_report()


def tool_health_check():
    """Run a comprehensive health check on the Callosum memory engine."""
    from .maintain import health_check

    return health_check(_config.palace_path)


def tool_maintain(**kwargs):
    """Run automated maintenance: stale remediation, GC, coverage check."""
    from .maintain import full_maintain
    import contextlib

    auto_fix = kwargs.get("auto_fix", False)
    # Capture print output since this runs inside MCP
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = full_maintain(
            palace_path=_config.palace_path,
            auto_fix=auto_fix,
            dry_run=False,
        )
    result["log"] = buf.getvalue()
    return result


TOOLS = {
    "Callosum_status": {
        "description": "Palace overview -- total drawers, wing and room counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_status,
    },
    "Callosum_list_wings": {
        "description": "List all wings with drawer counts",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_list_wings,
    },
    "Callosum_list_rooms": {
        "description": "List rooms within a wing (or all rooms if no wing given)",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to list rooms for (optional)"},
            },
        },
        "handler": tool_list_rooms,
    },
    "Callosum_get_taxonomy": {
        "description": "Full taxonomy: wing -> room -> drawer count",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_get_taxonomy,
    },
    "Callosum_kg_query": {
        "description": "Query the knowledge graph for an entity's relationships. Returns typed facts with temporal validity. E.g. 'Max' -> child_of Alice, loves chess, does swimming. Filter by date with as_of to see what was true at a point in time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to query (e.g. 'Max', 'MyProject', 'Alice')",
                },
                "as_of": {
                    "type": "string",
                    "description": "Date filter -- only facts valid at this date (YYYY-MM-DD, optional)",
                },
                "direction": {
                    "type": "string",
                    "description": "outgoing (entity->?), incoming (?->entity), or both (default: both)",
                },
            },
            "required": ["entity"],
        },
        "handler": tool_kg_query,
    },
    "Callosum_kg_add": {
        "description": "Add a fact to the knowledge graph. Subject -> predicate -> object with optional time window. E.g. ('Max', 'started_school', 'Year 7', valid_from='2026-09-01').",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "The entity doing/being something"},
                "predicate": {
                    "type": "string",
                    "description": "The relationship type (e.g. 'loves', 'works_on', 'daughter_of')",
                },
                "object": {"type": "string", "description": "The entity being connected to"},
                "valid_from": {
                    "type": "string",
                    "description": "When this became true (YYYY-MM-DD, optional)",
                },
                "source_closet": {
                    "type": "string",
                    "description": "Closet ID where this fact appears (optional)",
                },
                "source": {
                    "type": "string",
                    "description": "Source of the fact (e.g. 'User via Telegram', 'Source Code Analysis') (optional)",
                },
                "confidence_score": {
                    "type": "number",
                    "description": "Confidence in this fact, 0.0 to 1.0 (default: 1.0)",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_add,
    },
    "Callosum_kg_invalidate": {
        "description": "Mark a fact as no longer true. E.g. ankle injury resolved, job ended, moved house.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Entity"},
                "predicate": {"type": "string", "description": "Relationship"},
                "object": {"type": "string", "description": "Connected entity"},
                "ended": {
                    "type": "string",
                    "description": "When it stopped being true (YYYY-MM-DD, default: today)",
                },
            },
            "required": ["subject", "predicate", "object"],
        },
        "handler": tool_kg_invalidate,
    },
    "Callosum_kg_timeline": {
        "description": "Chronological timeline of facts. Shows the story of an entity (or everything) in order.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity to get timeline for (optional -- omit for full timeline)",
                },
            },
        },
        "handler": tool_kg_timeline,
    },
    "Callosum_kg_stats": {
        "description": "Knowledge graph overview: entities, triples, current vs expired facts, relationship types.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_kg_stats,
    },
    "Callosum_traverse": {
        "description": "Walk the palace graph from a room. Shows connected ideas across wings -- the tunnels. Like following a thread through the palace: start at 'chromadb-setup' in wing_code, discover it connects to wing_myproject (planning) and wing_user (feelings about it).",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_room": {
                    "type": "string",
                    "description": "Room to start from (e.g. 'chromadb-setup', 'riley-school')",
                },
                "max_hops": {
                    "type": "integer",
                    "description": "How many connections to follow (default: 2)",
                },
            },
            "required": ["start_room"],
        },
        "handler": tool_traverse_graph,
    },
    "Callosum_find_tunnels": {
        "description": "Find rooms that bridge two wings -- the hallways connecting different domains. E.g. what topics connect wing_code to wing_team?",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing (optional)"},
                "wing_b": {"type": "string", "description": "Second wing (optional)"},
            },
        },
        "handler": tool_find_tunnels,
    },
    "Callosum_graph_stats": {
        "description": "Palace graph overview: total rooms, tunnel connections, edges between wings.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_graph_stats,
    },
    "Callosum_list_hallways": {
        "description": "List entity-to-entity connections (hallways) within a wing. These reveal within-wing relationships dynamically computed from co-occurrences.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to list hallways for (optional)"},
            },
        },
        "handler": tool_list_hallways,
    },
    "Callosum_compute_hallways": {
        "description": "Compute and update intra-wing hallways (entity connections) for a specific wing. Run this to update the dynamics (potentiation/decay) of entity connections.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing to compute hallways for"},
            },
            "required": ["wing"],
        },
        "handler": tool_compute_hallways,
    },
    "Callosum_search": {
        "description": "Semantic search. Returns verbatim drawer content with similarity scores. To ensure strict isolation between projects (the Iron Curtain), you MUST provide a specific wing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
                "wing": {
                    "type": "string",
                    "description": "Filter by wing to enforce strict project boundaries (Iron Curtain)",
                },
                "room": {"type": "string", "description": "Filter by room (optional)"},
            },
            "required": ["query", "wing"],
        },
        "handler": tool_search,
    },
    "Callosum_check_duplicate": {
        "description": "Check if content already exists in the palace before filing",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to check"},
                "threshold": {
                    "type": "number",
                    "description": "Similarity threshold 0-1 (default 0.9)",
                },
            },
            "required": ["content"],
        },
        "handler": tool_check_duplicate,
    },
    "Callosum_add_drawer": {
        "description": "File verbatim content into the palace. Checks for duplicates first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project name)"},
                "room": {
                    "type": "string",
                    "description": "Room (aspect: backend, decisions, meetings...)",
                },
                "content": {
                    "type": "string",
                    "description": "Verbatim content to store -- exact words, never summarized",
                },
                "source_file": {"type": "string", "description": "Where this came from (optional)"},
                "added_by": {"type": "string", "description": "Who is filing this (default: mcp)"},
            },
            "required": ["wing", "room", "content"],
        },
        "handler": tool_add_drawer,
    },
    "Callosum_delete_drawer": {
        "description": "Delete a drawer by ID. Irreversible.",
        "input_schema": {
            "type": "object",
            "properties": {
                "drawer_id": {"type": "string", "description": "ID of the drawer to delete"},
            },
            "required": ["drawer_id"],
        },
        "handler": tool_delete_drawer,
    },
    "Callosum_diary_write": {
        "description": "Write to your personal agent diary. Your observations, thoughts, what you worked on, what matters. Each agent has their own diary with full history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name -- each agent gets their own diary wing",
                },
                "entry": {
                    "type": "string",
                    "description": "Your diary entry -- observations, decisions, context",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic tag (optional, default: general)",
                },
            },
            "required": ["agent_name", "entry"],
        },
        "handler": tool_diary_write,
    },
    "Callosum_diary_read": {
        "description": "Read your recent diary entries. See what past versions of yourself recorded -- your journal across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_name": {
                    "type": "string",
                    "description": "Your name -- each agent gets their own diary wing",
                },
                "last_n": {
                    "type": "integer",
                    "description": "Number of recent entries to read (default: 10)",
                },
            },
            "required": ["agent_name"],
        },
        "handler": tool_diary_read,
    },
    "Callosum_check_stale": {
        "description": "Check for stale drawers whose source files have changed since last mine. Detects drift.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Optional wing filter"},
                "project_dir": {
                    "type": "string",
                    "description": "Project directory to resolve file paths",
                },
            },
        },
        "handler": tool_check_stale,
    },
    "Callosum_check_engram_drift": {
        "description": "Check if Engram Protocol reference files have drifted from palace. Tier 2 integration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "engram_dir": {
                    "type": "string",
                    "description": "Path to knowledge/ dir (default: ~/.gemini/antigravity/knowledge)",
                },
            },
        },
        "handler": tool_check_engram_drift,
    },
    "Callosum_link_wings": {
        "description": "Create an explicit opt-in tunnel link between two project wings. Required before cross-wing traversal or search can cross the boundary. E.g. link project_a to project_b so they can share compliance context.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing to link"},
                "wing_b": {"type": "string", "description": "Second wing to link"},
                "reason": {
                    "type": "string",
                    "description": "Why these wings should share context (optional)",
                },
            },
            "required": ["wing_a", "wing_b"],
        },
        "handler": tool_link_wings,
    },
    "Callosum_unlink_wings": {
        "description": "Revoke a tunnel link between two wings. Stops cross-wing traversal and search from crossing this boundary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing"},
                "wing_b": {"type": "string", "description": "Second wing"},
            },
            "required": ["wing_a", "wing_b"],
        },
        "handler": tool_unlink_wings,
    },
    "Callosum_isolation_report": {
        "description": "Show the current wing isolation posture: which projects are isolated, which are linked, and why.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_isolation_report,
    },
    "Callosum_health_check": {
        "description": "Comprehensive health check: ChromaDB version, drawer/closet counts, wing isolation, stale files, schedule status.",
        "input_schema": {"type": "object", "properties": {}},
        "handler": tool_health_check,
    },
    "Callosum_maintain": {
        "description": "Run automated maintenance: stale remediation, GC orphaned drawers, closet coverage. Set auto_fix=true to auto-fix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "auto_fix": {
                    "type": "boolean",
                    "description": "Auto-fix stale files and migrate ChromaDB",
                },
            },
        },
        "handler": tool_maintain,
    },
    "Callosum_add_open_loop": {
        "description": "Add a new open loop to the backlog. Use this to defer tasks, bugs, or ideas for later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project) this loop belongs to"},
                "room": {"type": "string", "description": "Room (aspect) this loop belongs to"},
                "title": {"type": "string", "description": "Short title of the task/loop"},
                "description": {
                    "type": "string",
                    "description": "Detailed description of the task/loop (optional)",
                },
            },
            "required": ["wing", "room", "title"],
        },
        "handler": tool_add_open_loop,
    },
    "Callosum_get_backlog": {
        "description": "Retrieve backlog items to see what was deferred or left open.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Optional wing filter"},
                "status": {
                    "type": "string",
                    "description": "Status to filter by ('open', 'resolved', 'all') (default: 'open')",
                },
            },
        },
        "handler": tool_get_backlog,
    },
    "Callosum_resolve_open_loop": {
        "description": "Mark an open loop as resolved.",
        "input_schema": {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string", "description": "ID of the loop to resolve"},
            },
            "required": ["loop_id"],
        },
        "handler": tool_resolve_open_loop,
    },
    "Callosum_save_blueprint": {
        "description": "Save or overwrite an architectural blueprint (system map, topology). Payload can be arbitrary Markdown or JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {
                    "type": "string",
                    "description": "Wing (project) this blueprint belongs to",
                },
                "name": {
                    "type": "string",
                    "description": "Name of the blueprint (e.g. 'auth_flow', 'database_schema')",
                },
                "content": {
                    "type": "string",
                    "description": "The payload (Markdown, JSON, diagram)",
                },
            },
            "required": ["wing", "name", "content"],
        },
        "handler": tool_save_blueprint,
    },
    "Callosum_load_blueprint": {
        "description": "Retrieve a specific architectural blueprint.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing (project)"},
                "name": {"type": "string", "description": "Name of the blueprint"},
            },
            "required": ["wing", "name"],
        },
        "handler": tool_load_blueprint,
    },
    "Callosum_list_blueprints": {
        "description": "List all available architectural blueprints.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Optional wing filter"},
            },
        },
        "handler": tool_list_blueprints,
    },
}


def handle_request(request):
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Callosum", "version": __version__},
            },
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {"name": n, "description": t["description"], "inputSchema": t["input_schema"]}
                    for n, t in TOOLS.items()
                ]
            },
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        # Coerce argument types based on input_schema.
        # MCP JSON transport may deliver integers as floats or strings;
        # ChromaDB and Python slicing require native int.
        schema_props = TOOLS[tool_name]["input_schema"].get("properties", {})
        for key, value in list(tool_args.items()):
            prop_schema = schema_props.get(key, {})
            declared_type = prop_schema.get("type")
            if declared_type == "integer" and not isinstance(value, int):
                tool_args[key] = int(value)
            elif declared_type == "number" and not isinstance(value, (int, float)):
                tool_args[key] = float(value)
        try:
            result = TOOLS[tool_name]["handler"](**tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
            }
        except Exception as e:
            logger.error(f"Tool error in {tool_name}: {e}")
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    logger.info("Callosum MCP Server starting...")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                _real_stdout.write(json.dumps(response) + "\n")
                _real_stdout.flush()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Server error: {e}")


if __name__ == "__main__":
    main()
