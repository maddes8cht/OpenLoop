# CLI Reference

## Usage

```
python openloop.py [OPTIONS]
```

## Flags

| Flag | Description |
|---|---|
| `--cli` | Run in headless CLI mode (no GUI). Requires `--workflow` or `--resume`. |
| `--workflow <path>` | Path to a workflow JSON file. In GUI mode, pre-loads the workflow. In CLI mode, executes it immediately. Mutually exclusive with `--resume`. |
| `--resume <path>` | Resume an interrupted run from a log (`openloop-run-*.log`) or checkpoint (`openloop-run-*.json`) file. The checkpoint's stored workflow definition and state are used; the resume appends to the original log. Mutually exclusive with `--workflow`. |
| `--max-loops <n>` | Override `max_loops` for the run. Use a higher value to continue past a `max_loops_reached` termination. |
| `--workdir <path>` | Override the working directory for agent subprocesses. Takes precedence over `openloop.json` and workflow settings. Ignored with `--resume` (the checkpoint's stored workflow wins). |
| `--init-script <cmd>` | Override the init script/command run before each agent invocation. Takes precedence over `openloop.json` and workflow settings. Ignored with `--resume`. |
| `--opencode-defaults <json>` | JSON string overriding opencode defaults for all agents (e.g., `'{"model":"gpt-4o","agent":"plan"}'`). Merges with config/workflow settings. |
| `--config <path>` | Path to configuration file (default: `openloop.json` in CWD, falls back to `openloop.json` next to `openloop.py`) |
| `--verbose`, `-v` | Stream agent stdout/stderr to terminal during execution |
| `--log-dir <path>` | Log directory (overrides config and workflow `log_dir`). Ignored with `--resume` (the resume appends to the original log) |
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

# Resume an interrupted run (accepts the .log or its .json checkpoint)
python openloop.py --cli --resume .openloop/openloop-run-demo-20260101-120000.log

# Continue past a max_loops_reached termination with a higher limit
python openloop.py --cli --resume .openloop/openloop-run-demo-20260101-120000.json --max-loops 20

# Launch the GUI with a pre-loaded workflow
python openloop.py --workflow workflows/test_generation.json
```

## Resuming interrupted runs

Whenever a run stops abnormally, the engine leaves a checkpoint file next to
the run's log with the same base name and a `.json` extension (e.g.
`openloop-run-demo-20260101-120000.json`). It stores the full workflow
definition, the workflow state, and the execution position (phase, iteration,
and the last completed agent). A resume:

- continues **after** the last completed agent boundary (never mid-agent);
- does **not** re-increment the iteration for the partially completed one;
- appends a second `<openloop_log>` root to the **same** log file, marked
  with a `# OPENLOOP RESUMED` line;
- deletes the checkpoint once the run completes successfully.

Whether a given termination is resumable is controlled by `resume_reasons` in
`openloop.json` (default: all reasons except `"completed"`).
