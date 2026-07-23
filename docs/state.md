# WorkflowState

The shared state is the "single source of truth" in OpenLoop.  
It is represented by the `WorkflowState` dataclass and passed between agents as JSON.

## State Lifecycle

The state flows through the system in a continuous six-step cycle:

```
                    ┌──────────────────────────────────────────┐
                    │         ExecutionEngine                  │
                    │                                          │
                    │  ┌──────────┐     ┌──────────────────┐  │
                    │  │Workflow  │────►│ serialize to      │  │
                    │  │State     │     │ JSON + append to  │  │
                    │  └───┬──────┘     │ agent prompt      │  │
                    │      │            └────────┬─────────┘  │
                    │      │                     │            │
                    │      │ merge()   ┌─────────▼─────────┐  │
                    │      │           │  opencode run     │  │
                    │  ┌───▼──────┐    │  (LLM subprocess  │  │
                    │  │ normalize │    │  outputs          │  │
                    │  │ + merge   │◄───│  <state_update>   │  │
                    │  │           │    │  in stdout)       │  │
                    │  └──────────┘    └───────────────────┘  │
                    │                                          │
                    │  eval(end_state_condition) ──► next      │
                    │                               agent      │
                    └──────────────────────────────────────────┘
```

1. **Serialize** — `WorkflowState.to_json()` produces a JSON representation of the current state.
2. **Inject** — The engine appends this JSON to the agent's system prompt as a markdown code block under `# Current State` (see `_build_prompt()` in `core/engine.py`).
3. **Execute** — The prompt is written to `current_prompt.md` in the configured `log_dir`, then `opencode run --file <path> --dir <workdir> "Follow the instructions..."` is launched.
4. **Parse** — The engine calls `StateParser.extract_state_update(result.output)` on the agent's stdout. It searches for `<state_update>...</state_update>` XML tags first, then falls back to ` ```json ... ``` ` code blocks. If no valid state update is found, up to 2 correction attempts are made via `opencode run -c`, then the missing-state handler is invoked (interactive prompt in TTY, abort otherwise).
5. **Normalize & Merge** — Before merging, `_normalize_state_update()` adjusts the parsed dict (see Normalization below). The result is applied to `WorkflowState` via `merge()`. Only keys present in the update are changed; the `payload` dict is merged deeply.
6. **Evaluate** — After each agent, the engine evaluates `end_state_condition`. If true, the loop exits. Otherwise, the next agent in the sequence sees the updated state.

The same cycle applies in all three phases (preparation, loop, finalization). In the loop phase, the `iteration` counter is incremented before each full pass through the agent sequence.

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `current_phase` | string | `"preparation"` | Current execution phase (`preparation`, `loop`, `finalization`) |
| `iteration` | integer | `0` | Loop iteration counter (incremented each loop pass) |
| `is_complete` | boolean | `false` | Set by agents to signal completion (subject to authorization) |
| `termination_reason` | string | `""` | Why the workflow finished (`completed`, `max_loops_reached`, `stopped`, `agent_error:<name>`, `missing_state:<name>`) |
| `payload` | dict | `{}` | Arbitrary key-value store shared between agents |

## State Update Format

Agents communicate state changes by including a `<state_update>` XML tag in their response text. This is the **only** mechanism — the engine no longer reads a state file.

```
<state_update>
{"is_complete": true, "payload": {"coverage": 85}}
</state_update>
```

The engine searches each agent's stdout via `StateParser.extract_state_update()` (see `core/parser.py`). Priority order:

1. `<state_update>...</state_update>` XML tag (checked first)
2. ` ```json ... ``` ` or ` ``` ... ``` ` code blocks (fallback)

Returns the parsed dict or `None` if neither format is found.

## Normalization

Before merging, `_normalize_state_update()` applies these transformations:

| Rule | What happens |
|---|---|
| **Unknown top-level keys** | Moved into `payload` via `setdefault`. A NOTE is logged. |
| **Protected keys** (`current_phase`, `iteration`, `meta`) | Silently ignored with a NOTE log. |
| **Unauthorized `is_complete=true`** | Forced to `false`; `completion_blocked: true` added to payload. A WARNING is logged. |

This runs before `merge()`, so agents that accidentally put data at the wrong level still have their data preserved — it just ends up in `payload`.

## Merge Behavior

The `merge(update)` method applies changes from an agent state update:

- Only keys present in the update are changed
- `payload` is merged **deeply** — top-level keys in the update's `payload` are added/overwritten, other keys are preserved
- Strings, ints, and bools are type-coerced

```python
state = WorkflowState(payload={"a": 1, "b": 2})
state.merge({"payload": {"b": 3, "c": 4}})
# Result: payload = {"a": 1, "b": 3, "c": 4}
```

## Parsing (`core/parser.py`)

`StateParser.extract_state_update(text)` is called on every agent's stdout output. It searches for:

1. `<state_update>...</state_update>` XML tag (case-insensitive, DOTALL)
2. ` ```json ... ``` ` or ` ``` ... ``` ` code blocks

Returns the parsed dict or `None`. This is the **only** mechanism for receiving state updates from agents — there is no file-based fallback.
