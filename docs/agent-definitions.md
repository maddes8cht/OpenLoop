# Agent Definitions

Agent definition files use Markdown (`.md`) with YAML frontmatter.  
They are stored in `agents/` (configurable via `agents_dir` in `openloop.json`).

## File Format

```markdown
---
name: amala
role: author
expected_output_format: xml_tag
---

# Role: AMALA - Test Author

You are AMALA, a meticulous test author...
```

## Frontmatter Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | Yes | — | Agent identifier used in workflow slots |
| `role` | No | `""` | Short role description (e.g. `author`, `auditor`) |
| `expected_output_format` | No | `json_block` | Hint for expected output format (`json_block`, `xml_tag`, or `state_file`) |

## System Prompt

Everything after the closing `---` frontmatter delimiter becomes the **system prompt**.  
The engine appends the current `WorkflowState` as a JSON block and instructions to write state to `.openloop/state_update.json`.

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
| `is_complete` | boolean | Whether the workflow is done | Agents |
| `termination_reason` | string | Why the workflow ended | Engine + Agents |
| `payload` | dict | **Custom data shared between agents** | Agents |

### What agents can safely change

- **`is_complete`** — Set to `true` when the workflow should terminate.
- **`termination_reason`** — Optionally set to a descriptive string (e.g. `"all_tests_pass"`).
- **`payload`** — The only field designed for agent-specific data. Use it for anything agents need to communicate.

### What agents should NOT touch

- **`current_phase`** — Managed by the engine. Agent changes will be overwritten at the next phase transition.
- **`iteration`** — Managed by the engine. Changes are overwritten each loop.

---

## How to Update State

Agents communicate state changes by writing a JSON file to `.openloop/state_update.json`:

```json
{
  "is_complete": false,
  "payload": {
    "feedback": "Missing edge case in auth.py line 42",
    "coverage": 78.5
  }
}
```

The engine creates the `.openloop/` directory before each agent run and reads the file after. If the file is missing or contains invalid JSON, the engine makes up to **2 correction attempts** (re-running the agent with `-c`). As a last resort, it falls back to parsing `<state_update>` XML tags from stdout.

### Top-level keys vs. payload

The engine recognizes exactly **five** top-level keys in a state update:

| Top-level key | Behavior |
|---|---|
| `current_phase` | Stored but engine may overwrite |
| `iteration` | Stored but engine may overwrite |
| `is_complete` | ✓ Correct use |
| `termination_reason` | ✓ Correct use |
| `payload` | ✓ **The intended container for all custom data** |
| **Any other key** | ❌ Dropped with warning |

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

// BAD: Custom data at top level will be dropped
{
  "is_complete": true,
  "tests_written": 12,
  "bugs_found": 0
}
```

If you include an unknown top-level key, the engine will log a warning like:
```
WARNING: Unknown top-level key(s) in state update from 'amala': ['phase']. These will be ignored. Put custom data inside 'payload' instead.
```

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

Always include a `.openloop/state_update.json` example in your agent file. This serves as a template the LLM can follow.

```markdown
## State Update

At the end of your work, write the current state to `.openloop/state_update.json`:

```json
{
  "is_complete": false,
  "payload": {
    "result": "wrote 12 tests",
    "tests_written": 12
  }
}
```
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
expected_output_format: state_file
---

# Role: MY_AGENT

You are MY_AGENT, responsible for [describe purpose].

## Instructions

1. Read the Current State below.
   - `payload.input_data` contains the data to process.
   - If `payload.previous_result` exists, build on it.

2. Perform [your task].

3. Write the updated state to `.openloop/state_update.json`.

## State Update

Write the current state to `.openloop/state_update.json`:

```json
{
  "is_complete": false,
  "payload": {
    "my_result": "processed 42 items",
    "items_processed": 42
  }
}
```

## Critical Rules

- Set `is_complete: true` only when [condition].
- All custom data must go inside the `payload` object.
```
