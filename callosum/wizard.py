import os
import ssl
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path
from .config import CallosumConfig

CALLOSUM_ASCII = r"""
\033[1;36m
  ____      _ _
 / ___|__ _| | | ___  ___ _   _ _ __
| |   / _` | | |/ _ \/ __| | | | '_ \
| |__| (_| | | | (_) \__ \ |_| | | | |
 \____\__,_|_|_|\___/|___/\__,_|_| |_|
\033[0m
"""


def test_ollama_connection(endpoint, ca_cert=None):
    """Test if Ollama is accessible at the given endpoint."""
    url = f"{endpoint.rstrip('/')}/api/tags"
    ssl_ctx = None
    if ca_cert and os.path.exists(ca_cert):
        ssl_ctx = ssl.create_default_context(cafile=ca_cert)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5.0, context=ssl_ctx) as response:
            if response.status == 200:
                return True
    except Exception:
        pass
    return False


def run_setup_wizard():
    print(CALLOSUM_ASCII)
    print("\033[1;36m=======================================================\033[0m")
    print("\033[1;36m  Callosum Setup Wizard\033[0m")
    print("\033[1;36m=======================================================\033[0m\n")

    print("\033[0;32mWelcome! Let's get Callosum configured.\033[0m\n")

    config = CallosumConfig()
    config_file = config.init()

    with open(config_file, "r") as f:
        data = json.load(f)

    # 1. Identity
    print("\033[1;35m--- 1. Agent Identity ---\033[0m")
    print("Callosum loads an identity file upon wake-up so the AI knows who it is assisting.")
    identity_path = Path.home() / ".callosum" / "identity.txt"
    default_identity = "I am a helpful AI assistant."
    if identity_path.exists():
        with open(identity_path, "r", encoding="utf-8") as f:
            existing = f.read().strip()
            if existing:
                default_identity = existing

    print(f"Current Identity: '{default_identity}'")
    nav_ident = input("Enter new identity (or press Enter to keep current): ").strip()
    if nav_ident:
        with open(identity_path, "w", encoding="utf-8") as f:
            f.write(nav_ident)
        print("\033[0;32m[+] Identity saved.\033[0m\n")
    else:
        # Save default if file didn't exist
        if not identity_path.exists():
            with open(identity_path, "w", encoding="utf-8") as f:
                f.write(default_identity)
        print("\033[0;33m- Kept current identity.\033[0m\n")

    # 2. User Persona (People Map)
    print("\033[1;35m--- 2. User Persona ---\033[0m")
    print("Callosum maps extracted relationships to specific people. What is your name?")
    people_map = config.people_map
    default_user_name = "User"
    for alias, canonical in people_map.items():
        if canonical == "User":
            default_user_name = alias
            break

    user_name = input(f"Your Name [{default_user_name}]: ").strip()
    if user_name and user_name != default_user_name:
        people_map[user_name] = "User"
        people_map["Me"] = "User"
        config.save_people_map(people_map)
        print("\033[0;32m[+] User persona pinned to Knowledge Graph.\033[0m\n")
    else:
        print("\033[0;33m- Kept current persona pin.\033[0m\n")

    # 3. Workspace Directory
    print("\033[1;35m--- 3. Workspaces Directory ---\033[0m")
    print("Callosum can dynamically discover new projects by scanning a parent directory.")
    default_workspace = data.get("workspaces_dir", "C:/Development")

    while True:
        workspaces_input = input(f"Workspaces Directory [{default_workspace}]: ").strip()
        selected_workspace = workspaces_input if workspaces_input else default_workspace

        if Path(selected_workspace).exists() and Path(selected_workspace).is_dir():
            data["workspaces_dir"] = selected_workspace
            print("\033[0;32m[+] Workspace directory verified.\033[0m\n")
            break
        else:
            print(
                f"\033[0;31m[x] Directory '{selected_workspace}' not found. Please try again.\033[0m"
            )

    # 4. Palace Path (Storage)
    print("\033[1;35m--- 4. Palace Storage Path ---\033[0m")
    print("This is where your ChromaDB vector database will physically reside.")
    app_data_default = str(Path.home() / ".callosum" / "palace")
    default_palace = data.get("palace_path", app_data_default)

    palace_input = input(f"Palace Path [{default_palace}]: ").strip()
    selected_palace = palace_input if palace_input else default_palace
    # Ensure parent exists or we can create it
    try:
        Path(selected_palace).mkdir(parents=True, exist_ok=True)
        data["palace_path"] = selected_palace
        print(f"\033[0;32m[+] Palace path configured at {selected_palace}.\033[0m\n")
    except Exception as e:
        print(
            f"\033[0;31m[x] Could not create directory: {e}. Falling back to {default_palace}\033[0m\n"
        )
        data["palace_path"] = default_palace

    # 5. Smart Routing (Ollama)
    print("\033[1;35m--- 5. Smart Routing (Ollama) ---\033[0m")
    print(
        "Callosum can use a local LLM via Ollama to automatically route files to the correct project wings."
    )
    print(
        "This is especially useful for artifacts from Gemini CLI or chat exports where context is messy."
    )

    enable_smart = input("Enable Smart Routing? (y/N): ").strip().lower() == "y"
    data["smart_routing_enabled"] = enable_smart

    if enable_smart:
        default_model = data.get("smart_routing_model", "phi3.5")
        model_input = input(f"Ollama Model tag [{default_model}]: ").strip()
        if model_input:
            default_model = model_input
        data["smart_routing_model"] = default_model

        default_endpoint = data.get("ollama_endpoint", "http://127.0.0.1:11434")
        endpoint_input = input(f"Ollama Endpoint [{default_endpoint}]: ").strip()
        if endpoint_input:
            default_endpoint = endpoint_input
        data["ollama_endpoint"] = default_endpoint

        # CA cert for HTTPS endpoints (e.g. behind LlamaQ)
        ca_cert_path = data.get("ollama_ca_cert", "")
        if default_endpoint.startswith("https://"):
            default_ca = ca_cert_path or str(Path.home() / ".callosum" / "llamaq_ca.pem")
            ca_input = input(f"CA Certificate Path [{default_ca}]: ").strip()
            ca_cert_path = ca_input if ca_input else default_ca
            if os.path.exists(ca_cert_path):
                data["ollama_ca_cert"] = ca_cert_path
                print(f"\033[0;32m[+] CA cert loaded: {ca_cert_path}\033[0m")
            else:
                print(f"\033[0;33m[!] CA cert not found at {ca_cert_path} — HTTPS may fail\033[0m")
                data["ollama_ca_cert"] = ca_cert_path

        print("\nTesting Ollama connection...")
        if test_ollama_connection(data["ollama_endpoint"], ca_cert_path):
            print(
                "\033[0;32m[+] Smart Routing configured and Ollama connection successful!\033[0m\n"
            )
        else:
            print(
                "\033[0;33m[!] Warning: Could not connect to Ollama. The routing agent may fail during execution.\033[0m\n"
            )
            print("Make sure your Ollama instance is running and accessible.")
    else:
        print("\n\033[0;33m- Smart Routing disabled (using keyword detection only).\033[0m\n")

    # 6. Auto-Sweeper Scheduling
    print("\033[1;35m--- 6. Background Sweeper ---\033[0m")
    print(
        "Callosum can silently index and garbage collect your projects in the background using Windows Task Scheduler."
    )
    enable_sweep = input("Enable Auto-Sweeper? (y/N): ").strip().lower() == "y"
    if enable_sweep:
        interval = input("Run every N hours [4]: ").strip()
        try:
            int_val = int(interval) if interval else 4
        except ValueError:
            int_val = 4

        from .scheduler import register_schedule

        print()
        register_schedule(int_val)
        print()
    else:
        print(
            "\n\033[0;33m- Auto-Sweeper disabled. You can run 'callosum sweep' manually.\033[0m\n"
        )
        from .scheduler import unregister_schedule

        try:
            # Silently unregister if they say no
            unregister_schedule()
        except Exception:
            pass

    # 7. MCP Auto-Injection
    print("\033[1;35m--- 7. MCP Auto-Injection ---\033[0m")
    print("If you use Gemini CLI, we can automatically add Callosum as an MCP server.")
    gemini_settings_path = Path.home() / ".gemini" / "settings.json"
    if gemini_settings_path.exists():
        inject_mcp = (
            input("Inject Callosum into ~/.gemini/settings.json? (Y/n): ").strip().lower() != "n"
        )
        if inject_mcp:
            try:
                with open(gemini_settings_path, "r", encoding="utf-8") as gf:
                    g_data = json.load(gf)

                if "mcpServers" not in g_data:
                    g_data["mcpServers"] = {}

                g_data["mcpServers"]["callosum"] = {
                    "command": sys.executable,
                    "args": ["-m", "callosum.mcp_server"],
                }

                with open(gemini_settings_path, "w", encoding="utf-8") as gf:
                    json.dump(g_data, gf, indent=2)

                print("\033[0;32m[+] MCP Server auto-injected dynamically!\033[0m\n")
            except Exception as e:
                print(f"\033[0;31m[x] Could not inject MCP: {e}\033[0m\n")
        else:
            print("\033[0;33m- Skipped MCP auto-injection.\033[0m\n")
    else:
        print("\033[0;33m- No ~/.gemini/settings.json found to inject into. Skipping.\033[0m\n")

    # Write back to config
    with open(config_file, "w") as f:
        json.dump(data, f, indent=2)

    print("\033[1;36m=======================================================\033[0m")
    print(f"\033[1;32m[+] Setup complete! Configuration saved to {config_file}\033[0m")
    print("\033[1;36m=======================================================\033[0m\n")
