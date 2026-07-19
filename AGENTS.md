# OpenLoop

Zero-dependency Python 3.13+ orchestration engine for OpenCode.

## Commands

```powershell
python openloop.py                                          # Tkinter GUI mode
python openloop.py --cli --workflow workflows/test_generation.json  # headless
python openloop.py --cli --workflow wf.json --workdir . --init-script "conda activate myenv"
python openloop.py --cli --workflow wf.json --opencode-defaults '{"model":"gpt-4o","agent":"plan"}'
```

### Tests

```powershell
pytest tests\test_all.py                                    # unit tests (fast)
python tests\test_integration.py                             # integration (custom runner, Tier 2 needs `opencode` in PATH)
```

## Structure

| Path | Purpose |
|---|---|
| `openloop.py` | Entry point — dispatches to GUI or CLI |
| `core/` | `config.py`, `state.py`, `parser.py`, `agent.py`, `runner.py`, `engine.py` |
| `ui/app.py` | Tkinter GUI (706 lines) |
| `agents/*.md` | Agent definitions (YAML frontmatter + system prompt) |
| `workflows/*.json` | Workflow definitions |
| `docs/` | Reference docs for all subsystems |

## Setup

- No `pip install` needed — pure stdlib.
- `opencode` binary must be in `$PATH` for actual execution.
- Copy `openloop.example.json` → `openloop.json`, then edit. Config file supports JSONC (comments). The file is gitignored.
- Config search order: CWD → next to `openloop.py`.

## Architecture

Three phases run sequentially: **preparation** → **loop** → **finalization**.

Each agent invocation:
1. Serialize `WorkflowState` to JSON
2. Append it to the agent's system prompt under `# Current State`
3. Run `opencode run <prompt>`
4. Parse stdout for `<state_update>...</state_update>` (XML) or ````json...```` (fallback)
5. Merge parsed dict into state
6. Evaluate `end_state_condition` (restricted Python `eval` with `is_complete`, `iteration`, `termination_reason`, `phase`, `payload`)

### Gotchas

- Agent filenames in `agents/` match the name used in workflows (stem of `.md`).
- `preparation_agent` / `finalization_agent` (singular, legacy) and `preparation_agents` / `finalization_agents` (plural) are both accepted.
- `WorkflowState.merge()` does **deep merge** on `payload` (dict.update) but **replaces** top-level fields.
- `end_state_condition` runs in `{"__builtins__": {}}` — no builtins available.
- `OpenCodeOptions.pure`, once `True`, **cannot** be overridden back to `False` via merge (`override.pure if override.pure else self.pure`).
- `opencode_defaults` merges across config → workflow → CLI (each level overrides specific keys, not the whole dict).
- Init script extension detection: `.ps1` → `pwsh`, `.bat`/`.cmd` → `call`, `.sh` → `sh`, else treated as inline command.
- `max_loops` defaults to config `default_max_loops` (10) if not in workflow data.
- Exit codes: 0 = completed, 1 = max_loops/agent_error/stopped.
