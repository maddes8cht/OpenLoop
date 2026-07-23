# CLI Reference

## Usage

```
python openloop.py [OPTIONS]
```

## Flags

| Flag | Description |
|---|---|
| `--cli` | Run in headless CLI mode (no GUI). Requires `--workflow`. |
| `--workflow <path>` | Path to a workflow JSON file. In GUI mode, pre-loads the workflow. In CLI mode, executes it immediately. |
| `--workdir <path>` | Override the working directory for agent subprocesses. Takes precedence over `openloop.json` and workflow settings. |
| `--init-script <cmd>` | Override the init script/command run before each agent invocation. Takes precedence over `openloop.json` and workflow settings. |
| `--opencode-defaults <json>` | JSON string overriding opencode defaults for all agents (e.g., `'{"model":"gpt-4o","agent":"plan"}'`). Merges with config/workflow settings. |
| `--config <path>` | Path to configuration file (default: `openloop.json` in CWD, falls back to `openloop.json` next to `openloop.py`) |
| `--verbose`, `-v` | Stream agent stdout/stderr to terminal during execution |
| `--help` | Show help message and exit |

## Exit Codes

| Code | Condition |
|---|---|
| `0` | Workflow completed successfully (`termination_reason == "completed"`) |
| `1` | Workflow finished abnormally (max loops, agent error, stopped) |

## Environment

The `opencode` binary must be available in `PATH` (or configured via `opencode_binary` in `openloop.json`).

## Examples

```bash
# Run a workflow headless
python openloop.py --cli --workflow workflows/test_generation.json

# Run with detailed live agent output
python openloop.py --cli --workflow workflows/test_generation.json --verbose

# Run with custom working directory and init script
python openloop.py --cli --workflow workflows/test_generation.json ^
    --workdir C:\projects\myapp --init-script "conda activate myenv"

# Launch the GUI with a pre-loaded workflow
python openloop.py --workflow workflows/test_generation.json
```
