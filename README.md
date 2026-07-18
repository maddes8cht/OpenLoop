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
name: vera_auditor
role: auditor
expected_output_format: json_block
---

# Role
You are VERA, a strict QA Auditor.

# Current State Context
The orchestrator will inject the current state below.

# Instructions
1. Review the code and test coverage.
2. Update the state based on your findings.
3. You MUST output your state update inside a `<state_update>` XML tag at the very end of your response.

Example:
<state_update>
{
  "next_phase": "amala_fix",
  "feedback": "Missing edge case in auth.py line 42",
  "is_complete": false
}
</state_update>
```

### 2. Externalized State Management
The orchestrator holds the "Single Source of Truth" in a Python `dataclass`. 
- **Injection:** Before an agent runs, the current state is serialized to JSON and injected into the `opencode run` prompt.
- **Extraction:** After OpenCode finishes, OpenLoop parses the stdout for a structured state update (e.g., inside `<state_update>` tags).
- **Transition:** The orchestrator merges the update into the master state and evaluates the termination conditions.

### 3. Execution Flow
A workflow in OpenLoop consists of three distinct phases:

1. **Preparation Phase (Optional):** Runs exactly once at the very beginning. Useful for scaffolding, gathering context, or setting up the initial state.
2. **Loop Phase:** Iterates through a defined sequence of agents. 
   - Checks `max_loops` to prevent infinite execution.
   - Evaluates the `end_state_condition` (e.g., `state.is_complete == True`) after each agent.
3. **Finalization Phase (Optional):** Runs exactly once at the end. You can configure whether it runs only on successful completion, or also if the loop was aborted due to hitting `max_loops`.

---

## ⚙️ Configuration

OpenLoop uses a simple `config.json` in the root directory to define global settings. This allows you to customize where OpenLoop looks for agents and workflows.

```json
{
  "agents_dir": "./agents",
  "workflows_dir": "./workflows",
  "opencode_binary": "opencode",
  "default_max_loops": 10
}
```
*If `config.json` is missing, OpenLoop falls back to the default values shown above.*

---

## ✨ Key Features

- **Built for OpenCode:** Deeply integrated with the `opencode run` headless command.
- **Zero External Dependencies:** Built entirely on the Python 3.13+ Standard Library. No `pip install` required. Just clone and run.
- **Context Isolation:** Agents run in fresh, isolated OpenCode contexts, preventing the "context pollution" that plagues single-agent workflows.
- **Deterministic Loop Control:** Hard limits (`max_loops`) and strict state evaluation ensure the system never hangs in an infinite loop.
- **Flexible Agent Chaining:** Define any number of agents in the loop. It can be a 2-agent Author/Auditor, or a 5-agent pipeline.
- **Visual Workflow Builder:** Includes a built-in Tkinter GUI to easily select agents, define loop sequences, and configure termination conditions.
- **Ready-to-Use Examples:** Ships with fully fleshed-out `amala.md` and `vera.md` agents, plus a working `test_generation` workflow.

---

## 📂 Project Structure

```text
openloop/
├── main.py                   # Entry point (starts Tkinter GUI or CLI)
├── config.json               # Global configuration (paths, binary name)
├── requirements.txt          # Empty (Zero dependencies)
├── core/
│   ├── engine.py             # The execution loop and state machine
│   ├── state.py              # WorkflowState dataclass & serialization
│   ├── agent.py              # AgentDefinition loader (Markdown + YAML)
│   ├── parser.py             # State extraction from OpenCode stdout
│   └── runner.py             # Subprocess wrapper for `opencode run`
├── ui/
│   └── app.py                # Tkinter GUI for workflow configuration
├── agents/                   # Default directory for agent definitions
│   ├── amala.md              # Example: The Test Author
│   └── vera.md               # Example: The QA Auditor
└── workflows/                # Default directory for saved workflow configs
    └── test_generation.json  # Example: A ready-to-run Amala/Vera workflow
```

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/openloop.git
cd openloop
```

### 2. Verify OpenCode
Ensure `opencode` is installed and available in your `$PATH`.
```bash
opencode --version
```

### 3. Run OpenLoop
Since there are no dependencies, you can start the GUI immediately:

```bash
python main.py
```

### 4. Execute the Example Workflow
1. In the GUI, click **Load Workflow** and select `workflows/test_generation.json`.
2. Review the configuration (Prep: None, Loop: Amala -> Vera, Final: None).
3. Set your target directory/module in the execution parameters.
4. Click **START EXECUTION** and watch the logs as Amala and Vera iterate.

---

## 🛣️ Roadmap

- [x] Core Architecture & State Management
- [x] Agent Loader & Subprocess Runner
- [x] Execution Engine with Loop Control
- [x] Tkinter GUI for Workflow Building
- [x] Example Agents (Amala/Vera) & Example Workflow
- [ ] CLI-only mode for CI/CD integration
- [ ] Advanced state evaluation (e.g., evaluating Python expressions against the state payload)
- [ ] Live context-injection for external tools (e.g., injecting `pytest` output directly into the state payload before Vera runs)

---

## 🤝 Contributing

This project is developed iteratively using GitHub Issues. If you want to contribute, please check the open issues, pick one, and follow the standard fork-and-PR workflow. 

*Note: All code, comments, and documentation must be written in English.*
```

### Nächste Schritte für das Repo

Das README steht. Wenn du das Repo angelegt hast, schlage ich vor, dass wir als Nächstes die **GitHub Issues** formulieren. 

Soll ich dir die Texte für die ersten 3-4 Issues (Core State, Agent Loader, Runner/Engine) direkt hier ausformulieren, damit du sie 1:1 in GitHub kopieren kannst? Das gibt uns den perfekten Fahrplan, wenn du das Repo dann in OpenCode lädst.
