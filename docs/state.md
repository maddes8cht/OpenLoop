# WorkflowState

The shared state is the "single source of truth" in OpenLoop.  
It is represented by the `WorkflowState` dataclass and passed between agents as JSON.

## State Lifecycle

The state flows through the system in a continuous six-step cycle:

```
                    ┌──────────────────────────────────────┐
                    │         ExecutionEngine              │
                    │                                      │
                    │  ┌──────────┐     ┌──────────────┐  │
                    │  │Workflow  │────►│ serialize to  │  │
                    │  │State     │     │ JSON          │  │
                    │  └───┬──────┘     └──────┬────────┘  │
                    │      │                   │           │
                    │      │  merge()    append to prompt  │
                    │      │                   │           │
                    │  ┌───▼──────┐     ┌──────▼────────┐  │
                    │  │ parse()  │◄────│  opencode run │  │
                    │  │<state_   │     │  (LLM sub-    │  │
                    │  │ update>  │     │  process)     │  │
                    │  └──────────┘     └───────────────┘  │
                    │                                      │
                    │  eval(end_state_condition) ──► next  │
                    │                               agent  │
                    └──────────────────────────────────────┘
```

1. **Serialize** — `WorkflowState.to_json()` produces a JSON representation of the current state.
2. **Inject** — The engine appends this JSON to the agent's system prompt as a markdown code block under `# Current State` (see `_build_prompt()` in `core/engine.py`).
3. **Execute** — The prompt is passed to `opencode run`, which invokes the LLM in a fresh, isolated context.
4. **Extract** — `StateParser.extract_state_update()` (in `core/parser.py`) searches the LLM's stdout for `<state_update>...</state_update>` (preferred) or a JSON code block (fallback).
5. **Merge** — The parsed dict is applied to `WorkflowState` via `merge()`. Only keys present in the update are changed; the `payload` dict is merged deeply.
6. **Evaluate** — After each agent, the engine evaluates `end_state_condition`. If true, the loop exits. Otherwise, the next agent in the sequence sees the updated state.

The same cycle applies in all three phases (preparation, loop, finalization). In the loop phase, the `iteration` counter is incremented before each full pass through the agent sequence.

## Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `current_phase` | string | `"preparation"` | Current execution phase (`preparation`, `loop`, `finalization`) |
| `iteration` | integer | `0` | Loop iteration counter (incremented each loop pass) |
| `is_complete` | boolean | `false` | Set by agents to signal completion |
| `termination_reason` | string | `""` | Why the workflow finished (`completed`, `max_loops_reached`, `stopped`, `agent_error:<name>`) |
| `payload` | dict | `{}` | Arbitrary key-value store shared between agents |

## Agent Output Format

Agents communicate state changes by including a JSON update in their output:

**XML tag (preferred):**
```xml
<state_update>
{
  "is_complete": true,
  "payload": {
    "coverage": 85
  }
}
</state_update>
```

**JSON code block (fallback):**
```json
{
  "is_complete": true,
  "payload": {
    "coverage": 85
  }
}
```

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

`StateParser.extract_state_update(text)` searches agent output for:

1. `<state_update>...</state_update>` XML tag (checked first)
2. ` ```json ... ``` ` or ` ``` ... ``` ` code blocks (fallback)

Returns the parsed dict or `None` if neither format is found.
