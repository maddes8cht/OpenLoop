# OpenLoop
A simple orchestration engine for OpenCode

**OpenLoop** is a zero-dependency, local-first orchestration engine specifically built for [**OpenCode**](https://github.com/anomalyco/opencode). It allows you to define multiple AI agents, chain them in iterative loops via `opencode run`, and manage their execution state until a specific termination condition is met.

OpenLoop solves the "premature convergence" problem of single-agent systems by enforcing strict state isolation, externalized state management, and deterministic loop control, turning OpenCode from an interactive coding assistant into a robust, autonomous workflow engine.

---

## 🧠 The Core Idea

OpenCode is incredible at interactive, single-turn coding tasks. However, when tasked with complex, multi-step workflows (e.g., "write a full test suite, audit it, and iterate until coverage is optimal"), a single OpenCode session will typically hallucinate completion after the first or second attempt. This happens due to context pollution and the inherent LLM bias to mark tasks as "done" prematurely.

OpenLoop solves this by decoupling the **orchestration** from the **execution**. 

Instead of one agent doing everything in a single, bloated context window, OpenLoop acts as a deterministic state machine. It spins up isolated, headless OpenCode instances via `opencode run`, injects the current workflow state into their prompts, parses their structured output to update the state, and decides whether to loop back or terminate.

### The Amala & Vera Pattern
The classic use case, included as a working example in this repository, is the **Author/Auditor** pattern:
1. **Amala (Author)** writes a comprehensive `pytest` suite.
2. **Vera (Auditor)** reviews the test output, checks coverage gaps, and audits the code.
3. If Vera finds gaps, she updates the state with specific feedback, and OpenLoop loops back to Amala.
4. If Vera is satisfied, she updates the state to `is_complete: true`, and OpenLoop terminates the loop.

Because each agent runs in a fresh OpenCode context with only the relevant state injected, they never suffer from context degradation.

---

## 🏗️ Architecture & How It Works

OpenLoop is built on three core pillars: **Agent Definitions**, **Externalized State**, and **Deterministic Execution**.

### 1. Agent Definitions (`agents/*.md`)
Agents are defined as simple Markdown files with YAML frontmatter. This keeps the system prompt human-readable while providing the orchestrator with necessary metadata.

 ```markdown
---
name: vera
role: auditor
can_complete: true
---

# Role
You are VERA, a strict QA Auditor.

# Current State Context
The orchestrator will inject the current state below.

# Instructions
1. Review the code and test coverage.
2. Update the state based on your findings.

# State Update
End your response with exactly one `<state_update>` block:

```xml
<state_update>
{"is_complete": false, "payload": {"feedback": "Missing edge case in auth.py line 42"}}
</state_update>
```
```

### 2. Externalized State Management
The orchestrator holds the "Single Source of Truth" in a Python `dataclass`. 
- **Injection:** Before an agent runs, the current state is serialized to JSON and injected into the `opencode run` prompt.
- **Extraction:** The engine parses `<state_update>` XML tags or JSON code blocks from the agent's stdout via `StateParser.extract_state_update()`.
- **Normalization:** Unknown keys are moved into `payload`, protected keys (`current_phase`, `iteration`, `meta`) are ignored, and `is_complete` is coerced to boolean with authorization checks.
- **Transition:** The orchestrator merges the update into the master state and evaluates the termination conditions.

### 3. Execution Flow
A workflow in OpenLoop consists of three distinct phases:

1. **Preparation Phase (Optional):** Runs once at the very beginning. Supports one or multiple agents executed in sequence. Useful for scaffolding, gathering context, or setting up the initial state.
2. **Loop Phase:** Iterates through a defined sequence of agents. 
   - Supports any number of agents in the loop sequence.
   - Checks `max_loops` to prevent infinite execution.
   - Evaluates the `end_state_condition` (e.g., `is_complete == True` or `payload.get('coverage', 0) >= 80`) after each agent.
3. **Finalization Phase (Optional):** Runs once at the end. Supports one or multiple agents executed in sequence. You can configure whether it runs only on successful completion (`finalize_on_abort: false`), or also if the loop was aborted due to hitting `max_loops` (`finalize_on_abort: true`).

### 4. The State Lifecycle (Step by Step)

Every agent invocation follows the same six-step cycle:

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
                    │  eval(end_state_condition) ──► loop  │
                    │                               or    │
                    │                              stop   │
                    └──────────────────────────────────────┘
```

1. **Serialize** — The engine serializes the current `WorkflowState` to a JSON string via `state.to_json()`.
2. **Inject** — The JSON is appended to the agent's system prompt under a `# Current State` heading (see `_build_prompt()` in `core/engine.py`).
3. **Execute** — The full prompt is written to `current_prompt.md` in the log directory, then `opencode run --file <path> --dir <workdir> "Follow the instructions..."` is launched as a headless LLM subprocess.
4. **Parse** — The engine calls `StateParser.extract_state_update(result.output)` on the agent's stdout, searching for `<state_update>` XML tags first, then JSON code blocks as fallback. If no valid state update is found, it makes up to 2 correction attempts via `opencode run -c` with a targeted error prompt, then invokes the missing-state handler (interactive prompt in TTY, abort otherwise).
5. **Normalize & Merge** — Before merging, `_normalize_state_update()` adjusts the parsed dict: unknown keys are moved into `payload`, `is_complete` is coerced to bool, and completion is blocked for unauthorized agents. The result is merged into `WorkflowState` via `merge()`. Only keys present in the update are changed; the `payload` dict is merged deeply.
6. **Evaluate** — The `end_state_condition` is evaluated. If true, the loop terminates. Otherwise, the next agent in the sequence starts at step 1 with the updated state.

The same cycle applies in all three phases. During the loop phase, the `iteration` counter is incremented before each full pass through the agent sequence.

---

## Documentation

Detailed reference documentation is available in the [`docs/`](./docs/) directory:

| Document | Description |
|---|---|
| [Agent Definitions](./docs/agent-definitions.md) | Agent file format, frontmatter fields, system prompt conventions, best practices |
| [Workflows](./docs/workflows.md) | Workflow JSON schema, slot configuration, `end_state_condition` syntax |
| [CLI](./docs/cli.md) | Complete CLI reference, flags, exit codes, examples |
| [Configuration](./docs/configuration.md) | `openloop.json` schema (JSONC with `//` and `/* */` comments), field descriptions, search order, override priority |
| [State](./docs/state.md) | `WorkflowState` API, merge behavior, agent output format |
| [GUI](./docs/gui.md) | Tkinter GUI layout, zones, toolbar actions |

---

## ✨ Key Features

- **Built for OpenCode:** Deeply integrated with the `opencode run` headless command.
- **Zero External Dependencies:** Built entirely on the Python 3.13+ Standard Library. No `pip install` required. Just clone and run.
- **Context Isolation:** Agents run in fresh, isolated OpenCode contexts, preventing the "context pollution" that plagues single-agent workflows.
- **Deterministic Loop Control:** Hard limits (`max_loops`) and strict state evaluation (including arbitrary Python expressions against the state payload) ensure the system never hangs in an infinite loop.
- **Flexible Agent Chaining:** Define any number of agents per phase (preparation, loop, finalization). Supports complex multi-agent pipelines.
- **Multiple Agents per Phase:** Preparation and Finalization phases support one or multiple agents executed in sequence.
- **Init Script & Workdir:** Run a setup script (e.g., activate conda environment) and set the working directory before each agent invocation – configurable globally, per workflow, or via CLI.
- **OpenCode Defaults:** Set global defaults for model, agent (`build`/`plan`), variant, and pure mode for every `opencode run` invocation – configurable in `openloop.json`, per workflow, or via `--opencode-defaults` CLI flag.
- **CLI & GUI Modes:** Use the visual Tkinter builder for interactive workflow design, or run headless via `--cli` for CI/CD integration.
- **Ready-to-Use Examples:** Ships with fully fleshed-out `amala.md` (author), `vera.md` (auditor), and `proteus.md` (analyst) agents, plus a working `test_generation` workflow.

---

## 📂 Project Structure

```text
openloop/
├── openloop.py               # Entry point (starts Tkinter GUI or CLI)
├── openloop.example.json      # Example configuration with documented fields
├── openloop.json              # Local configuration (gitignored; searched in CWD, falls back to openloop directory)
├── requirements.txt          # Empty (Zero dependencies)
├── core/
│   ├── config.py             # Configuration loader
│   ├── state.py              # WorkflowState dataclass & serialization
│   ├── parser.py             # State extraction from OpenCode stdout
│   ├── agent.py              # AgentDefinition loader (Markdown + YAML)
│   ├── runner.py             # Subprocess wrapper for `opencode run`
│   └── engine.py             # The execution loop and state machine
├── ui/
│   └── app.py                # Tkinter GUI for workflow configuration
├── agents/                   # Default directory for agent definitions
│   ├── amala.md              # Example: The Test Author
│   ├── vera.md               # Example: The QA Auditor
│   └── proteus.md            # Example: The Change Analyst
├── workflows/                # Default directory for saved workflow configs
│   └── test_generation.json  # Example: A ready-to-run Amala/Vera workflow
└── tests/
    └── integration.py        # Integration tests
```

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/openloop.git
cd openloop
```

### 2. (Optional) Create Configuration
Copy the example configuration and adjust it to your needs:

```bash
cp openloop.example.json openloop.json
```

### 3. Verify OpenCode
Ensure `opencode` is installed and available in your `$PATH`.
```bash
opencode --version
```

### 4. Run OpenLoop
Since there are no dependencies, you can start the GUI immediately:

```bash
python openloop.py
```

The GUI will show the available agents in the left panel. From there you can build a workflow, load the example, or configure a new one.

### 5. Execute the Example Workflow
1. Launch the GUI: `python openloop.py`
2. Click **Load Workflow** and select `workflows/test_generation.json`.
3. The workflow builder populates with Amala and Vera in the Loop zone.
4. Click **Start Execution** to run. The log panel shows live output as the engine iterates between authoring and auditing.
5. Click **Stop Execution** to abort between iterations.

You can also run headless (requires `opencode` in PATH):

```bash
python openloop.py --cli --workflow workflows/test_generation.json
```

Additional CLI options:

| Flag | Description |
|---|---|---|
| `--cli` | Run in headless CLI mode |
| `--workflow <path>` | Path to the workflow JSON file |
| `--workdir <path>` | Override the working directory |
| `--init-script <cmd>` | Override the init script/command |
| `--opencode-defaults <json>` | JSON string overriding opencode defaults for all agents (e.g., `'{"model":"gpt-4o","agent":"plan"}'`) |
| `--verbose`, `-v` | Stream agent stdout/stderr to terminal |
| `--config <path>` | Path to configuration file (default: `openloop.json` in CWD, falls back to `openloop.json` next to `openloop.py`) |

---

## 🛣️ Roadmap

- [x] Core Architecture & State Management
- [x] Agent Loader & Subprocess Runner
- [x] Execution Engine with Loop Control
- [x] Tkinter GUI for Workflow Building
- [x] Example Agents (Amala/Vera/Proteus) & Example Workflow
- [x] CLI mode with `--cli`, `--workdir`, `--init-script` flags
- [x] Advanced state evaluation (arbitrary Python expressions against the state payload)
- [x] Multiple agents in preparation and finalization phases
- [x] Workdir & init_script support (global, per-workflow, and CLI override)
- [x] GUI preview pane for agent content
- [x] Integration tests
- [x] Global OpenCode defaults (`opencode_defaults` in config/workflow/CLI) (Issue #23)
- [ ] Sub-workflow support in slots (Epic #18)
- [ ] Script hooks as third element category in slots (Epic #19)
- [x] Dedicated documentation in `docs/` (Issue #20)

---

## 🤝 Contributing

This project is developed iteratively using GitHub Issues. If you want to contribute, please check the open issues, pick one, and follow the standard fork-and-PR workflow. 

*Note: All code, comments, and documentation must be written in English.*
