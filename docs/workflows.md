# Workflow Definitions

Workflow definitions are JSON files stored in `workflows/` (configurable via `workflows_dir` in `config.json`).

## Schema

```json
{
  "preparation_agents": ["prepper"],
  "loop_agents": ["amala", "vera"],
  "finalization_agents": ["reporter"],
  "end_state_condition": "payload.get('coverage', 0) >= 80",
  "max_loops": 5,
  "finalize_on_abort": true,
  "workdir": "/path/to/project",
  "init_script": "conda activate myenv"
}
```

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `preparation_agents` | string / array of strings | `[]` | Agent(s) run once before the loop. Accepts single string or list. Also accepts legacy key `preparation_agent`. |
| `loop_agents` | array of strings | `[]` | Agent(s) executed in sequence each iteration |
| `finalization_agents` | string / array of strings | `[]` | Agent(s) run once after the loop. Accepts single string or list. Also accepts legacy key `finalization_agent`. |
| `end_state_condition` | string | `"is_complete == True"` | Python expression evaluated after each agent to decide loop termination |
| `max_loops` | integer | `10` | Hard limit on loop iterations |
| `finalize_on_abort` | boolean | `false` | If `true`, finalization runs even when `max_loops` is reached |
| `workdir` | string / null | `null` | Working directory for the opencode subprocess (overrides `config.json`) |
| `init_script` | string / null | `null` | Script/command run before each agent invocation (overrides `config.json`) |

## end_state_condition

A Python expression evaluated in a restricted namespace with these variables:

| Variable | Type | Description |
|---|---|---|
| `is_complete` | bool | Direct access to `state.is_complete` |
| `iteration` | int | Current loop iteration count |
| `termination_reason` | str | Current termination reason |
| `phase` | str | Current phase name |
| `payload` | dict | Arbitrary state payload set by agents |

Built-in functions are not available — only the variables above.

Examples:

```
"is_complete == True"
"payload.get('coverage', 0) >= 80"
"iteration >= 3 and payload.get('tests_written', 0) > 5"
```

## Override Order

For `workdir` and `init_script`:

1. CLI flag (`--workdir` / `--init-script`) — highest priority
2. Workflow JSON field — medium priority
3. `config.json` field — lowest priority
