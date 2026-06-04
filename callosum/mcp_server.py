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
import re
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
from .chroma_compat import fix_palace_before_open  # noqa: E402

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


_LONE_SURROGATE_RE = re.compile(r"[\ud800-\udfff]")


def _clean(text: str) -> str:
    """Remove lone surrogates that break UTF-8 encoding (issue #1235).

    MCP clients can emit lone surrogates (\udc00-\udfff) when relaying
    binary-in-Unicode or corrupted text. Python's str.encode('utf-8')
    raises UnicodeEncodeError on these; ChromaDB's add() / upsert()
    then crashes with -32000 Internal Error.

    Uses a pre-compiled regex (ported from upstream) for faster repeated
    calls vs the previous encode/decode roundtrip.
    """
    return _LONE_SURROGATE_RE.sub("\ufffd", text)


_chroma_client = None
_chroma_collection = None


def _get_collection(create=False):
    """Return the ChromaDB collection, or None on failure."""
    global _chroma_client, _chroma_collection
    try:
        if _chroma_client is None:
            fix_palace_before_open(_config.palace_path)
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



def dispatch_taxonomy(action: str, wing: str = None):
    if action == "status":
        return tool_status()
    elif action == "list_wings":
        return tool_list_wings()
    elif action == "list_rooms":
        return tool_list_rooms(wing)
    elif action == "get_taxonomy":
        return tool_get_taxonomy()
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_kg(action: str, **kwargs):
    if action == "query":
        return tool_kg_query(**kwargs)
    elif action == "add":
        return tool_kg_add(**kwargs)
    elif action == "invalidate":
        return tool_kg_invalidate(**kwargs)
    elif action == "timeline":
        return tool_kg_timeline(**kwargs)
    elif action == "stats":
        return tool_kg_stats()
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_graph(action: str, **kwargs):
    if action == "traverse":
        return tool_traverse_graph(**kwargs)
    elif action == "find_tunnels":
        return tool_find_tunnels(**kwargs)
    elif action == "graph_stats":
        return tool_graph_stats()
    elif action == "list_hallways":
        return tool_list_hallways(**kwargs)
    elif action == "compute_hallways":
        return tool_compute_hallways(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_drawer(action: str, **kwargs):
    if action == "add":
        return tool_add_drawer(**kwargs)
    elif action == "delete":
        return tool_delete_drawer(**kwargs)
    elif action == "check_duplicate":
        return tool_check_duplicate(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_diary(action: str, **kwargs):
    if action == "read":
        return tool_diary_read(**kwargs)
    elif action == "write":
        return tool_diary_write(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_open_loops(action: str, **kwargs):
    if action == "add":
        return tool_add_open_loop(**kwargs)
    elif action == "get":
        return tool_get_backlog(**kwargs)
    elif action == "resolve":
        return tool_resolve_open_loop(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_blueprints(action: str, **kwargs):
    if action == "save":
        return tool_save_blueprint(**kwargs)
    elif action == "load":
        return tool_load_blueprint(**kwargs)
    elif action == "list":
        return tool_list_blueprints(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

def dispatch_admin(action: str, **kwargs):
    if action == "check_stale":
        return tool_check_stale(**kwargs)
    elif action == "check_engram_drift":
        return tool_check_engram_drift(**kwargs)
    elif action == "link_wings":
        return tool_link_wings(**kwargs)
    elif action == "unlink_wings":
        return tool_unlink_wings(**kwargs)
    elif action == "isolation_report":
        return tool_isolation_report()
    elif action == "health_check":
        return tool_health_check()
    elif action == "maintain":
        return tool_maintain(**kwargs)
    else:
        raise ValueError(f"Unknown action: {action}")

TOOLS = {
    "Callosum_search": {
        "description": "Semantic search. Returns verbatim drawer content with similarity scores. MUST provide a specific wing for project isolation (Iron Curtain).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
                "wing": {"type": "string", "description": "Filter by wing"},
                "room": {"type": "string", "description": "Filter by room (optional)"},
            },
            "required": ["query", "wing"],
        },
        "handler": tool_search,
    },
    "Callosum_taxonomy": {
        "description": "Palace taxonomy & status. Actions: status (overview), list_wings, list_rooms, get_taxonomy (full tree).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "list_wings", "list_rooms", "get_taxonomy"]},
                "wing": {"type": "string", "description": "Wing to filter on (for list_rooms)"}
            },
            "required": ["action"]
        },
        "handler": dispatch_taxonomy,
    },
    "Callosum_kg": {
        "description": "Knowledge graph operations. Actions: query (entity relationships), add (add a fact), invalidate (end a fact), timeline (chronological story), stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["query", "add", "invalidate", "timeline", "stats"]},
                "entity": {"type": "string"},
                "subject": {"type": "string"},
                "predicate": {"type": "string"},
                "object": {"type": "string"},
                "as_of": {"type": "string"},
                "direction": {"type": "string"},
                "valid_from": {"type": "string"},
                "source_closet": {"type": "string"},
                "source": {"type": "string"},
                "confidence_score": {"type": "number"},
                "ended": {"type": "string"}
            },
            "required": ["action"]
        },
        "handler": dispatch_kg,
    },
    "Callosum_graph": {
        "description": "Graph & tunnel operations. Actions: traverse (walk connections), find_tunnels (bridges between wings), graph_stats, list_hallways, compute_hallways.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["traverse", "find_tunnels", "graph_stats", "list_hallways", "compute_hallways"]},
                "start_room": {"type": "string"},
                "max_hops": {"type": "integer"},
                "wing": {"type": "string"},
                "wing_a": {"type": "string"},
                "wing_b": {"type": "string"}
            },
            "required": ["action"]
        },
        "handler": dispatch_graph,
    },
    "Callosum_drawer": {
        "description": "Drawer operations. Actions: add (file content), delete (remove by ID), check_duplicate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "delete", "check_duplicate"]},
                "wing": {"type": "string"},
                "room": {"type": "string"},
                "content": {"type": "string"},
                "source_file": {"type": "string"},
                "added_by": {"type": "string"},
                "drawer_id": {"type": "string"},
                "threshold": {"type": "number"}
            },
            "required": ["action"]
        },
        "handler": dispatch_drawer,
    },
    "Callosum_diary": {
        "description": "Agent diary operations. Actions: read (recent entries), write (add entry).",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write"]},
                "agent_name": {"type": "string"},
                "entry": {"type": "string"},
                "topic": {"type": "string"},
                "last_n": {"type": "integer"}
            },
            "required": ["action", "agent_name"]
        },
        "handler": dispatch_diary,
    },
    "Callosum_open_loops": {
        "description": "Task tracking. Actions: add (new task), get (view backlog), resolve.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "get", "resolve"]},
                "wing": {"type": "string"},
                "room": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "status": {"type": "string"},
                "loop_id": {"type": "string"}
            },
            "required": ["action"]
        },
        "handler": dispatch_open_loops,
    },
    "Callosum_blueprints": {
        "description": "Architectural blueprints. Actions: save, load, list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["save", "load", "list"]},
                "wing": {"type": "string"},
                "name": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["action"]
        },
        "handler": dispatch_blueprints,
    },
    "Callosum_admin": {
        "description": "System administration. Actions: check_stale, check_engram_drift, link_wings, unlink_wings, isolation_report, health_check, maintain.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["check_stale", "check_engram_drift", "link_wings", "unlink_wings", "isolation_report", "health_check", "maintain"]},
                "wing": {"type": "string"},
                "project_dir": {"type": "string"},
                "engram_dir": {"type": "string"},
                "wing_a": {"type": "string"},
                "wing_b": {"type": "string"},
                "reason": {"type": "string"},
                "auto_fix": {"type": "boolean"}
            },
            "required": ["action"]
        },
        "handler": dispatch_admin,
    }
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
