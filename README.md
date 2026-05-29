<p align="center">
  <img src="assets/callosum_logo_192x191.png" alt="Callosum Logo" width="128" />
</p>

<h1 align="center">Callosum</h1>
<p align="center"><strong>Cooperative AI Memory — Claude · Gemini Bridge</strong></p>

<p align="center">
  Give your AI persistent memory across sessions and models.<br/>
  No API key required. Runs entirely on your local machine.
</p>

---

## What is Callosum?

Callosum is a local-first memory system that lets AI assistants (Claude, Gemini, or any MCP-compatible model) **remember** across conversations. It stores verbatim content in a searchable vector database (ChromaDB) organized by **wings** (projects) and **rooms** (topics).

Named after the [corpus callosum](https://en.wikipedia.org/wiki/Corpus_callosum) — the neural bridge connecting the brain's two hemispheres — Callosum bridges multiple AI models so they share a single persistent memory.

### Key Features

- **Three ingest modes**: Mine project files (`projects`), chat exports (`convos`), or Gemini CLI artifacts (`antigravity`)
- **Smart Routing**: LLM-backed auto-classification via local Ollama inference (default: `phi3.5`)
- **Dynamic Discovery**: Auto-scans your workspace for `callosum.yaml` files — new projects are learned instantly
- **MCP server**: Register as a tool for Claude Code, Gemini CLI, or any MCP client
- **Knowledge graph**: Temporal facts with validity windows — track what's true and when it changed
- **Agent diary**: Each AI model gets its own journal, readable by others
- **Semantic search**: Find anything by meaning, filtered by wing or room
- **Windows-first**: Python-native hooks, no bash dependencies

### Architecture

See the [ARCHITECTURE.md](ARCHITECTURE.md) for a deep dive into the subsystems, data flows, and routing algorithms.

```
~/.callosum/
├── config.json           # Settings (smart routing, workspaces_dir, etc.)
├── identity.txt          # Who you are (loaded on wake-up)
├── people_map.json       # User → canonical name mapping
├── entity_registry.json  # Global entity registry (people, projects)
├── known_names.json      # Name variants / aliases resolved during mining
├── hallways.json         # Entity-to-entity connections within wings
├── knowledge_graph.db    # SQLite — temporal facts (subject→predicate→object)
├── backlog.db            # SQLite — open loops / deferred tasks
├── blueprints.db         # SQLite — architectural maps and topologies
└── palace/               # ChromaDB vector database
    ├── wing_projectA/    # One wing per project
    │   ├── room_planning/
    │   ├── room_security/
    │   └── room_general/
    ├── wing_projectB/
    │   └── ...
    └── wing_general/     # Cross-project context
```

---

## Quick Start

### Install (Recommended: pipx)

```bash
git clone https://github.com/Apollo-Approach/Callosum.git
pip install pipx
pipx install ./Callosum --editable
pipx ensurepath   # adds ~/.local/bin to PATH (restart terminal)
```

**Updating:** If you pull new dependencies and need to refresh your installation, simply double-click `update.bat` in the repository root. This automates the safe `pipx` uninstall/reinstall pipeline without path collisions on Windows.

This installs `callosum` in its own isolated virtual environment while making the CLI globally available. No global Python pollution.

<details>
<summary>Alternative: venv install (development)</summary>

```bash
cd Callosum
python -m venv .venv
.venv\Scripts\pip install -e .     # Windows
# or: .venv/bin/pip install -e .   # macOS/Linux
```
</details>

### First-Time Setup

```bash
callosum setup
```

The interactive setup wizard configures your Callosum instance end-to-end, requiring zero manual tweaking. It dynamically handles:

1. **Agent Identity:** Defines the persona of the AI reading the memory upon wake-up.
2. **User Persona:** Maps your actual name securely into the Knowledge Graph to properly attribute interpersonal facts to *you*.
3. **Workspace Tracking:** Defines what parent directories to dynamically scan for new projects.
4. **Storage Configuration:** Connects directly to your structured local ChromaDB Palace.
5. **Smart Routing:** Identifies and live-probes your local Ollama LLM instance to auto-classify unstructured artifacts.
6. **Auto-Sweeper Scheduling:** Automatically pushes an instruction to Windows Task Scheduler to silently run background memory indexing/garbage collection without interrupting you.
7. **MCP Zero-Touch Auto-Injection:** Automatically detects your Gemini CLI configurations and safely injects the Callosum memory engine as an active MCP server, making installation completely plug-and-play.

### Mine a Project

When inside your project directory, simply run:

```bash
# Initialize room detection (defaults to current directory)
callosum init

# Mine project files (defaults to current directory)
callosum mine

# Search
callosum search "why did we switch to GraphQL"
```

*(You can also pass explicit paths like `callosum mine ~/projects/my_app`)*

### Universal Daemon

Callosum is meant to be ambient—running continuously in the background to safely and automatically update memory whenever you save files across any project. It relies on a zero-dependency polling mechanism (`os.stat`) ensuring negligible CPU overhead.

```bash
# Launch the silent Universal Daemon in the background
callosum watch --all --daemon

# Check whether the daemon is actively running
callosum watch --status

# Register the daemon to start invisibly on Windows boot
callosum watch --install-startup
```

### System Tray App (Windows)

Callosum includes a Windows System Tray application (`callosum_tray.exe`) that runs a background sweeper and provides quick access:
- **Sweep Now** — Trigger an immediate auto-mine and garbage collection across all discovered projects
- **Open Palace Directory** — Open the `~/.callosum/palace/` data directory in Windows Explorer
- **Exit** — Shut down the tray app and its background sweeper thread

The tray app also runs a silent background sweeper every 4 hours automatically.

Run `python run_tray.py` during development, or use the compiled executable included in the Windows Installer.

### Mine Gemini CLI Artifacts

```bash
# Mine your Antigravity brain directory
callosum mine ~/.gemini/antigravity/brain --mode antigravity

# Search across all your AI conversations
callosum search "incorporation checklist"
```

---

## Smart Routing

When mining unstructured artifacts (chat logs, Gemini CLI brain dumps), Callosum uses a two-stage routing pipeline:

1. **Keyword scoring** — Fast, static matching against known project terms
2. **LLM fallback** — If keywords don't match, the content is sent to a local Ollama model for intelligent classification

### Dynamic Project Discovery

The Smart Router automatically scans your `workspaces_dir` (default: `C:/Development`) for `callosum.yaml` files on every run. This means:

- **Zero maintenance** — New projects are learned instantly when you run `callosum init .` in them
- **No hardcoded lists** — The LLM prompt is dynamically assembled from your actual project configs
- **Graceful fallback** — If the workspace scan fails, a hardcoded baseline map is used

### Configuration

Smart routing is configured via `~/.callosum/config.json` or the setup wizard:

```json
{
  "smart_routing_enabled": true,
  "smart_routing_model": "phi3.5",
  "ollama_endpoint": "http://127.0.0.1:11434",
  "workspaces_dir": "C:/Development"
}
```

| Setting | Description | Default |
|---------|-------------|---------|
| `smart_routing_enabled` | Enable LLM-backed routing | `false` |
| `smart_routing_model` | Ollama model tag | `phi3.5` |
| `ollama_endpoint` | Ollama server URL | `http://127.0.0.1:11434` |
| `workspaces_dir` | Directory to scan for project YAMLs | `C:/Development` |

### Register as MCP Server

**Gemini CLI** (`~/.gemini/settings.json`):
```json
{
  "mcpServers": {
    "callosum": {
      "command": "C:\\path\\to\\Callosum\\.venv\\Scripts\\python.exe",
      "args": ["-m", "callosum.mcp_server"]
    }
  }
}
```

**Claude Code**:
```bash
claude mcp add callosum -- python -m callosum.mcp_server
```

---

## MCP Tools (33 total)

### Core
| Tool | Description |
|------|-------------|
| `Callosum_status` | Palace overview — total drawers, wing/room counts |
| `Callosum_list_wings` | List all wings with drawer counts |
| `Callosum_list_rooms` | List rooms within a wing (or all rooms) |
| `Callosum_get_taxonomy` | Full wing → room → drawer count tree |
| `Callosum_search` | Semantic search with mandatory wing filter (Iron Curtain) |
| `Callosum_check_duplicate` | Check if content already exists before filing |
| `Callosum_add_drawer` | File verbatim content into a wing/room (auto-deduplicates) |
| `Callosum_delete_drawer` | Remove a drawer by ID (irreversible) |

### Knowledge Graph
| Tool | Description |
|------|-------------|
| `Callosum_kg_query` | Query entity relationships with temporal filtering |
| `Callosum_kg_add` | Add a temporal fact (subject → predicate → object) |
| `Callosum_kg_invalidate` | Mark a fact as no longer true (set end date) |
| `Callosum_kg_timeline` | Chronological fact history for an entity or all |
| `Callosum_kg_stats` | Knowledge graph overview — entities, triples, relationship types |

### Diary
| Tool | Description |
|------|-------------|
| `Callosum_diary_write` | Write a personal agent diary entry (timestamped journal) |
| `Callosum_diary_read` | Read an agent's recent diary entries |

### Graph / Traversal
| Tool | Description |
|------|-------------|
| `Callosum_traverse` | Walk the palace graph from a room across wings |
| `Callosum_find_tunnels` | Find rooms that bridge two wings |
| `Callosum_graph_stats` | Palace graph overview — nodes, tunnels, edges, connectivity |

### Hallways
| Tool | Description |
|------|-------------|
| `Callosum_list_hallways` | List entity-to-entity connections within a wing |
| `Callosum_compute_hallways` | Compute/update intra-wing hallways for a wing |

### Backlog
| Tool | Description |
|------|-------------|
| `Callosum_add_open_loop` | Defer a task, bug, or idea to the project backlog |
| `Callosum_get_backlog` | Retrieve backlog items (open, resolved, or all) |
| `Callosum_resolve_open_loop` | Mark an open loop as resolved |

### Blueprints
| Tool | Description |
|------|-------------|
| `Callosum_save_blueprint` | Save/overwrite an architectural blueprint (Markdown/JSON) |
| `Callosum_load_blueprint` | Retrieve a specific blueprint by wing and name |
| `Callosum_list_blueprints` | List all available blueprints |

### Isolation
| Tool | Description |
|------|-------------|
| `Callosum_link_wings` | Create an opt-in tunnel link between two wings |
| `Callosum_unlink_wings` | Revoke a tunnel link between two wings |
| `Callosum_isolation_report` | Show the current wing isolation posture |

### Staleness
| Tool | Description |
|------|-------------|
| `Callosum_check_stale` | Check for drawers whose source files have changed |
| `Callosum_check_engram_drift` | Check if Engram reference files have drifted from palace |

### Maintenance
| Tool | Description |
|------|-------------|
| `Callosum_health_check` | Comprehensive health check — ChromaDB, drawers, isolation, schedule |
| `Callosum_maintain` | Run automated maintenance — stale fix, GC, coverage report |

---

## Maintenance & Administration

Beyond the core `setup`, `init`, `mine`, `search`, `wake-up`, and `watch` commands, Callosum includes several maintenance and administration subcommands:

### Garbage Collection & Sweeping

```bash
# Remove stale drawers for files that no longer exist
callosum gc                          # GC current directory
callosum gc ~/projects/my_app        # GC a specific project
callosum gc ~/.gemini/antigravity/brain  # GC antigravity artifacts
callosum gc --dry-run                # Preview what would be removed

# Auto-mine + GC all discovered projects in one pass
callosum sweep
```

### Scheduling

```bash
# Register a Windows scheduled task to sweep automatically
callosum schedule                    # Default: every 4 hours
callosum schedule --interval 8       # Every 8 hours

# Remove the scheduled task
callosum unschedule

# List active Callosum scheduled tasks
callosum schedules
```

### Health & Maintenance

```bash
# Full system health check: drawers, closets, isolation, staleness, schedule
callosum health

# Automated maintenance: stale remediation, GC, coverage report
callosum maintain                    # Report only
callosum maintain --auto-fix         # Automatically fix issues
callosum maintain --dry-run          # Preview without changes
```

### Migration & Repair

```bash
# Migrate ChromaDB palace to current version (after upgrading ChromaDB)
callosum migrate                     # Interactive confirmation
callosum migrate --yes               # Skip confirmation prompt
callosum migrate --no-backup         # Skip backup (not recommended)

# Rebuild palace vector index from stored data (fixes corruption)
callosum repair
```

---

## Hooks (Windows)

Python-native hooks for Gemini CLI — no bash required:

```json
{
  "hooks": {
    "PreCompress": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "C:\\path\\to\\Callosum\\.venv\\Scripts\\python.exe C:\\path\\to\\Callosum\\hooks\\precompress_hook.py"
      }]
    }]
  }
}
```

---

## TOS Compliance

Callosum operates **entirely on local files**:

- **Project miner**: Reads files from directories you specify on your own filesystem
- **Antigravity miner**: Reads `.md` artifact files already saved to your disk by Gemini CLI at `~/.gemini/antigravity/brain/`
- **MCP server**: Uses the [officially supported MCP protocol](https://modelcontextprotocol.io/) — a first-party Gemini CLI feature
- **Hooks**: Uses [official Gemini CLI hook APIs](https://github.com/google-gemini/gemini-cli)

**No network requests** are made to Google or any other service. No scraping, no API interception, no unauthorized access. Callosum is architecturally equivalent to reading your own text files with a Python script.

---

## Attribution

Callosum is a hard fork of [MemPalace](https://github.com/milla-jovovich/mempalace) by milla-jovovich, licensed under the MIT License.

### What changed from upstream

- **Rebranded**: All `MemPalace` references → `Callosum`
- **Stripped AAAK dialect**: Removed the lossy compression layer
- **Windows-first**: Python hooks replacing bash scripts
- **Antigravity miner**: Novel ingest mode for Gemini CLI artifacts (not in upstream)
- **Smart Routing**: LLM-backed auto-classification with dynamic project discovery (not in upstream)
- **Multi-model bridge**: Designed for Claude + Gemini cooperative workflows
- **Agentic state management**: Integrated Backlog and Architectural Blueprints (not in upstream)

---

## License

MIT — see [LICENSE](LICENSE).
