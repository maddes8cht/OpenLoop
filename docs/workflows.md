# Workflow Definitions

Workflow definitions are JSON files stored in `workflows/` (configurable via `workflows_dir` in `openloop.json`).

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
| `workdir` | string / null | `null` | Working directory for the opencode subprocess (overrides `openloop.json`) |
| `init_script` | string / null | `null` | Script/command run before each agent invocation (overrides `openloop.json`) |
| `opencode_defaults` | object | `{}` | Default flags for `opencode run` (see Configuration docs). Merges with `openloop.json` — workflow values override config values. |

> **Completion authority:** An agent can only set `is_complete=true` if its definition has `can_complete: true` or its role is one of `auditor`, `approver`, `finalizer`, `finalization`. See [Agent Definitions](agent-definitions.md) for details.

## end_state_condition

A Python expression evaluated in a restricted namespace with these variables:

| Variable | Type | Description |
|---|---|---|
| `is_complete` | bool | Direct access to `state.is_complete` |
| `iteration` | int | Current loop iteration count |
| `termination_reason` | str | Current termination reason |
| `phase` | str | Current phase name |
| `payload` | dict | Arbitrary state payload set by agents |
| `meta` | dict | Run metadata (e.g. `{"run_id": "..."}`) — also accessible via `payload._openloop` |

Built-in functions are not available — only the variables above.

Examples:

```
"is_complete == True"
"payload.get('coverage', 0) >= 80"
"iteration >= 3 and payload.get('tests_written', 0) > 5"
```

## Override Order

### `workdir` and `init_script`

1. CLI flag (`--workdir` / `--init-script`) — highest priority
2. Workflow JSON field — medium priority
3. `openloop.json` field — lowest priority

### `opencode_defaults`

1. CLI flag (`--opencode-defaults`) — highest priority
2. Workflow JSON `opencode_defaults` — medium priority
3. `openloop.json` `opencode_defaults` — lowest priority

Values are **merged** (not replaced) at each level: a workflow specifying only `{"model":"gpt-4o"}` will preserve the config's `agent` setting.
