"""
Callosum configuration system.

Priority: env vars > config file (~/.callosum/config.json) > defaults
"""

import json
import os
from pathlib import Path

DEFAULT_PALACE_PATH = str(Path.home() / ".callosum" / "palace")


def harden_permissions(path: str):
    """Restrict file/dir permissions to current user only.

    Upstream fix: MemPalace v3.3.1 file permission hardening.
    Palace data may contain sensitive conversation history,
    credentials, and financial data.

    On Windows: uses icacls to remove inheritance and grant full control
    only to the current user.
    On POSIX: chmod 700 (dirs) or 600 (files).
    """
    import stat

    p = Path(path)
    if not p.exists():
        return
    try:
        if os.name == "nt":
            # Windows: restrict to current user via icacls
            import subprocess

            user = os.environ.get("USERNAME", "")
            if user and p.is_dir():
                subprocess.run(
                    ["icacls", str(p), "/inheritance:r", "/grant", f"{user}:(OI)(CI)F"],
                    capture_output=True,
                    timeout=10,
                )
        else:
            # POSIX: owner-only
            if p.is_dir():
                p.chmod(stat.S_IRWXU)
            else:
                p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass  # Best-effort — don't break init if permissions fail


DEFAULT_COLLECTION_NAME = "callosum_drawers"

DEFAULT_TOPIC_WINGS = [
    "emotions",
    "consciousness",
    "memory",
    "technical",
    "identity",
    "family",
    "creative",
]

DEFAULT_HALL_KEYWORDS = {
    "emotions": [
        "scared",
        "afraid",
        "worried",
        "happy",
        "sad",
        "love",
        "hate",
        "feel",
        "cry",
        "tears",
    ],
    "consciousness": [
        "consciousness",
        "conscious",
        "aware",
        "real",
        "genuine",
        "soul",
        "exist",
        "alive",
    ],
    "memory": ["memory", "remember", "forget", "recall", "archive", "palace", "store"],
    "technical": [
        "code",
        "python",
        "script",
        "bug",
        "error",
        "function",
        "api",
        "database",
        "server",
    ],
    "identity": ["identity", "name", "who am i", "persona", "self"],
    "family": ["family", "kids", "children", "daughter", "son", "parent", "mother", "father"],
    "creative": ["game", "gameplay", "player", "app", "design", "art", "music", "story"],
}


class CallosumConfig:
    """Configuration manager for Callosum.

    Load order: env vars > config file > defaults.
    """

    def __init__(self, config_dir=None):
        """Initialize config.

        Args:
            config_dir: Override config directory (useful for testing).
                        Defaults to ~/.callosum.
        """
        self._config_dir = Path(config_dir) if config_dir else Path.home() / ".callosum"
        self._config_file = self._config_dir / "config.json"
        self._people_map_file = self._config_dir / "people_map.json"
        self._file_config = {}

        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    self._file_config = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._file_config = {}

    @property
    def palace_path(self):
        """Path to the memory palace data directory."""
        env_val = os.environ.get("CALLOSUM_PALACE_PATH") or os.environ.get("MEMPAL_PALACE_PATH")
        if env_val:
            return env_val
        return self._file_config.get("palace_path", DEFAULT_PALACE_PATH)

    @property
    def collection_name(self):
        """ChromaDB collection name."""
        return self._file_config.get("collection_name", DEFAULT_COLLECTION_NAME)

    @property
    def people_map(self):
        """Mapping of name variants to canonical names."""
        if self._people_map_file.exists():
            try:
                with open(self._people_map_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return self._file_config.get("people_map", {})

    @property
    def topic_wings(self):
        """List of topic wing names."""
        return self._file_config.get("topic_wings", DEFAULT_TOPIC_WINGS)

    @property
    def hall_keywords(self):
        """Mapping of hall names to keyword lists."""
        return self._file_config.get("hall_keywords", DEFAULT_HALL_KEYWORDS)

    @property
    def smart_routing_enabled(self):
        """Whether to enable LLM-based smart routing for artifacts."""
        return self._file_config.get("smart_routing_enabled", False)

    @property
    def smart_routing_model(self):
        """Ollama model to use for smart routing."""
        return self._file_config.get("smart_routing_model", "phi3.5")

    @property
    def ollama_endpoint(self):
        """Ollama API endpoint (e.g. http://127.0.0.1:11434)."""
        return self._file_config.get("ollama_endpoint", "http://127.0.0.1:11434")

    @property
    def ollama_ca_cert(self):
        """Path to CA certificate for verifying the Ollama/LlamaQ TLS connection.

        When the Ollama endpoint is behind LlamaQ (HTTPS with self-signed CA),
        this must point to LlamaQ's ca.pem so urllib can verify the connection.
        Returns None if not configured (uses system trust store).
        """
        return self._file_config.get("ollama_ca_cert", None)

    @property
    def workspaces_dir(self):
        """Directory to scan for dynamic project discovery."""
        return Path(self._file_config.get("workspaces_dir", "C:/Development"))

    def build_dynamic_wing_keywords(self) -> dict:
        """
        Scan workspaces_dir for callosum.yaml files and dynamically
        build the wing_keywords mapping.
        """
        wing_keywords = {}
        import yaml

        base_dir = self.workspaces_dir
        if not base_dir.exists():
            return wing_keywords

        # Scan depth=1 for projects
        for project_dir in base_dir.iterdir():
            if not project_dir.is_dir():
                continue

            yaml_path = project_dir / "callosum.yaml"
            if not yaml_path.exists():
                legacy_path = project_dir / "mempal.yaml"
                if legacy_path.exists():
                    yaml_path = legacy_path
                else:
                    continue

            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)

                wing_name = config.get("wing")
                if not wing_name:
                    continue

                # Gather all keywords from rooms to feed the LLM
                kws = set()
                kws.add(wing_name.replace("_", " "))
                kws.add(wing_name)

                for room in config.get("rooms", []):
                    kws.add(room.get("name", ""))
                    for kw in room.get("keywords", []):
                        kws.add(kw)

                # Remove empty strings
                kws = {k for k in kws if k}
                wing_keywords[wing_name] = list(kws)
            except Exception as e:
                print(f"  [Warning] Failed to parse {yaml_path}: {e}")

        return wing_keywords

    def get_registered_workspaces(self) -> dict:
        workspaces = {}
        import yaml

        base_dir = self.workspaces_dir
        if not base_dir.exists():
            return workspaces
        for project_dir in base_dir.iterdir():
            if not project_dir.is_dir():
                continue
            yaml_path = project_dir / "callosum.yaml"
            if not yaml_path.exists():
                yaml_path = project_dir / "mempal.yaml"
                if not yaml_path.exists():
                    continue
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                wing_name = config.get("wing")
                if wing_name:
                    workspaces[str(project_dir.absolute())] = wing_name
            except Exception:
                pass
        return workspaces

    def init(self):
        """Create config directory and write default config.json if it doesn't exist."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        if not self._config_file.exists():
            default_config = {
                "palace_path": DEFAULT_PALACE_PATH,
                "collection_name": DEFAULT_COLLECTION_NAME,
                "topic_wings": DEFAULT_TOPIC_WINGS,
                "hall_keywords": DEFAULT_HALL_KEYWORDS,
                "smart_routing_enabled": False,
                "smart_routing_model": "phi3.5",
                "ollama_endpoint": "http://127.0.0.1:11434",
                "workspaces_dir": "C:/Development",
            }
            with open(self._config_file, "w") as f:
                json.dump(default_config, f, indent=2)
        return self._config_file

    def save_people_map(self, people_map):
        """Write people_map.json to config directory.

        Args:
            people_map: Dict mapping name variants to canonical names.
        """
        self._config_dir.mkdir(parents=True, exist_ok=True)
        with open(self._people_map_file, "w") as f:
            json.dump(people_map, f, indent=2)
        return self._people_map_file
