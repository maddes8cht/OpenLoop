# Configuration

Global settings are defined in `openloop.json`. OpenLoop searches in this order:

1. **CWD** – Current working directory (for project-specific config)
2. **OpenLoop directory** – Next to `openloop.py` (global/fallback config)

If neither exists, all fields fall back to their defaults.

The config file supports **JSONC** (JSON with Comments) – both `//` line comments and `/* */` block comments are stripped before parsing. Use the included `openloop.example.json` as a starting point.

## Schema

```json
{
  "agents_dir": "./agents",
  "workflows_dir": "./workflows",
  "opencode_binary": "opencode",
  "default_max_loops": 10,
  "workdir": "/path/to/project",
  "init_script": "conda activate myenv",
  "opencode_defaults": {
    "model": "anthropic/claude-sonnet-4",
    "agent": "plan",
    "variant": "high",
    "pure": true,
    "log_level": "INFO"
  }
}
```

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `agents_dir` | string | `"./agents"` | Directory containing agent `.md` files |
| `workflows_dir` | string | `"./workflows"` | Directory containing workflow `.json` files |
| `opencode_binary` | string | `"opencode"` | Path or name of the opencode binary |
| `default_max_loops` | integer | `10` | Default `max_loops` when a workflow does not specify one (must be >= 1) |
| `workdir` | string / null | `null` | Working directory for the opencode subprocess |
| `init_script` | string / null | `null` | Script or command to run before each opencode invocation |
| `opencode_defaults` | object | `{}` | Default flags for every `opencode run` invocation (see below) |

### `opencode_defaults` fields

| Field | Type | Description |
|---|---|---|
| `model` | string | Model in `provider/model` format (e.g., `anthropic/claude-sonnet-4`) |
| `agent` | string | OpenCode agent to use (e.g., `build`, `plan`) |
| `variant` | string | Reasoning effort (`high`, `max`, `minimal`) |
| `pure` | boolean | Run without external plugins (MCP/skills) |
| `log_level` | string | Log level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| `extra_args` | array | Additional raw CLI flags passed to `opencode run` |

## Override Priority

The full override chain:

```
CLI flag (--workdir / --init-script / --opencode-defaults)  ← highest
Workflow JSON field
openloop.json field                                            ← lowest
```

For `opencode_defaults`, values from each level are merged (not replaced). For example, if `openloop.json` sets `{"model": "claude-sonnet"}` and the workflow sets `{"agent": "plan"}`, the effective result is `{"model": "claude-sonnet", "agent": "plan"}`.

## Validation

On load, `Config.load()` validates:
- `agents_dir` and `workflows_dir` must be accessible (created if missing)
- `default_max_loops` must be >= 1

Invalid values raise a `ValueError`.
