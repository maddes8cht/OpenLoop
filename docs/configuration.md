# Configuration

Global settings are defined in `config.json` at the project root.  
If the file is missing, all fields fall back to their defaults.

## Schema

```json
{
  "agents_dir": "./agents",
  "workflows_dir": "./workflows",
  "opencode_binary": "opencode",
  "default_max_loops": 10,
  "workdir": "/path/to/project",
  "init_script": "conda activate myenv"
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

## Override Priority

For `workdir` and `init_script`:

```
CLI flag (--workdir / --init-script)   ← highest
Workflow JSON field
config.json field                       ← lowest
```

## Validation

On load, `Config.load()` validates:
- `agents_dir` and `workflows_dir` must be accessible (created if missing)
- `default_max_loops` must be >= 1

Invalid values raise a `ValueError`.
