#!/usr/bin/env python3
"""
antigravity_miner.py -- Mine Antigravity/Gemini CLI artifacts into the palace.

Understands the Antigravity brain directory format:
    ~/.gemini/antigravity/brain/<conversation-id>/
        *.md                    -> Artifact content (implementation plans, walkthroughs, etc.)
        *.md.metadata.json      -> Artifact metadata (type, summary)
        *.md.resolved.*         -> Version history (every edit over time)
        .system_generated/
            steps/*/content.md  -> Tool outputs (URL fetches, research)
        *.png, *.webp, *.img   -> Media (skipped)

This is a novel miner -- no upstream equivalent exists. It's designed for users
who work across multiple AI models (Claude, Gemini) via the Gemini CLI / Antigravity
interface and want their artifact history searchable.
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import ssl
import urllib.request
import urllib.error

os.environ["ANONYMIZED_TELEMETRY"] = "False"
import chromadb
from .config import CallosumConfig

# File types we can mine
ARTIFACT_EXTENSIONS = {".md", ".txt"}

# File types to skip
SKIP_EXTENSIONS = {".png", ".webp", ".img", ".jpg", ".jpeg", ".gif", ".mp4", ".webm"}

# Files that are system noise, not useful content
SKIP_FILENAMES = {"knowledge.lock"}

# Minimum content length to bother indexing
MIN_CONTENT_LENGTH = 50


def get_collection(palace_path: str):
    """Get or create the callosum_drawers collection."""
    os.makedirs(palace_path, exist_ok=True)
    client = chromadb.PersistentClient(path=palace_path)
    try:
        return client.get_collection("callosum_drawers")
    except Exception:
        return client.create_collection("callosum_drawers")


def drawer_exists(collection, drawer_id: str) -> bool:
    """Check if a drawer already exists in the collection."""
    try:
        results = collection.get(ids=[drawer_id])
        return len(results.get("ids", [])) > 0
    except Exception:
        return False


def compute_file_hash(content: str) -> str:
    """Compute SHA-256 hash of file content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def file_needs_update(collection, source_file: str, content_hash: str) -> str:
    """Check if a file needs mining."""
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


def read_artifact_metadata(metadata_path: Path) -> dict:
    """Read the .metadata.json file for an artifact."""
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def chunk_content(content: str, max_chunk_size: int = 2000) -> list:
    """Split content into semantic chunks by paragraph, with size limits."""
    chunks = []
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]

    current_chunk = []
    current_size = 0

    for para in paragraphs:
        para_size = len(para)

        # If adding this paragraph would exceed the limit, flush current chunk
        if current_size + para_size > max_chunk_size and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_size = 0

        current_chunk.append(para)
        current_size += para_size

    # Don't forget the last chunk
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def detect_wing_from_content(content: str, filename: str) -> str:
    """Auto-detect which project wing this artifact belongs to based on content."""
    content_lower = content[:5000].lower()
    filename_lower = filename.lower()

    config = CallosumConfig()
    wing_keywords = config.build_dynamic_wing_keywords()

    if not wing_keywords:
        # Fallback keyword scoring for wing detection
        wing_keywords = {
            "project_alpha": ["project-alpha", "project alpha", "advisor", "automation", "tax"],
            "project_beta": ["project-beta", "fleet", "mobile", "forensic"],
            "project_gamma": ["project-gamma", "verification", "mobile", "identity"],
            "apollo_approach": [
                "apollo approach",
                "incorporation",
                "hst",
                "nuans",
                "business centre",
                "seminar",
                "grant",
                "starter company",
            ],
            "infra": ["server1", "192.168.1.100", "docker", "ollama", "localhost"],
            "callosum": ["callosum", "callosum", "memory palace", "chromadb"],
        }

    scores = {}
    for wing, keywords in wing_keywords.items():
        score = sum(1 for kw in keywords if kw in content_lower or kw in filename_lower)
        if score > 0:
            scores[wing] = score

    if scores:
        best_wing = max(scores, key=scores.get)
        return f"wing_{best_wing}"

    if config.smart_routing_enabled:
        llm_wing = _ollama_classify_wing(content_lower, wing_keywords, config)
        if llm_wing:
            return llm_wing

    return "wing_general"


def _ollama_classify_wing(content: str, wing_keywords: dict, config: CallosumConfig) -> str | None:
    """Uses LLM to smartly classify unknown content into a wing."""
    # Pros/cons addressed: llama.cpp natively supports the OpenAI /v1/chat/completions standard.
    # We switch to this format to ensure maximum compatibility.
    endpoint = config.ollama_endpoint.strip("/")
    if not endpoint.endswith("/v1/chat/completions"):
        if endpoint.endswith("/api/generate"):
            endpoint = endpoint.replace("/api/generate", "/v1/chat/completions")
        elif not endpoint.endswith("/v1") and not endpoint.endswith("/v1/"):
            endpoint = endpoint + "/v1/chat/completions"

    model = config.smart_routing_model

    # Extract keys
    known_projects = list(wing_keywords.keys())
    wings_str = ", ".join(known_projects)

    # Build keyword hints
    hints = []
    for project, kws in wing_keywords.items():
        hints.append(f"- {project}: {', '.join(kws)}")
    hints_str = "\n".join(hints)

    system_prompt = (
        "You are a strict data classification tool. You must output EXACTLY ONE WORD from the 'Possible projects' list that best describes the content. "
        "NEVER output conversational text, greetings, phrases, or explanations. If you are unsure, output 'general'."
    )
    prompt = (
        f"You must assign the following content to one of these projects: {wings_str}.\n\n"
        f"Context hints for projects based on keywords:\n"
        f"{hints_str}\n\n"
        f"Content:\n{content[:2000]}\n\n"
        f"Reply with EXACTLY ONE word from the list of possible projects, and nothing else. Do not include any other text or explanation."
    )

    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 10,
    }

    req = urllib.request.Request(endpoint, data=json.dumps(data).encode("utf-8"))
    req.add_header("Content-Type", "application/json")

    # Build SSL context if CA cert is configured (for LlamaQ HTTPS endpoints)
    ssl_ctx = None
    ca_cert = config.ollama_ca_cert
    if ca_cert and os.path.exists(ca_cert):
        ssl_ctx = ssl.create_default_context(cafile=ca_cert)

    try:
        # Use a generous timeout for the local inference server (120s for model loading)
        with urllib.request.urlopen(req, timeout=120.0, context=ssl_ctx) as response:
            result = json.loads(response.read().decode("utf-8"))
            response_text = (
                result.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
            )

            # Iron Curtain Fix: Enforce exact word boundaries or equality to prevent subset project bleed
            # e.g., 'call' shouldn't match 'callosum'
            clean_response = response_text.replace("_", "").replace("-", "")
            for wing in known_projects:
                clean_wing = wing.lower().replace("_", "").replace("-", "")
                if clean_wing == clean_response:
                    return f"wing_{wing.replace(' ', '_')}"
            return None
    except Exception as e:
        print(f"  \033[0;31m[Smart Routing Error]\033[0m LLM classification failed: {e}")
        return None


def detect_room_from_artifact(metadata: dict, filename: str) -> str:
    """Detect room from artifact type metadata or filename patterns."""
    artifact_type = metadata.get("ArtifactType", "")

    type_to_room = {
        "implementation_plan": "planning",
        "walkthrough": "decisions",
        "task": "tasks",
        "other": "general",
    }

    if artifact_type in type_to_room:
        return type_to_room[artifact_type]

    # Filename-based detection
    filename_lower = filename.lower()
    if "security" in filename_lower:
        return "security"
    if "benchmark" in filename_lower or "test" in filename_lower:
        return "testing"
    if "tax" in filename_lower or "accounting" in filename_lower or "invoice" in filename_lower:
        return "finance"
    if "seminar" in filename_lower or "meeting" in filename_lower:
        return "meetings"

    return "general"


def scan_brain_directory(brain_path: Path) -> dict:
    """Scan the Antigravity brain directory and catalog all mineable content.

    Returns a dict of conversation_id -> list of mineable items.
    """
    catalog = {}

    if not brain_path.exists():
        return catalog

    for conversation_dir in brain_path.iterdir():
        if not conversation_dir.is_dir():
            continue
        if conversation_dir.name in {"tempmediaStorage", ".tempmediaStorage"}:
            continue

        conversation_id = conversation_dir.name
        items = []

        # 1. Scan top-level artifacts (*.md files, not metadata or resolved versions)
        for filepath in conversation_dir.iterdir():
            if not filepath.is_file():
                continue
            if filepath.name in SKIP_FILENAMES:
                continue
            if filepath.suffix.lower() in SKIP_EXTENSIONS:
                continue
            if ".metadata.json" in filepath.name:
                continue
            if ".resolved" in filepath.name:
                continue  # We'll handle resolved versions separately

            if filepath.suffix.lower() in ARTIFACT_EXTENSIONS:
                # Check for metadata
                metadata_path = filepath.parent / f"{filepath.name}.metadata.json"
                metadata = read_artifact_metadata(metadata_path) if metadata_path.exists() else {}

                # Count version history
                versions = sorted(
                    filepath.parent.glob(f"{filepath.name}.resolved.*"),
                    key=lambda p: _version_sort_key(p.name),
                )

                items.append(
                    {
                        "type": "artifact",
                        "path": filepath,
                        "metadata": metadata,
                        "versions": versions,
                        "conversation_id": conversation_id,
                    }
                )

        # 2. Scan step outputs (tool results, URL fetches)
        steps_dir = conversation_dir / ".system_generated" / "steps"
        if steps_dir.exists():
            for step_dir in steps_dir.iterdir():
                if not step_dir.is_dir():
                    continue
                content_file = step_dir / "content.md"
                if content_file.exists():
                    items.append(
                        {
                            "type": "step",
                            "path": content_file,
                            "metadata": {},
                            "versions": [],
                            "conversation_id": conversation_id,
                            "step_id": step_dir.name,
                        }
                    )

        if items:
            catalog[conversation_id] = items

    return catalog


def _version_sort_key(filename: str) -> int:
    """Extract version number from a .resolved.N filename for sorting."""
    try:
        return int(filename.rsplit(".", 1)[-1])
    except ValueError:
        return -1


def mine_antigravity(
    brain_path: str,
    palace_path: str,
    wing: str = None,
    agent: str = "callosum",
    limit: int = 0,
    dry_run: bool = False,
    include_versions: bool = False,
    include_steps: bool = True,
):
    """Mine Antigravity brain directory into the palace.

    Args:
        brain_path: Path to ~/.gemini/antigravity/brain/
        palace_path: Path to the palace data directory
        wing: Override wing name (default: auto-detect per artifact)
        agent: Agent name to tag drawers with
        limit: Max items to process (0 = all)
        dry_run: Preview without filing
        include_versions: Also mine .resolved.* version history
        include_steps: Also mine step outputs (tool results)
    """
    brain_dir = Path(brain_path).expanduser().resolve()

    print(f"\n{'=' * 55}")
    print("  Callosum Mine -- Antigravity Artifacts")
    print(f"{'=' * 55}")
    print(f"  Source:  {brain_dir}")
    print(f"  Palace:  {palace_path}")
    if wing:
        print(f"  Wing:    {wing} (override)")
    else:
        print("  Wing:    auto-detect")
    if dry_run:
        print("  DRY RUN -- nothing will be filed")
    print(f"{'-' * 55}\n")

    # Scan and catalog
    catalog = scan_brain_directory(brain_dir)

    if not catalog:
        print("  No conversations found in brain directory.")
        return

    total_conversations = len(catalog)
    total_items = sum(len(items) for items in catalog.values())
    print(f"  Found {total_conversations} conversations, {total_items} artifacts\n")

    collection = get_collection(palace_path) if not dry_run else None

    total_drawers = 0
    skipped = 0
    wing_counts = defaultdict(int)
    room_counts = defaultdict(int)

    items_processed = 0
    for conversation_id, items in catalog.items():
        for item in items:
            if limit > 0 and items_processed >= limit:
                break

            filepath = item["path"]
            metadata = item["metadata"]

            # Skip steps if not requested
            if item["type"] == "step" and not include_steps:
                continue

            # Read content
            try:
                content = filepath.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            if len(content.strip()) < MIN_CONTENT_LENGTH:
                continue

            # Detect wing and room
            item_wing = wing or detect_wing_from_content(content, filepath.name)
            item_room = detect_room_from_artifact(metadata, filepath.name)

            source_file = str(filepath)
            content_hash = compute_file_hash(content)

            status = "new" if dry_run else file_needs_update(collection, source_file, content_hash)

            # Chunk the content
            chunks = chunk_content(content)

            if status == "unchanged":
                skipped += len(chunks)
                items_processed += 1
                continue
            elif status == "changed" and not dry_run:
                delete_drawers_for_file(collection, source_file)

            if dry_run:
                artifact_type = metadata.get("ArtifactType", "unknown")
                summary = metadata.get("Summary", "")[:80]
                version_count = len(item.get("versions", []))
                print(f"    [{item_wing}/{item_room}] {filepath.name}")
                print(
                    f"      Type: {artifact_type} | Chunks: {len(chunks)} | Versions: {version_count}"
                )
                if summary:
                    print(f"      Summary: {summary}...")
                total_drawers += len(chunks)
                wing_counts[item_wing] += 1
                room_counts[item_room] += len(chunks)
                items_processed += 1
                continue

            # Batch file chunks
            batch_docs = []
            batch_ids = []
            batch_metas = []

            for i, chunk in enumerate(chunks):
                drawer_id = (
                    f"drawer_ag_{conversation_id[:8]}_{filepath.stem}_"
                    f"{hashlib.md5(chunk[:200].encode(), usedforsecurity=False).hexdigest()[:12]}"
                )

                if drawer_exists(collection, drawer_id):
                    skipped += 1
                    continue

                batch_docs.append(chunk)
                batch_ids.append(drawer_id)
                batch_metas.append(
                    {
                        "wing": item_wing,
                        "room": item_room,
                        "source_file": str(filepath),
                        "conversation_id": conversation_id,
                        "artifact_type": metadata.get("ArtifactType", "unknown"),
                        "chunk_index": i,
                        "added_by": agent,
                        "filed_at": datetime.now().isoformat(),
                        "content_hash": content_hash,
                        "ingest_mode": "antigravity",
                        "item_type": item["type"],
                    }
                )

            drawers_added = 0
            if batch_docs:
                try:
                    collection.add(
                        documents=batch_docs,
                        ids=batch_ids,
                        metadatas=batch_metas,
                    )
                    drawers_added = len(batch_ids)
                except Exception as e:
                    if "already exists" not in str(e).lower():
                        print(f"    Error filing {filepath.name}: {e}")

            total_drawers += drawers_added
            wing_counts[item_wing] += 1
            room_counts[item_room] += drawers_added

            if drawers_added > 0:
                print(f"  [+] {filepath.name:50} +{drawers_added} drawers")

            items_processed += 1

            # Mine version history if requested
            if include_versions and item.get("versions"):
                for version_path in item["versions"]:
                    try:
                        v_content = version_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue

                    if len(v_content.strip()) < MIN_CONTENT_LENGTH:
                        continue

                    v_source_file = str(version_path)
                    v_content_hash = compute_file_hash(v_content)
                    v_status = (
                        "new"
                        if dry_run
                        else file_needs_update(collection, v_source_file, v_content_hash)
                    )

                    if v_status == "unchanged":
                        continue
                    elif v_status == "changed" and not dry_run:
                        delete_drawers_for_file(collection, v_source_file)

                    v_chunks = chunk_content(v_content)
                    v_batch_docs = []
                    v_batch_ids = []
                    v_batch_metas = []
                    for vi, v_chunk in enumerate(v_chunks):
                        v_drawer_id = (
                            f"drawer_ag_{conversation_id[:8]}_{version_path.name}_"
                            f"{hashlib.md5(v_chunk[:200].encode(), usedforsecurity=False).hexdigest()[:12]}"
                        )

                        if drawer_exists(collection, v_drawer_id):
                            continue

                        v_batch_docs.append(v_chunk)
                        v_batch_ids.append(v_drawer_id)
                        v_batch_metas.append(
                            {
                                "wing": item_wing,
                                "room": item_room,
                                "source_file": str(version_path),
                                "conversation_id": conversation_id,
                                "artifact_type": metadata.get("ArtifactType", "unknown"),
                                "chunk_index": vi,
                                "added_by": agent,
                                "filed_at": datetime.now().isoformat(),
                                "content_hash": v_content_hash,
                                "ingest_mode": "antigravity_version",
                                "item_type": "version",
                                "version_of": filepath.name,
                            }
                        )

                    if v_batch_docs:
                        try:
                            collection.add(
                                documents=v_batch_docs,
                                ids=v_batch_ids,
                                metadatas=v_batch_metas,
                            )
                            total_drawers += len(v_batch_docs)
                        except Exception:
                            pass

        if limit > 0 and items_processed >= limit:
            break

    print(f"\n{'=' * 55}")
    print("  Done.")
    print(f"  Conversations scanned: {total_conversations}")
    print(f"  Items processed: {items_processed}")
    print(f"  Drawers filed: {total_drawers}")
    if skipped:
        print(f"  Drawers skipped (already filed): {skipped}")
    if wing_counts:
        print("\n  By wing:")
        for w, count in sorted(wing_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"    {w:30} {count} items")
    if room_counts:
        print("\n  By room:")
        for r, count in sorted(room_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"    {r:30} {count} drawers")
    print('\n  Next: callosum search "what you\'re looking for"')
    print(f"{'=' * 55}\n")


# =============================================================================
# GARBAGE COLLECTION
# =============================================================================


def garbage_collect_antigravity(palace_path: str, brain_path: str, dry_run: bool = False) -> dict:
    """Find and remove drawers for antigravity artifacts that no longer exist."""
    print(f"\n{'=' * 55}")
    print("  Callosum GC -- Antigravity")
    print(f"{'=' * 55}")
    print(f"  Brain:   {brain_path}")
    if dry_run:
        print("  DRY RUN -- nothing will be deleted")
    print(f"{'-' * 55}\n")

    try:
        client = chromadb.PersistentClient(path=palace_path)
        col = client.get_collection("callosum_drawers")
    except Exception:
        print("  No palace found.")
        return {"files_removed": 0, "drawers_removed": 0}

    # Query all metadata for antigravity mode (requires scanning all, or fetching by chunks)
    # ChromaDB 'get' might be heavy for all. We'll use get with where clause.
    try:
        # We query for both ingest modes: "antigravity" and "antigravity_version"
        # ChromaDB doesn't allow OR across different keys, but we can query each.
        results_ag = col.get(where={"ingest_mode": "antigravity"}, include=["metadatas"])
        results_v = col.get(where={"ingest_mode": "antigravity_version"}, include=["metadatas"])
        metadatas = results_ag.get("metadatas", []) + results_v.get("metadatas", [])
    except Exception as e:
        print(f"  Error querying palace: {e}")
        return {"files_removed": 0, "drawers_removed": 0}

    if not metadatas:
        print("  No antigravity drawers found.")
        return {"files_removed": 0, "drawers_removed": 0}

    source_files = set()
    for m in metadatas:
        if "source_file" in m:
            source_files.add(m["source_file"])

    files_removed = 0
    drawers_removed = 0

    for source_file in source_files:
        if not os.path.exists(source_file):
            if dry_run:
                print(
                    f"    [DRY RUN] Would delete drawers for missing artifact: {Path(source_file).name}"
                )
                files_removed += 1
            else:
                try:
                    count = len(col.get(where={"source_file": source_file}).get("ids", []))
                    col.delete(where={"source_file": source_file})
                    print(f"  [+] Purged {count} orphaned drawers for: {Path(source_file).name}")
                    drawers_removed += count
                    files_removed += 1
                except Exception as e:
                    print(f"  ! Error deleting {source_file}: {e}")

    print(f"\n{'=' * 55}")
    print("  GC Complete.")
    print(f"  Artifacts pruned: {files_removed}")
    print(f"  Drawers purged:   {drawers_removed}")
    print(f"{'=' * 55}\n")
    return {"files_removed": files_removed, "drawers_removed": drawers_removed}
