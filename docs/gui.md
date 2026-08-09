# GUI — Workflow Builder

The GUI is a Tkinter-based visual workflow editor.  
Launch it with:

```bash
python openloop.py
```

## Layout

```
┌─────────────────────────────────────────────────────┐
│  Toolbar: [Load] [Save] [Execute] [Stop]            │
├──────────┬──────────┬───────────────────────────────┤
│ Agent    │ Workflow │  Preview                      │
│ Pool     │ Builder  │                               │
│          │          │  (agent markdown               │
│  [amala] │ Prep     │   rendered with                │
│  [vera]  │  [prep1] │   formatting)                  │
│ [proteus]│          │                               │
│          │ Loop     │                               │
│          │  [amala] │                               │
│          │  [vera]  │                               │
│          │          │                               │
│          │ Final    │                               │
│          │  [final1]│                               │
│          │          │                               │
│          │ Config   │                               │
│          │  max: 10 │                               │
├──────────┴──────────┴───────────────────────────────┤
│  Status Bar                                          │
└─────────────────────────────────────────────────────┘
```

## Zones

### Agent Pool (left column)
Lists all `.md` files found in the configured `agents_dir`.  
Click an agent to preview its content in the Preview pane.

### Workflow Builder (center column)
Four sub-sections:

| Zone | Description |
|---|---|
| **Preparation** | Agents run once before the loop. Supports multiple agents. |
| **Loop** | Agent sequence executed each iteration. |
| **Finalization** | Agents run after the loop completes. Supports multiple agents. |
| **Configuration** | `max_loops`, `end_state_condition`, `finalize_on_abort`, `workdir`, `init_script`, model/agent/variant/pure mode (OpenCode Defaults) |

Use the buttons below each zone to add/remove agents:
- **Add Prep Agent** / **Add Final Agent** — moves selected agent from the pool to the zone
- **Remove** — removes selected agent from the zone
- **Up / Down** — reorder agents within the zone

### Preview Pane (right column)
Shows the selected agent's `.md` file with Markdown formatting rendered using the built-in `markdown_renderer` module (no external dependencies).

## Toolbar

| Button | Action |
|---|---|
| **Load Workflow** | Opens a file dialog to load a `.json` workflow file |
| **Save Workflow** | Opens a file dialog to save the current workflow |
| **Execute** | Runs the current workflow in the engine (logs to console) |
| **Stop** | Signals the engine to stop after the current agent finishes |
 | **Continue** | Enabled after a run stops with a resumable reason. Confirms the auto-selected checkpoint (matching the active run's log) and resumes it |
| **LoopLog** | Opens the standalone LoopLog viewer for the active run's log |

## CLI Mode

Use `--cli` to skip the GUI entirely:

```bash
python openloop.py --cli --workflow workflows/test_generation.json
```

## Continue (resuming interrupted runs)

After a run stops abnormally, the **Continue** button becomes active. It is
only enabled when a resumable checkpoint exists for the run's own log, so
clicking it resumes exactly that run.

A confirmation dialog shows which workflow, log file, and checkpoint will be
used, along with the run id, termination reason, resume position, and when
the checkpoint was created. It offers **Continue**, **Abort**, and
**Select another file…**:

- **Continue** resumes the auto-selected checkpoint (the one next to the
  active run's log, same name with a `.json` extension).
- **Abort** cancels.
- **Select another file…** opens a list of all resumable checkpoints
  (`openloop-run-*.json`) in the configured log directory, newest first, each
  annotated with run id, termination reason, phase/iteration, and creation
  time — useful when something unusual happened and a different run needs to
  be continued.

Checkpoints whose termination reason is not allowed by the `resume_reasons`
config filter are excluded from the list (and the Continue button stays
disabled for them), matching the CLI behavior.

If no checkpoints are found, the GUI offers to pick an OpenLoop `.log` file
instead and derives its checkpoint path by swapping the extension.

See [`resume.md`](resume.md) for the full resume mechanics and the
`resume_reasons` config setting.
