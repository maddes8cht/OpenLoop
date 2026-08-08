"""OpenLoop Integration Tests.

Usage:
    python tests/integration.py

Zero external dependencies. Uses plain assert.
Tier 2 requires `opencode` in PATH (skipped if missing).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"

_total = 0
_passed = 0
_failed = 0
_skipped = 0


def test(name: str, func):
    global _total, _passed, _failed
    _total += 1
    try:
        func()
        _passed += 1
        print(f"    [{PASS}] {name}")
    except Exception as exc:
        _failed += 1
        print(f"    [{FAIL}] {name}: {exc}")


def skip(name: str, reason: str = "opencode not found in PATH"):
    global _total, _skipped
    _total += 1
    _skipped += 1
    print(f"    [{SKIP}] {name} — {reason}")


def heading(label: str):
    print(f"\n  {label}")


def summary():
    print(
        f"\n  Result: {_passed}/{_total - _skipped} passed, "
        f"{_skipped} skipped, {_failed} failed"
    )
    return _failed


# ---------------------------------------------------------------------------
# Tier 1 — Module Integration (mock runner, no opencode needed)
# ---------------------------------------------------------------------------

def _make_mock_runner(responses: list | None = None):
    """Return a mock runner whose .run() cycles through *responses*."""
    responses = responses or [
        {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'}
    ]
    idx = [0]

    def side_effect(prompt, timeout=None, **kw):
        r = responses[idx[0] % len(responses)]
        idx[0] += 1
        return type("R", (), dict(r, error="", exit_code=0 if r["success"] else 1))()

    runner = MagicMock()
    runner.run.side_effect = side_effect
    return runner


def _make_mock_agent_loader(agents: dict | None = None):
    """Return a mock AgentLoader."""
    agents = agents or {"test": "You are a test agent."}
    loader = MagicMock()

    def get_agent(name):
        prompt = agents.get(name, "You are a default agent.")
        return type("A", (), {"name": name, "can_complete": True, "role": "auditor", "system_prompt": prompt})()

    loader.get_agent.side_effect = get_agent
    return loader


def test_full_pipeline():
    """Engine loads agents, runs loop, merges state, terminates."""
    from core.engine import ExecutionEngine, WorkflowConfig

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": True, "output": '<state_update>{"is_complete": false, "payload": {"step": 1}}</state_update>'},
        {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a1": "Agent 1", "a2": "Agent 2"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a1", "a2"],
        "max_loops": 5,
        "end_state_condition": "is_complete == True",
    })
    assert state.is_complete is True
    assert state.termination_reason == "completed"
    assert state.payload.get("step") == 1


def test_loop_max_iterations():
    """Loop exhausts max_loops when agents never set is_complete."""
    from core.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a"],
        "max_loops": 3,
        "end_state_condition": "is_complete == True",
    })
    assert state.iteration == 3
    assert state.termination_reason == "max_loops_reached"


def test_state_passed_between_agents():
    """Each agent's prompt includes the current state from the previous run."""
    from core.engine import ExecutionEngine

    responses = [
        {"success": True, "output": '<state_update>{"payload": {"seen": 1}}</state_update>'},
        {"success": True, "output": '<state_update>{"is_complete": true, "payload": {"seen": 2}}</state_update>'},
    ]
    prompts = []
    _next = [0]
    engine = ExecutionEngine()
    engine.runner = MagicMock()

    def _side_effect(p, **kw):
        idx = _next[0]
        _next[0] += 1
        prompts.append(p)
        return type("R", (), dict(responses[idx], error="", exit_code=0))()

    engine.runner.run.side_effect = _side_effect
    engine.agent_loader = _make_mock_agent_loader({"a": "First", "b": "Second"})

    engine.execute_workflow_data({
        "loop_agents": ["a", "b"],
        "max_loops": 1,
        "end_state_condition": "is_complete == True",
    })
    assert len(prompts) == 2
    assert '"is_complete": false' in prompts[0]  # initial state
    assert '"seen": 1' in prompts[1]  # state from agent a merged before b runs


def test_agent_failure():
    """agent_error is set when runner returns failure."""
    from core.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": False, "output": ""},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a"],
        "max_loops": 1,
        "end_state_condition": "is_complete == True",
    })
    assert state.termination_reason == "agent_error:a"


def test_malformed_agent_output():
    """No crash when agent output lacks state_update."""
    from core.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": True, "output": "Hello world, no XML here"},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a"],
        "max_loops": 1,
        "end_state_condition": "is_complete == True",
    })
    assert state.termination_reason == "missing_state:a"
    assert state.is_complete is False
    assert state.iteration == 1


def test_end_condition_payload_expression():
    """end_state_condition evaluates payload expressions."""
    from core.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": True, "output": '<state_update>{"payload": {"coverage": 50}}</state_update>'},
        {"success": True, "output": '<state_update>{"payload": {"coverage": 90}}</state_update>'},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a"],
        "max_loops": 5,
        "end_state_condition": "payload.get('coverage', 0) >= 80",
    })
    assert state.iteration == 2
    assert state.termination_reason == "completed"
    assert state.payload["coverage"] == 90


def test_stop_event():
    """Stop event terminates loop between iterations."""
    from core.engine import ExecutionEngine

    stop_ev = threading.Event()
    engine = ExecutionEngine(stop_event=stop_ev)

    def slow_run(prompt, timeout=None, **kw):
        time.sleep(0.3)
        return type("R", (), {
            "success": True,
            "output": '<state_update>{"is_complete": false}</state_update>',
            "error": "", "exit_code": 0,
        })()
    engine.runner = MagicMock()
    engine.runner.run.side_effect = slow_run
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    results = []
    def run():
        state = engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 10,
            "end_state_condition": "is_complete == True",
        })
        results.append(state.termination_reason)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.5)
    stop_ev.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert results[0] == "stopped"


def test_preparation_agent():
    """Preparation agent runs once before the loop."""
    from core.engine import ExecutionEngine

    responses = [
        {"success": True, "output": '<state_update>{"payload": {"prepped": true}}</state_update>'},
        {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
    ]
    calls = []
    _next = [0]
    engine = ExecutionEngine()
    engine.runner = MagicMock()

    def _side_effect(p, **kw):
        idx = _next[0]
        _next[0] += 1
        calls.append(p)
        return type("R", (), dict(responses[idx], error="", exit_code=0))()

    engine.runner.run.side_effect = _side_effect
    engine.agent_loader = _make_mock_agent_loader({
        "prepper": "Prep agent",
        "worker": "Loop agent",
    })

    state = engine.execute_workflow_data({
        "preparation_agent": "prepper",
        "loop_agents": ["worker"],
        "max_loops": 1,
        "end_state_condition": "is_complete == True",
    })
    assert state.payload.get("prepped") is True
    assert len(calls) == 2  # prep + one loop


def test_finalization_agent():
    """Finalization runs on completion."""
    from core.engine import ExecutionEngine

    responses = [
        {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
        {"success": True, "output": '<state_update>{"payload": {"finalized": true}}</state_update>'},
    ]
    calls = []
    _next = [0]
    engine = ExecutionEngine()
    engine.runner = MagicMock()

    def _side_effect(p, **kw):
        idx = _next[0]
        _next[0] += 1
        calls.append(p)
        return type("R", (), dict(responses[idx], error="", exit_code=0))()

    engine.runner.run.side_effect = _side_effect
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent", "fin": "Finisher"})

    state = engine.execute_workflow_data({
        "loop_agents": ["a"],
        "finalization_agent": "fin",
        "max_loops": 1,
        "end_state_condition": "is_complete == True",
        "finalize_on_abort": False,
    })
    assert state.payload.get("finalized") is True
    assert len(calls) == 2  # loop agent + finalization


def test_execute_workflow_from_file():
    """Engine.execute_workflow loads from file path."""
    from core.engine import ExecutionEngine

    engine = ExecutionEngine()
    engine.runner = _make_mock_runner([
        {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
    ])
    engine.agent_loader = _make_mock_agent_loader({"a": "Agent"})

    with tempfile.TemporaryDirectory() as tmp:
        wf = Path(tmp) / "wf.json"
        wf.write_text(json.dumps({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
        }))
        state = engine.execute_workflow(str(wf))
    assert state.is_complete is True


def _make_resume_agent_loader(agents: dict | None = None):
    """Mock loader whose agents are completion-authorized (role auditor)."""
    agents = agents or {"a": "Agent"}
    loader = MagicMock()

    def get_agent(name):
        prompt = agents.get(name, "You are a default agent.")
        return type("A", (), {"name": name, "role": "auditor", "can_complete": True, "system_prompt": prompt})()

    loader.get_agent.side_effect = get_agent
    return loader


def test_resume_after_agent_error():
    """Checkpoint is written on agent_error and resume finishes the run."""
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine(log_dir=tmp)
        engine.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"payload": {"step": 1}}</state_update>'},
            {"success": False, "output": ""},
        ])
        engine.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        state = engine.execute_workflow_data({
            "loop_agents": ["a", "b"],
            "max_loops": 5,
            "end_state_condition": "is_complete == True",
        })
        assert state.termination_reason == "agent_error:b"
        checkpoint = engine._checkpoint_path
        assert checkpoint is not None and checkpoint.exists()

        engine2 = ExecutionEngine(log_dir=tmp)
        engine2.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": true, "payload": {"step": 2}}</state_update>'},
        ])
        engine2.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        state2 = engine2.execute_resume(checkpoint)
        assert state2.termination_reason == "completed"
        assert state2.iteration == 1
        assert state2.payload.get("step") == 2
        assert not checkpoint.exists()


def test_resume_mid_iteration_does_not_reincrement():
    """Resuming mid-iteration continues from the failed agent without
    incrementing the iteration number."""
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine(log_dir=tmp)
        engine.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
            {"success": False, "output": ""},
        ])
        engine.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        state = engine.execute_workflow_data({
            "loop_agents": ["a", "b"],
            "max_loops": 5,
            "end_state_condition": "is_complete == True",
        })
        assert state.termination_reason == "agent_error:b"
        checkpoint = engine._checkpoint_path

        engine2 = ExecutionEngine(log_dir=tmp)
        engine2.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
        ])
        engine2.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        state2 = engine2.execute_resume(checkpoint)
        assert state2.iteration == 1
        assert state2.termination_reason == "completed"


def test_resume_max_loops_override():
    """Resuming max_loops_reached continues when given a higher limit."""
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine(log_dir=tmp)
        engine.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
        ])
        engine.agent_loader = _make_resume_agent_loader({"a": "A"})
        state = engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 2,
            "end_state_condition": "is_complete == True",
        })
        assert state.termination_reason == "max_loops_reached"
        checkpoint = engine._checkpoint_path

        engine2 = ExecutionEngine(log_dir=tmp)
        engine2.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
            {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
        ])
        engine2.agent_loader = _make_resume_agent_loader({"a": "A"})
        state2 = engine2.execute_resume(checkpoint, max_loops_override=5)
        assert state2.termination_reason == "completed"
        assert state2.iteration == 4


def test_resume_timeout_reason():
    """A timed-out agent produces a timeout:<name>:<sec> reason."""
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine(log_dir=tmp, timeout=60)
        engine.runner = MagicMock()
        engine.runner.run.side_effect = lambda *a, **kw: type("R", (), {
            "success": False, "output": "", "error": "timed out",
            "exit_code": -1, "timed_out": True,
        })()
        engine.agent_loader = _make_resume_agent_loader({"a": "A"})
        state = engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 5,
            "end_state_condition": "is_complete == True",
        })
        assert state.termination_reason == "timeout:a:60"


def test_resume_log_continuation():
    """Resumed run appends a second <openloop_log> root with a marker."""
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine(log_dir=tmp)
        engine.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
            {"success": False, "output": ""},
        ])
        engine.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        engine.execute_workflow_data({
            "loop_agents": ["a", "b"],
            "max_loops": 5,
            "end_state_condition": "is_complete == True",
        })
        log_path = engine._log_path
        checkpoint = engine._checkpoint_path

        engine2 = ExecutionEngine(log_dir=tmp)
        engine2.runner = _make_mock_runner([
            {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
        ])
        engine2.agent_loader = _make_resume_agent_loader({"a": "A", "b": "B"})
        engine2.execute_resume(checkpoint)

        text = log_path.read_text(encoding="utf-8")
        assert text.count("<openloop_log>") == 2
        assert "# OPENLOOP RESUMED" in text


def test_resume_blocked_by_reason_filter():
    """resume_reasons config filter blocks resuming disallowed reasons."""
    from core.config import Config
    from core.engine import ExecutionEngine

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(log_dir=tmp, resume_reasons=["stopped"])
        engine = ExecutionEngine(config=cfg, log_dir=tmp)
        engine.runner = _make_mock_runner([{"success": False, "output": ""}])
        engine.agent_loader = _make_resume_agent_loader({"a": "A"})
        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 5,
            "end_state_condition": "is_complete == True",
        })
        try:
            engine.execute_resume(engine._checkpoint_path)
            raise AssertionError("Expected resume to be blocked")
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Tier 2 — System Integration (requires `opencode` in PATH)
# ---------------------------------------------------------------------------

def _opencode_available() -> bool:
    try:
        subprocess.run(
            ["opencode", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def test_opencode_runner_basic():
    """OpenCodeRunner runs a trivial prompt and returns output."""
    if not _opencode_available():
        skip("OpenCodeRunner basic prompt")
        return

    from core.runner import OpenCodeRunner
    runner = OpenCodeRunner(timeout=30)
    result = runner.run("Say 'hello' and nothing else.")
    assert result.success, f"opencode run failed: {result.error}"
    assert "hello" in result.output.lower()


def test_opencode_pipeline_end_to_end():
    """Full workflow prompt builds and executes without runner error."""
    if not _opencode_available():
        skip("Full workflow end-to-end")
        return

    from core.runner import OpenCodeRunner

    runner = OpenCodeRunner(timeout=120)

    # Build a realistic prompt the same way the engine does
    prompt = (
        "You are a test author. Write pytest tests for the following "
        "Python module:\n\n"
        "```python\ndef add(a, b): return a + b\n"
        "def divide(a, b):\n    if b == 0: raise ValueError\n    return a / b\n"
        "def is_palindrome(s): return s == s[::-1]\n"
        "```\n\n"
        "Output your tests in a ```python code block and include a "
        "<state_update> XML tag with is_complete set to true."
    )
    result = runner.run(prompt)
    assert result.success, f"opencode run failed: {result.error[:200]}"
    assert "def test_" in result.output or "<state_update>" in result.output, \
        "Expected test code or state_update in output"


# ---------------------------------------------------------------------------
# Tier 3 — Artifact Verification
# ---------------------------------------------------------------------------

def test_amala_agent_parses():
    from core.agent import AgentLoader
    loader = AgentLoader(str(ROOT / "agents"))
    amala = loader.get_agent("amala")
    assert amala.name == "amala"
    assert amala.role == "author"
    assert "pytest" in amala.system_prompt


def test_vera_agent_parses():
    from core.agent import AgentLoader
    loader = AgentLoader(str(ROOT / "agents"))
    vera = loader.get_agent("vera")
    assert vera.name == "vera"
    assert vera.role == "auditor"
    assert "VERA" in vera.system_prompt


def test_example_workflow_loads():
    from core.engine import WorkflowConfig
    wf = WorkflowConfig.load(str(ROOT / "workflows" / "test_generation.json"))
    assert wf.loop_agents == ["amala", "vera"]
    assert wf.max_loops == 10
    assert wf.preparation_agents == []


def test_all_core_modules_import():
    import core.config
    import core.state
    import core.parser
    import core.agent
    import core.runner
    import core.engine
    import core.checkpoint
    # Smoke-test public API
    assert core.config.Config is not None
    assert core.state.WorkflowState is not None
    assert core.parser.StateParser is not None
    assert core.agent.AgentLoader is not None
    assert core.runner.OpenCodeRunner is not None
    assert core.engine.ExecutionEngine is not None
    assert core.checkpoint.CheckpointData is not None


def test_entry_point_parses_args():
    from openloop import parse_args

    args = parse_args([])
    assert args.cli is False
    assert args.workflow is None
    assert args.config is None

    args = parse_args(["--cli", "--workflow", "test.json"])
    assert args.cli is True
    assert args.workflow == "test.json"


def test_markdown_renderer():
    from tkinter import Tk, Text, font
    root = Tk()
    try:
        w = Text(root)
        from core.markdown_renderer import render
        render(w, """# Heading

**bold** and `code`

---
front: matter
---

```
code block
line two
```

- bullet

> quote""")
        text = w.get("1.0", "end - 1c")
        assert "Heading" in text
        assert "bold" in text
        assert "code" in text
        assert "front" in text
        assert "matter" in text
        assert "code block" in text
        assert "line two" in text
        assert "bullet" in text
        assert "quote" in text

        default_family = font.Font(font=w.cget("font")).actual()["family"]
        mono_family = font.nametofont("TkFixedFont").actual()["family"]
        assert default_family != mono_family, \
            f"Default ({default_family}) should differ from mono ({mono_family})"
        tag_family = font.Font(font=w.tag_cget("codeblock", "font")).actual()["family"]
        assert tag_family == mono_family, \
            f"Codeblock tag font ({tag_family}) should be resolved mono ({mono_family})"
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    failed = 0

    print("=" * 60)
    print("  OpenLoop Integration Tests")
    print("=" * 60)

    heading("Tier 1 — Module Integration")
    for fn in [
        test_full_pipeline,
        test_loop_max_iterations,
        test_state_passed_between_agents,
        test_agent_failure,
        test_malformed_agent_output,
        test_end_condition_payload_expression,
        test_stop_event,
        test_preparation_agent,
        test_finalization_agent,
        test_execute_workflow_from_file,
        test_resume_after_agent_error,
        test_resume_mid_iteration_does_not_reincrement,
        test_resume_max_loops_override,
        test_resume_timeout_reason,
        test_resume_log_continuation,
        test_resume_blocked_by_reason_filter,
    ]:
        test(fn.__name__.replace("_", " ").replace("test ", ""), fn)
    failed += summary()

    heading("Tier 2 — System Integration (opencode)")
    if _opencode_available():
        test("opencode runner basic prompt", test_opencode_runner_basic)
        test("pipeline end-to-end", test_opencode_pipeline_end_to_end)
    else:
        skip("opencode runner basic prompt")
        skip("pipeline end-to-end")
    failed += summary()

    heading("Tier 3 — Artifact Verification")
    for fn in [
        test_amala_agent_parses,
        test_vera_agent_parses,
        test_example_workflow_loads,
        test_all_core_modules_import,
        test_entry_point_parses_args,
        test_markdown_renderer,
    ]:
        test(fn.__name__.replace("_", " ").replace("test ", ""), fn)
    failed += summary()

    print()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
