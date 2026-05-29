#!/usr/bin/env python3
"""
save_hook.py - Periodic save hook for active sessions.

This hook fires periodically (e.g., every 15 messages) to remind the AI
to save important memories from the ongoing session.

Cross-platform: works on Windows, macOS, and Linux.

Security: This hook intentionally takes NO arguments from the shell
environment. All output is static text. This prevents shell injection
attacks via manipulated SESSION_ID or other env vars.
(Upstream fix: Callosum PR #387, #141)
"""

import sys
import re


# --- Upstream fix: Sanitize any env vars we might read (PR #141) ---
def _sanitize(value: str, max_len: int = 200) -> str:
    """Strip anything that isn't alphanumeric, dash, underscore, or dot."""
    if not value:
        return ""
    clean = re.sub(r"[^a-zA-Z0-9_.\-]", "", value[:max_len])
    return clean


SAVE_PROMPT = """
\U0001f4be PERIODIC MEMORY SAVE

If you've made important decisions, discoveries, or progress since the last
save, use `callosum_save_session` to record them. Include:

- Key decisions and rationale
- Problems solved and how
- Architecture or design changes
- Important user preferences or context

Skip this if nothing significant has happened since the last save.
"""


def main():
    # Static output only - no shell-injectable inputs
    print(SAVE_PROMPT.strip())
    sys.exit(0)


if __name__ == "__main__":
    main()
