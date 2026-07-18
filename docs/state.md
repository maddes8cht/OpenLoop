# WorkflowState

The shared state is the "single source of truth" in OpenLoop.  
It is represented by the `WorkflowState` dataclass and passed between agents as JSON.

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
