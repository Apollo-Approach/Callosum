#!/usr/bin/env python3
"""
precompress_hook.py — Save memories before context window compression.

This hook fires when the AI's context window is about to be compressed/truncated.
It instructs the AI to save important memories from the current session before
they're lost.

Cross-platform: works on Windows, macOS, and Linux.

Usage in Gemini CLI settings.json:
{
  "hooks": {
    "PreCompress": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "C:\\\\path\\\\to\\\\Callosum\\\\.venv\\\\Scripts\\\\python.exe C:\\\\path\\\\to\\\\Callosum\\\\hooks\\\\precompress_hook.py"
      }]
    }]
  }
}
"""

import sys

# The hook outputs instructions for the AI to follow before compression happens.
# This text is injected into the conversation as a system message.
PRECOMPRESS_PROMPT = """
⚠️ CONTEXT COMPRESSION IMMINENT — SAVE YOUR MEMORIES

Your context window is about to be compressed. Before that happens, save
the most important information from this session using callosum tools:

1. Call `callosum_save_session` with a summary of key decisions, discoveries,
   and important context from this conversation.

2. For any critical decisions made, also call `callosum_save_decision` to
   record them in the knowledge graph with proper temporal metadata.

3. Prioritize saving:
   - Decisions and their rationale
   - New discoveries or insights
   - Architecture changes
   - User preferences learned
   - Unresolved issues or next steps

Save now — this context will be lost after compression.
"""


def main():
    print(PRECOMPRESS_PROMPT.strip())
    sys.exit(0)


if __name__ == "__main__":
    main()
