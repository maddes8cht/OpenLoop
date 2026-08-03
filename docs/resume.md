# Resuming Interrupted Runs

OpenLoop can resume a workflow that stopped abnormally — after a timeout,
agent error, missing state update, user stop, or `max_loops_reached` — instead
of starting over from scratch.

## How it works

While a run executes, the engine writes a **checkpoint** after every completed
agent boundary (the atomic write is tmp-file + rename). The checkpoint lives
next to the run's log with the same base name and a `.json` extension:

```
.openloop/openloop-run-demo-20260101-120000.log
.openloop/openloop-run-demo-20260101-120000.json
```

A checkpoint stores:

| Field | Purpose |
|---|---|
| `workflow` | The full workflow definition (agents, max_loops, end condition, opencode defaults, ...) so a resume needs no workflow file |
| `state` | The serialized `WorkflowState` (payload, iteration, meta/run_id, ...) |
| `position` | The resume point: `phase`, `iteration`, and the `agent_index` of the last **completed** agent |
| `run_id` | The original run id (preserved across the resume) |
| `log_path` | Absolute path of the log the resume will append to |

When the run terminates abnormally, the checkpoint is refreshed with the final
termination reason. On successful completion the checkpoint is **deleted**.

## Resume semantics

- Resuming continues **after** the last completed agent boundary — a failed or
  timed-out agent runs again from its start.
- The iteration number of a partially completed iteration is **not**
  re-incremented; only the remaining agents of that iteration run.
- A `max_loops_reached` resume requires a higher `--max-loops` override to
  make progress.
- The resume appends a second `<openloop_log>` root to the **same** log file,
  introduced by a `# OPENLOOP RESUMED at <timestamp> (run_id: ..., reason: ...)`
  marker. The LoopLog viewer renders the two roots as separate top-level
  sections.
- The workflow state (payload) is preserved, so work done before the
  interruption is not lost.

## CLI

```bash
# Resume from a log or its checkpoint (either works)
python openloop.py --cli --resume .openloop/openloop-run-demo-20260101-120000.log

# Continue past max_loops_reached with a higher limit
python openloop.py --cli --resume .openloop/openloop-run-demo-20260101-120000.json --max-loops 20
```

`--resume` is mutually exclusive with `--workflow`.

## GUI

The **Continue** toolbar button enables after a run stops with a resumable
reason. It lists available checkpoints in the log directory (annotated with
run id + reason) and resumes the selection; if none are found it offers to
pick a `.log` file directly.

## Configuration: `resume_reasons`

Which terminations are resumable is controlled by `resume_reasons` in
`openloop.json`:

- `null` (default): resumable for every reason except `"completed"`.
- A list of prefixes matched against the termination reason. Examples:
  `"stopped"`, `"max_loops_reached"`, `"agent_error"` (matches
  `"agent_error:a"`), `"timeout"` (matches `"timeout:a:600"`),
  `"missing_state"` (matches `"missing_state:a"`).

```json
{
  "resume_reasons": ["stopped", "max_loops_reached", "agent_error", "timeout", "missing_state"]
}
```

## Timeouts

`RunResult` gained a `timed_out` flag. A timed-out agent terminates the run
with reason `timeout:<agent>:<seconds>` (instead of a generic `agent_error`),
and the run can then be resumed from the last completed agent.
