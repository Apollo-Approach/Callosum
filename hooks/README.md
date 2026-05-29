# Callosum Hooks — Auto-Save for Terminal AI Tools

These hook scripts make Callosum save automatically. No manual "save" commands needed.

## What They Do

| Hook | When It Fires | What Happens |
|------|--------------|-------------|
| **Save Hook** (`save_hook.py`) | Every 15 human messages | Reminds the AI to save key topics/decisions/quotes to the palace |
| **PreCompact Hook** (`precompress_hook.py`) | Right before context compaction | Emergency save — forces the AI to save EVERYTHING before losing context |

The AI does the actual filing — it knows the conversation context, so it classifies memories into the right wings/halls/closets. The hooks just tell it WHEN to save.

## Install — Gemini CLI

Edit `~/.gemini/settings.json`:

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "/absolute/path/to/callosum/.venv/bin/python /absolute/path/to/callosum/hooks/save_hook.py",
        "timeout": 30
      }]
    }],
    "PreCompress": [{
      "hooks": [{
        "type": "command",
        "command": "/absolute/path/to/callosum/.venv/bin/python /absolute/path/to/callosum/hooks/precompress_hook.py",
        "timeout": 30
      }]
    }]
  }
}
```

> **Windows:** Use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## Install — Claude Code

Add to `.claude/settings.local.json`:

```json
{
  "hooks": {
    "Stop": [{
      "matcher": "*",
      "hooks": [{
        "type": "command",
        "command": "python /absolute/path/to/callosum/hooks/save_hook.py",
        "timeout": 30
      }]
    }],
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "python /absolute/path/to/callosum/hooks/precompress_hook.py",
        "timeout": 30
      }]
    }]
  }
}
```

## Install — Codex CLI (OpenAI)

Add to `.codex/hooks.json`:

```json
{
  "Stop": [{
    "type": "command",
    "command": "python /absolute/path/to/callosum/hooks/save_hook.py",
    "timeout": 30
  }],
  "PreCompact": [{
    "type": "command",
    "command": "python /absolute/path/to/callosum/hooks/precompress_hook.py",
    "timeout": 30
  }]
}
```

## Configuration

The hooks are self-contained Python scripts that output static prompts. Edit the `SAVE_PROMPT` or `PRECOMPRESS_PROMPT` constants in the respective `.py` files to customize the instructions given to the AI.

### callosum CLI

The relevant commands are:

```bash
callosum mine <dir>               # Mine all files in a directory
callosum mine <dir> --mode convos # Mine conversation transcripts only
```

The hooks resolve the repo root automatically from their own path, so they work regardless of where you install the repo.

## How It Works (Technical)

### Save Hook (Stop event)

```
User sends message → AI responds → CLI fires Stop hook
                                            ↓
                                    save_hook.py outputs save prompt
                                            ↓
                              ┌─── AI decides nothing important ──→ skips save
                              │
                              └─── AI has new decisions/context ──→ calls callosum_save_session
```

### PreCompact Hook

```
Context window getting full → CLI fires PreCompact
                                        ↓
                                precompress_hook.py outputs emergency save prompt
                                        ↓
                                AI saves everything
                                        ↓
                                Compaction proceeds
```

No counting needed — compaction always warrants a save.

## Debugging

Check the hook log:
```bash
cat ~/.callosum/hook_state/hook.log
```

Example output:
```
[14:30:15] Session abc123: 12 exchanges, 12 since last save
[14:35:22] Session abc123: 15 exchanges, 15 since last save
[14:35:22] TRIGGERING SAVE at exchange 15
[14:40:01] Session abc123: 18 exchanges, 3 since last save
```

## Cost

**Zero extra tokens.** The hooks are Python scripts that run locally. They don't call any API. The only "cost" is the AI spending a few seconds organizing memories at each checkpoint — and it's doing that with context it already has loaded.
