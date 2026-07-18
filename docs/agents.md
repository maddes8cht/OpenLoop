# Agent Definitions

Agent definition files use Markdown (`.md`) with YAML frontmatter.  
They are stored in `agents/` (configurable via `agents_dir` in `config.json`).

## File Format

```markdown
---
name: amala
role: author
expected_output_format: xml_tag
---

# Role: AMALA - Test Author

You are AMALA, a meticulous test author...

## Instructions

1. Read the Current State payload...
```

## Frontmatter Fields

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | Yes | — | Agent identifier used in workflow slots |
| `role` | No | `""` | Short role description (e.g. `author`, `auditor`) |
| `expected_output_format` | No | `json_block` | Hint for the expected output structure (`json_block` or `xml_tag`) |

## System Prompt

Everything after the closing `---` frontmatter delimiter becomes the **system prompt**.  
The engine appends the current `WorkflowState` as a JSON block and instructions to output a `<state_update>` XML tag.

## State Update Parsing

Agents communicate state changes via:

1. **XML tag** (preferred): `<state_update>{...}</state_update>`
2. **JSON code block**: ` ```json {..."is_complete": true} ``` `

The engine merges the parsed fields into the shared `WorkflowState` via `merge()`.

## Agent Loader (`core/agent.py`)

- `AgentLoader(agents_dir)` — scans `.md` files in the directory
- `list_agents()` — returns filenames without `.md` extension
- `get_agent(name)` — parses a single agent file into `AgentDefinition`
- Frontmatter parsing is strict: file **must** start with `---` and contain `name`
