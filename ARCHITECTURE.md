# Callosum Architecture

Callosum is designed as a modular, local-first memory pipeline. Its core purpose is to parse disparate files, chats, and AI context logs into a single, highly structured semantic graph and searchable vector database, bridging the memory of independent AI agents.

## Core Taxonomies

Callosum organizes memory via three spatial vectors:
- **Wing**: Represents a large, isolated project or domain (e.g., `wing_callosum`, `wing_my_app`).
- **Room**: Represents a specific topic or functional classification within the wing (e.g., `room_planning`, `room_security`).
- **Drawer**: The physical storage unit holding chunks of identical semantic information in the vector database and entity relationships in the Knowledge Graph.

## Key Subsystems

### 1. Vector Storage (`palace/`)
Powered by local ChromaDB instances. When queries are executed, embeddings are strictly calculated using built-in, lightweight sentence transformers without external API dependency. Chroma collections are mapped by wing, establishing clear retrieval boundaries.

### 2. Knowledge Graph (`knowledge_graph.py`)
An SQLite layer with `WAL` (Write-Ahead Logging) journaling enabled for heavy concurrency handling.
- Maps Subjects and Predicates.
- Enables queries regarding temporal shifts (e.g., *When did we switch from SQL to Firebase?*).
- Incorporates user-identity pinning inside `people_map.json` to canonicalize interpersonal records.

### 3. The Extraction Pipeline (`miner.py` & `convo_miner.py`)
The ingestion engine follows this pattern:
1. **Source Discovery**: Walks `workspaces_dir` recursively.
2. **Room Detection** (`room_detector_local.py`): Examines context windows to classify the text into semantic spatial rooms.
3. **Extraction Layer** (`layers.py`): Compresses verbatim text into isolated, declarative statements, avoiding lossy abstract summarization.
4. **Garbage Collection (GC)**: Computes chunk hashes to detect and invalidate stale information rapidly.

### 4. Smart Routing
Handled through a locally hosted Ollama API (`smart_routing_model`). When simple keyword taxonomy mappings fail, Callosum invokes the LLM with dynamic sets of local project identifiers to contextually deduce where isolated notes and artifacts belong.

### 5. Interaction Hooks
- **MCP Server** (`mcp_server.py`): Uses an interactive RPC daemon integrated securely into `~/.gemini/settings.json` and standard `claude` CLI commands, providing remote, interactive manipulation of the unified graph.
- **Gemini Hooks** (`hooks/precompress_hook.py`): Injected natively as a Python preprocessing layer inside a `gemini-cli` compression step, modifying API outputs on the fly.
- **Autonomous Sweeper Task** (`scheduler.py`): Hooked into `schtasks` to constantly read over background file states to maintain fresh context effortlessly.

### 6. Zero-Touch Universal Daemon
The background intelligence is handled via an asynchronous, completely decoupled daemon (`watcher.py` via `callosum watch --all --daemon`). 
Instead of relying on heavy third-party file event libraries like `watchdog`, it implements a hyper-lightweight loop polling `os.stat` timestamps to map modifications.
- **Ambient Memory**: Polling operates with negligible CPU footprint (30s interval).
- **Graceful Error Isolation**: Errors on one project file do not crash the watcher protecting other wings.
- **Silent Boot Configuration**: Features self-registering VBS-based integration with the Windows Registry `[HKCU\Software\Microsoft\Windows\CurrentVersion\Run]` to maintain constant observation invisibly upon startup.
