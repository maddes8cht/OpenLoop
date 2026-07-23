# Agent Definitions

Agent definition files use Markdown (`.md`) with YAML frontmatter.  
They are stored in `agents/` (configurable via `agents_dir` in `openloop.json`).

## File Format

```markdown
---
name: amala
role: author
can_complete: false
expected_output_format: xml_tag
---

# Role: AMALA — Test Author

You are AMALA, a meticulous test author...
```

## Frontmatter Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | Yes | — | Agent identifier used in workflow slots |
| `role` | No | `""` | Short role description (e.g. `author`, `auditor`) |
| `can_complete` | No | `false` | Whether this agent may set `is_complete=true`. If `false`, `is_complete=true` is forced to `false` and a warning is logged. Truthy values: `"1"`, `"true"`, `"yes"`, `"on"`. |
| `expected_output_format` | No | `json_block` | Hint for expected output format (`json_block` or `xml_tag`) |

> **Note:** Without `can_complete: true`, an agent's `is_complete=true` is always blocked. Agents with roles `auditor`, `approver`, `finalizer`, or `finalization` can complete even without an explicit `can_complete` field (fallback).

## System Prompt

Everything after the closing `---` frontmatter delimiter becomes the **system prompt**.  
The engine appends the current `WorkflowState` as a JSON block and instructions for the state update protocol.

---

## Understanding the State

OpenLoop maintains a shared `WorkflowState` that is **injected into every agent's prompt** as a JSON block. Think of it as a global scratchpad that all agents can read and write.

### State Fields

```json
{
  "current_phase": "loop",
  "iteration": 2,
  "is_complete": false,
  "termination_reason": "",
  "payload": {
    "feedback": "Coverage is 78%",
    "coverage": 78.5
  }
}
```

| Field | Type | Meaning | Who sets it |
|---|---|---|---|
| `current_phase` | string | Engine phase (`preparation`, `loop`, `finalization`) | Engine only |
| `iteration` | integer | Current loop iteration (starts at 1) | Engine only |
| `is_complete` | boolean | Whether the workflow is done | Agents (with authorization) |
| `termination_reason` | string | Why the workflow ended | Engine + Agents |
| `payload` | dict | **Custom data shared between agents** | Agents |

### What agents can safely change

- **`is_complete`** — Set to `true` when the workflow should terminate. Only honored if the agent has `can_complete: true` or an authorized role.
- **`termination_reason`** — Optionally set to a descriptive string (e.g. `"all_tests_pass"`).
- **`payload`** — The only field designed for agent-specific data. Use it for anything agents need to communicate.

### What agents should NOT touch

- **`current_phase`** — Managed by the engine. Agent changes will be overwritten at the next phase transition.
- **`iteration`** — Managed by the engine. Changes are overwritten each loop.
- **`meta`** — Reserved for run metadata (run ID, start time).

---

## How to Update State

Agents communicate state changes by including a `<state_update>` XML tag in their response text:

```
<state_update>
{"is_complete": false, "payload": {"feedback": "Missing edge case in auth.py line 42", "coverage": 78.5}}
</state_update>
```

The engine parses every agent's stdout for `<state_update>` tags via `StateParser.extract_state_update()`. If the tag is missing or contains invalid JSON, the engine makes up to **2 correction attempts** (re-running the agent with `-c`).

### Normalization

Before merging, the engine runs `_normalize_state_update()` which:

1. **Moves unknown keys** into `payload` — any key that is not `is_complete`, `termination_reason`, or `payload` is silently placed inside `payload` via `setdefault`. A NOTE is logged.
2. **Blocks unauthorized completion** — if the agent lacks `can_complete: true` or an authorized role, `is_complete=true` is forced to `false` and a WARNING is logged. A `completion_blocked` flag is added to `payload`.
3. **Ignores protected keys** — `current_phase`, `iteration`, `meta` are silently dropped if present in the state update.

### Top-level keys vs. payload

The engine recognizes exactly **three** top-level keys in a state update:

| Top-level key | Behavior |
|---|---|
| `is_complete` | ✓ Honored only if agent is authorized to complete |
| `termination_reason` | ✓ Correct use |
| `payload` | ✓ **The intended container for all custom data** |
| `current_phase` | 🔒 Ignored (protected) |
| `iteration` | 🔒 Ignored (protected) |
| `meta` | 🔒 Ignored (protected) |
| **Any other key** | ➡️ Moved into `payload` with a NOTE log |

**Rule of thumb:** Everything specific to your agent workflow goes inside `payload`. Only `is_complete` and `termination_reason` belong at the top level.

```json
// GOOD: Custom data inside payload
{
  "is_complete": true,
  "payload": {
    "tests_written": 12,
    "bugs_found": 0
  }
}

// GOOD: Unknown keys are moved into payload automatically
{
  "is_complete": true,
  "tests_written": 12,
  "bugs_found": 0
}
// → After normalization: is_complete=true, payload={tests_written: 12, bugs_found: 0}
```

If you include an unknown top-level key, the engine logs:
```
NOTE: Moved non-top-level key(s) into payload: ['bugs_found', 'tests_written']
```

---

## Completion Authorization

An agent may set `is_complete=true` if **any** of these conditions is met:

1. The frontmatter contains `can_complete: true`
2. The frontmatter contains `can_complete: yes` / `can_complete: 1` / `can_complete: on`
3. The agent's `role` is one of: `auditor`, `approver`, `finalizer`, `finalization`

If none of these conditions is met and the agent returns `is_complete=true`, the engine:

- Forces `is_complete` to `false`
- Adds `completion_blocked: true` and `completion_blocked_reason: "<name> is not authorized to complete the workflow"` to `payload`
- Logs a WARNING

---

## Writing an Agent: Best Practices

### 1. Tell the agent how to read state

The injected state JSON appears under a `# Current State` heading. Refer to it clearly in your instructions:

```markdown
## Instructions

1. Read the Current State below. It contains:
   - The target module path and code in `payload.target_module`
   - Previous feedback in `payload.feedback` (if any)
2. ...
```

### 2. Tell the agent how to write state

Always include a `<state_update>` example in your agent file. This serves as a template the LLM can follow.

```markdown
## State Update

Your final response MUST end with a `<state_update>` block:

<state_update>
{"is_complete": false, "payload": {"result": "wrote 12 tests", "tests_written": 12}}
</state_update>
```

### 3. Name payload keys thoughtfully

- Use `snake_case` for consistency with the state schema.
- Prefix keys with the agent name if multiple agents might write similar data (e.g. `amala_tests_written`, `vera_feedback`), or agree on shared keys within your workflow.
- Keep the payload shallow where possible — avoid deeply nested structures that agents may struggle to produce correctly.

### 4. Coordinate keys within your workflow

Agents in a workflow agree on which `payload` keys they read and write. Document these conventions in the agent files. For a two-agent author/auditor workflow:

| Key | Written by | Read by |
|---|---|---|
| `tests_written` | Amala | Vera |
| `bugs_found` | Amala | Vera |
| `feedback` | Vera | Amala |
| `coverage` | Vera | Amala, Engine (in `end_state_condition`) |

### 5. Complete agent example

```markdown
---
name: my_agent
role: custom_role
can_complete: false
expected_output_format: xml_tag
---

# Role: MY_AGENT

You are MY_AGENT, responsible for [describe purpose].

## Instructions

1. Read the Current State below.
   - `payload.input_data` contains the data to process.
   - If `payload.previous_result` exists, build on it.

2. Perform [your task].

3. End with a `<state_update>` block.

## State Update

Your final response MUST contain exactly one `<state_update>` tag:

<state_update>
{"is_complete": false, "payload": {"my_result": "processed 42 items", "items_processed": 42}}
</state_update>

## Critical Rules

- Set `is_complete: true` only when [condition].
- All custom data must go inside the `payload` object.
- Do NOT write files, reports, or Markdown documents as a substitute for the state update.
```
