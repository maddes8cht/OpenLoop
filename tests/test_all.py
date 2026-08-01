"""Comprehensive pytest suite for every function in the OpenLoop codebase."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import tkinter as tk


# ===========================================================================
# core.state — WorkflowState
# ===========================================================================


class TestWorkflowState:
    def test_default_initialization(self):
        from core.state import WorkflowState

        s = WorkflowState()
        assert s.current_phase == "preparation"
        assert s.iteration == 0
        assert s.is_complete is False
        assert s.termination_reason == ""
        assert s.payload == {}

    def test_custom_initialization(self):
        from core.state import WorkflowState

        s = WorkflowState(
            current_phase="loop",
            iteration=3,
            is_complete=True,
            termination_reason="completed",
            payload={"key": "val"},
        )
        assert s.current_phase == "loop"
        assert s.iteration == 3
        assert s.is_complete is True
        assert s.termination_reason == "completed"
        assert s.payload == {"key": "val"}

    def test_to_json(self):
        from core.state import WorkflowState

        s = WorkflowState(iteration=1, is_complete=True, termination_reason="completed")
        data = json.loads(s.to_json())
        assert data["iteration"] == 1
        assert data["is_complete"] is True
        assert data["termination_reason"] == "completed"

    def test_from_json(self):
        from core.state import WorkflowState

        json_str = '{"current_phase": "loop", "iteration": 5, "is_complete": true, "termination_reason": "completed", "payload": {"x": 1}}'
        s = WorkflowState.from_json(json_str)
        assert s.current_phase == "loop"
        assert s.iteration == 5
        assert s.is_complete is True
        assert s.termination_reason == "completed"
        assert s.payload == {"x": 1}

    def test_from_json_minimal(self):
        from core.state import WorkflowState

        s = WorkflowState.from_json('{"is_complete": true}')
        assert s.is_complete is True
        assert s.current_phase == "preparation"

    def test_from_json_invalid_root(self):
        from core.state import WorkflowState

        with pytest.raises(ValueError, match="JSON root must be a dict"):
            WorkflowState.from_json('"not_a_dict"')

    def test_from_json_list_root(self):
        from core.state import WorkflowState

        with pytest.raises(ValueError, match="JSON root must be a dict"):
            WorkflowState.from_json("[1, 2, 3]")

    def test_merge_updates_fields(self):
        from core.state import WorkflowState

        s = WorkflowState()
        s.merge(
            {
                "current_phase": "loop",
                "iteration": 1,
                "is_complete": True,
                "termination_reason": "completed",
                "payload": {"step": 1},
            }
        )
        assert s.current_phase == "loop"
        assert s.iteration == 1
        assert s.is_complete is True
        assert s.termination_reason == "completed"
        assert s.payload == {"step": 1}

    def test_merge_payload_accumulates(self):
        from core.state import WorkflowState

        s = WorkflowState(payload={"a": 1})
        s.merge({"payload": {"b": 2}})
        assert s.payload == {"a": 1, "b": 2}

    def test_merge_payload_overwrite(self):
        from core.state import WorkflowState

        s = WorkflowState(payload={"key": 1})
        s.merge({"payload": {"key": 2}})
        assert s.payload["key"] == 2

    def test_merge_partial_update(self):
        from core.state import WorkflowState

        s = WorkflowState(iteration=0)
        s.merge({"iteration": 42})
        assert s.iteration == 42
        assert s.is_complete is False

    def test_merge_type_coercion(self):
        from core.state import WorkflowState

        s = WorkflowState()
        s.merge({"is_complete": 1, "iteration": "3", "current_phase": 123})
        assert s.is_complete is True
        assert s.iteration == 3
        assert s.current_phase == "123"

    def test_to_json_roundtrip(self):
        from core.state import WorkflowState

        original = WorkflowState(
            current_phase="finalization",
            iteration=7,
            is_complete=True,
            termination_reason="completed",
            payload={"result": "ok"},
        )
        restored = WorkflowState.from_json(original.to_json())
        assert restored == original

    def test_payload_default_factory_isolation(self):
        from core.state import WorkflowState

        s1 = WorkflowState()
        s2 = WorkflowState()
        s1.payload["x"] = 1
        assert s2.payload == {}


# ===========================================================================
# core.config — Config
# ===========================================================================


class TestConfig:
    def test_default_values(self):
        from core.config import Config

        c = Config()
        assert c.agents_dir == "./agents"
        assert c.workflows_dir == "./workflows"
        assert c.opencode_binary == "opencode"
        assert c.default_max_loops == 10

    def test_load_from_file(self, tmp_path):
        from core.config import Config

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(
            json.dumps(
                {
                    "agents_dir": "./my_agents",
                    "workflows_dir": "./my_workflows",
                    "opencode_binary": "my-opencode",
                    "default_max_loops": 5,
                }
            )
        )
        # Ensure dirs exist so _validate passes
        (tmp_path / "my_agents").mkdir()
        (tmp_path / "my_workflows").mkdir()

        old_cwd = Path.cwd()
        try:
            import os

            os.chdir(tmp_path)
            c = Config.load(str(cfg_file))
            assert c.agents_dir == "./my_agents"
            assert c.workflows_dir == "./my_workflows"
            assert c.opencode_binary == "my-opencode"
            assert c.default_max_loops == 5
        finally:
            os.chdir(old_cwd)

    def test_load_file_not_found_returns_default(self, tmp_path):
        from core.config import Config

        c = Config._from_file(str(tmp_path / "nonexistent.json"))
        assert c.agents_dir == "./agents"

    def test_load_invalid_json(self, tmp_path):
        from core.config import Config

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            Config._from_file(str(cfg_file))

    def test_load_non_dict_json(self, tmp_path):
        from core.config import Config

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text('"just a string"')
        with pytest.raises(ValueError, match="must contain a JSON object"):
            Config._from_file(str(cfg_file))

    def test_validate_creates_dirs(self, tmp_path):
        from core.config import Config

        c = Config(agents_dir=str(tmp_path / "new_agents"), workflows_dir=str(tmp_path / "new_workflows"))
        c._validate()
        assert (tmp_path / "new_agents").is_dir()
        assert (tmp_path / "new_workflows").is_dir()

    def test_validate_rejects_small_max_loops(self, tmp_path):
        from core.config import Config

        c = Config(agents_dir=str(tmp_path / "a"), workflows_dir=str(tmp_path / "b"), default_max_loops=0)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        with pytest.raises(ValueError, match="default_max_loops must be >= 1"):
            c._validate()

    def test_get_config_before_load_raises(self):
        import core.config
        core.config._config = None
        from core.config import get_config

        with pytest.raises(RuntimeError, match="not loaded"):
            get_config()

    def test_get_config_after_load(self, tmp_path):
        from core.config import Config, get_config

        agents = tmp_path / "agents"
        workflows = tmp_path / "workflows"
        agents.mkdir()
        workflows.mkdir()
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"agents_dir": str(agents), "workflows_dir": str(workflows)}))

        old_cwd = Path.cwd()
        try:
            import os

            os.chdir(tmp_path)
            loaded = Config.load(str(cfg_file))
            assert get_config() is loaded
        finally:
            os.chdir(old_cwd)


# ===========================================================================
# core.parser — StateParser
# ===========================================================================


class TestStateParser:
    def test_extract_xml_state_update(self):
        from core.parser import StateParser

        text = '<state_update>{"is_complete": true}</state_update>'
        result = StateParser.extract_state_update(text)
        assert result == {"is_complete": True}

    def test_extract_xml_with_whitespace(self):
        from core.parser import StateParser

        text = '<state_update>\n  {"is_complete": true}\n</state_update>'
        result = StateParser.extract_state_update(text)
        assert result == {"is_complete": True}

    def test_extract_json_block(self):
        from core.parser import StateParser

        text = "Some text\n```json\n{\"is_complete\": true}\n```\nmore text"
        result = StateParser.extract_state_update(text)
        assert result == {"is_complete": True}

    def test_extract_json_block_without_lang(self):
        from core.parser import StateParser

        text = "```\n{\"is_complete\": true}\n```"
        result = StateParser.extract_state_update(text)
        assert result == {"is_complete": True}

    def test_xml_preferred_over_json_block(self):
        from core.parser import StateParser

        text = (
            '<state_update>{"is_complete": true}</state_update>\n'
            "```json\n{\"is_complete\": false}\n```"
        )
        result = StateParser.extract_state_update(text)
        assert result == {"is_complete": True}

    def test_no_match_returns_none(self):
        from core.parser import StateParser

        result = StateParser.extract_state_update("Hello world, nothing special here.")
        assert result is None

    def test_empty_string_returns_none(self):
        from core.parser import StateParser

        result = StateParser.extract_state_update("")
        assert result is None

    def test_none_input_returns_none(self):
        from core.parser import StateParser

        result = StateParser.extract_state_update(None)
        assert result is None

    def test_json_block_multiline(self):
        from core.parser import StateParser

        text = "```json\n{\n  \"a\": 1,\n  \"b\": 2\n}\n```"
        result = StateParser.extract_state_update(text)
        assert result == {"a": 1, "b": 2}

    def test_non_dict_json_returns_none(self):
        from core.parser import StateParser

        text = '```json\n"just a string"\n```'
        result = StateParser.extract_state_update(text)
        assert result is None

    def test_invalid_json_in_xml_returns_none(self):
        from core.parser import StateParser

        text = "<state_update>{invalid}</state_update>"
        result = StateParser.extract_state_update(text)
        assert result is None

    def test_case_insensitive_tag(self):
        from core.parser import StateParser

        text = '<STATE_UPDATE>{"ok": true}</STATE_UPDATE>'
        result = StateParser.extract_state_update(text)
        assert result == {"ok": True}

    def test_markdown_fallback_extracts_state(self):
        from core.parser import StateParser

        text = """<state_update>
- **is_complete**: false
- **summary**: Wrote 55 new tests
- **tests_written**: 55
- **additional_missing_tests**:
  - `calculate_tolerance` -> not found
  - `safe_read_parquet` -> not found
- **branch**: `main`
- **commit**: 8095e30
</state_update>"""
        result = StateParser.extract_state_update(text)
        assert result is not None
        assert result["is_complete"] is False
        assert result["summary"] == "Wrote 55 new tests"
        assert result["tests_written"] == 55
        assert result["additional_missing_tests"] == [
            "calculate_tolerance", "safe_read_parquet"
        ]
        assert result["branch"] == "main"
        assert result["commit"] == "8095e30"


# ===========================================================================
# core.runner — OpenCodeOptions
# ===========================================================================


class TestOpenCodeOptions:
    def test_default_initialization(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions()
        assert opts.model is None
        assert opts.agent is None
        assert opts.variant is None
        assert opts.pure is False
        assert opts.log_level is None
        assert opts.extra_args == []

    def test_custom_initialization(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions(model="gpt-4", agent="plan", variant="full", pure=True, log_level="debug", extra_args=["--verbose"])
        assert opts.model == "gpt-4"
        assert opts.agent == "plan"
        assert opts.variant == "full"
        assert opts.pure is True
        assert opts.log_level == "debug"
        assert opts.extra_args == ["--verbose"]

    def test_to_cli_args_all_fields(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions(model="gpt-4", agent="plan", variant="full", pure=True, log_level="debug", extra_args=["--verbose"])
        args = opts.to_cli_args()
        assert args == ["-m", "gpt-4", "--agent", "plan", "--variant", "full", "--pure", "--log-level", "debug", "--verbose"]

    def test_to_cli_args_empty(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions()
        assert opts.to_cli_args() == []

    def test_to_cli_args_partial(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions(model="claude")
        assert opts.to_cli_args() == ["-m", "claude"]

    def test_merge_full(self):
        from core.runner import OpenCodeOptions

        base = OpenCodeOptions(model="gpt-4", agent="build", variant="fast", pure=False, log_level="info")
        override = OpenCodeOptions(model="claude", agent="plan", variant="full", pure=True, log_level="debug")
        merged = base.merge(override)
        assert merged.model == "claude"
        assert merged.agent == "plan"
        assert merged.variant == "full"
        assert merged.pure is True
        assert merged.log_level == "debug"

    def test_merge_partial(self):
        from core.runner import OpenCodeOptions

        base = OpenCodeOptions(model="gpt-4", agent="build")
        override = OpenCodeOptions(model="claude")
        merged = base.merge(override)
        assert merged.model == "claude"
        assert merged.agent == "build"
        assert merged.pure is False

    def test_merge_empty(self):
        from core.runner import OpenCodeOptions

        base = OpenCodeOptions(model="gpt-4", agent="plan", pure=True)
        override = OpenCodeOptions()
        merged = base.merge(override)
        assert merged.model == "gpt-4"
        assert merged.agent == "plan"
        assert merged.pure is True

    def test_merge_pure_flag(self):
        from core.runner import OpenCodeOptions

        base = OpenCodeOptions(pure=False)
        override = OpenCodeOptions(pure=True)
        merged = base.merge(override)
        assert merged.pure is True

        base2 = OpenCodeOptions(pure=True)
        override2 = OpenCodeOptions(pure=False)
        merged2 = base2.merge(override2)
        assert merged2.pure is True

    def test_merge_extra_args_concatenated(self):
        from core.runner import OpenCodeOptions

        base = OpenCodeOptions(extra_args=["-v"])
        override = OpenCodeOptions(extra_args=["--dry-run"])
        merged = base.merge(override)
        assert merged.extra_args == ["-v", "--dry-run"]

    def test_to_dict_all_fields(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions(model="gpt-4", agent="plan", variant="full", pure=True, log_level="debug", extra_args=["--verbose"])
        d = opts.to_dict()
        assert d == {"model": "gpt-4", "agent": "plan", "variant": "full", "pure": True, "log_level": "debug", "extra_args": ["--verbose"]}

    def test_to_dict_empty(self):
        from core.runner import OpenCodeOptions

        d = OpenCodeOptions().to_dict()
        assert d == {}

    def test_to_dict_partial(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions(model="claude", pure=True)
        d = opts.to_dict()
        assert d == {"model": "claude", "pure": True}

    def test_from_dict_all_fields(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions.from_dict({"model": "gpt-4", "agent": "plan", "variant": "full", "pure": True, "log_level": "debug", "extra_args": ["--verbose"]})
        assert opts.model == "gpt-4"
        assert opts.agent == "plan"
        assert opts.variant == "full"
        assert opts.pure is True
        assert opts.log_level == "debug"
        assert opts.extra_args == ["--verbose"]

    def test_from_dict_empty(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions.from_dict({})
        assert opts.model is None
        assert opts.agent is None
        assert opts.pure is False
        assert opts.extra_args == []

    def test_from_dict_partial(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions.from_dict({"model": "claude"})
        assert opts.model == "claude"
        assert opts.agent is None

    def test_from_dict_pure_coercion(self):
        from core.runner import OpenCodeOptions

        opts = OpenCodeOptions.from_dict({"pure": 1})
        assert opts.pure is True

        opts2 = OpenCodeOptions.from_dict({"pure": "yes"})
        assert opts2.pure is True

    def test_roundtrip(self):
        from core.runner import OpenCodeOptions

        original = OpenCodeOptions(model="gpt-4", agent="plan", variant="full", pure=True, log_level="info", extra_args=["-v"])
        restored = OpenCodeOptions.from_dict(original.to_dict())
        assert restored == original


# ===========================================================================
# core.runner — OpenCodeRunner
# ===========================================================================


class TestOpenCodeRunner:
    def test_initialization(self):
        from core.runner import OpenCodeRunner

        r = OpenCodeRunner(binary="my-bin", timeout=30)
        assert r.binary == "my-bin"
        assert r.timeout == 30

    def test_initialization_defaults(self):
        from core.runner import OpenCodeRunner

        r = OpenCodeRunner()
        assert r.binary == "opencode"
        assert r.timeout == 600

    def test_run_success(self):
        from core.runner import OpenCodeRunner, RunResult

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "Hello world"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            r = OpenCodeRunner(binary="echo")
            result = r.run("test prompt")

            assert result.success is True
            assert result.output == "Hello world"
            assert result.error == ""
            assert result.exit_code == 0

    def test_run_failure(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 1
            mock_proc.stdout = ""
            mock_proc.stderr = "error occurred"
            mock_run.return_value = mock_proc

            r = OpenCodeRunner(binary="false")
            result = r.run("prompt")

            assert result.success is False
            assert result.error == "error occurred"
            assert result.exit_code == 1

    def test_run_timeout(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            import subprocess

            mock_run.side_effect = subprocess.TimeoutExpired(cmd="opencode", timeout=10)

            r = OpenCodeRunner(binary="opencode", timeout=10)
            result = r.run("prompt")

            assert result.success is False
            assert "timed out" in result.error
            assert result.exit_code == -1

    def test_run_file_not_found(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()

            r = OpenCodeRunner()
            result = r.run("prompt")

            assert result.success is False
            assert "not found" in result.error
            assert result.exit_code == -1

    def test_run_os_error(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("permission denied")

            r = OpenCodeRunner()
            result = r.run("prompt")

            assert result.success is False
            assert "permission denied" in result.error
            assert result.exit_code == -1

    def test_run_custom_timeout(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "ok"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            r = OpenCodeRunner(timeout=600)
            r.run("prompt", timeout=30)
            assert mock_run.call_args[1]["timeout"] == 30

    def test_run_default_timeout(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "ok"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            r = OpenCodeRunner(timeout=600)
            r.run("prompt")
            assert mock_run.call_args[1]["timeout"] == 600

    def test_run_uses_correct_command(self):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            r = OpenCodeRunner(binary="my-opencode")
            r.run("some prompt")
            cmd = mock_run.call_args.kwargs["args"]
            assert cmd[0:3] == ["my-opencode", "run", "--file"]
            assert cmd[3].endswith("current_prompt.md")
            assert cmd[4:6] == ["--dir", "."]
            assert cmd[6] == "Follow the instructions in the attached file exactly."

    def test_run_continue_session_uses_dir(self, tmp_path):
        from core.runner import OpenCodeRunner

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = ""
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc

            workdir = tmp_path / "workdir"
            workdir.mkdir()
            prompt_file = workdir / ".openloop" / "current_prompt.md"

            r = OpenCodeRunner(binary="my-opencode")
            r.run(
                "STATE UPDATE REQUIRED",
                cwd=str(workdir),
                continue_session=True,
                prompt_file=prompt_file,
            )
            cmd = mock_run.call_args.kwargs["args"]
            assert cmd[0:4] == ["my-opencode", "run", "-c", "--file"]
            assert cmd[4].endswith("current_prompt.md")
            assert cmd[5:7] == ["--dir", str(workdir.resolve())]
            assert cmd[7] == "No valid state update found. Follow the instructions in the attached file."


# ===========================================================================
# core.agent — AgentLoader & AgentDefinition
# ===========================================================================


class TestAgentDefinition:
    def test_default_initialization(self):
        from core.agent import AgentDefinition

        a = AgentDefinition(name="test", role="tester")
        assert a.name == "test"
        assert a.role == "tester"
        assert a.expected_output_format == "json_block"
        assert a.system_prompt == ""

    def test_custom_initialization(self):
        from core.agent import AgentDefinition

        a = AgentDefinition(name="x", role="y", expected_output_format="xml", system_prompt="Do stuff")
        assert a.name == "x"
        assert a.role == "y"
        assert a.expected_output_format == "xml"
        assert a.system_prompt == "Do stuff"


class TestAgentLoader:
    def test_list_agents_empty_dir(self, tmp_path):
        from core.agent import AgentLoader

        loader = AgentLoader(str(tmp_path / "nonexistent"))
        assert loader.list_agents() == []

    def test_list_agents_empty_existing_dir(self, tmp_path):
        from core.agent import AgentLoader

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        loader = AgentLoader(str(agents_dir))
        assert loader.list_agents() == []

    def test_list_agents_returns_sorted(self, tmp_path):
        from core.agent import AgentLoader

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "zebra.md").write_text("---\nname: zebra\nrole: test\n---\ncontent")
        (agents_dir / "alpha.md").write_text("---\nname: alpha\nrole: test\n---\ncontent")
        loader = AgentLoader(str(agents_dir))
        assert loader.list_agents() == ["alpha", "zebra"]

    def test_list_agents_ignores_non_md(self, tmp_path):
        from core.agent import AgentLoader

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "tester.md").write_text("---\nname: tester\nrole: r\n---\nprompt")
        (agents_dir / "readme.txt").write_text("hello")
        loader = AgentLoader(str(agents_dir))
        assert loader.list_agents() == ["tester"]

    def test_get_agent_missing_raises(self, tmp_path):
        from core.agent import AgentLoader

        loader = AgentLoader(str(tmp_path / "agents"))
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.get_agent("nonexistent")

    def test_get_agent_success(self, tmp_path):
        from core.agent import AgentLoader

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "tester.md").write_text(
            "---\nname: tester\nrole: author\n---\nYou are a test author."
        )
        loader = AgentLoader(str(agents_dir))
        agent = loader.get_agent("tester")
        assert agent.name == "tester"
        assert agent.role == "author"
        assert agent.system_prompt == "You are a test author."

    def test_load_all(self, tmp_path):
        from core.agent import AgentLoader

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "a.md").write_text("---\nname: a\nrole: r1\n---\np1")
        (agents_dir / "b.md").write_text("---\nname: b\nrole: r2\n---\np2")
        loader = AgentLoader(str(agents_dir))
        agents = loader.load_all()
        assert len(agents) == 2
        names = {a.name for a in agents}
        assert names == {"a", "b"}

    def test_parse_frontmatter_basic(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "test.md"
        path.write_text("---\nname: test\nrole: tester\n---\n\nYou are a test agent.\n\nDo work.")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.name == "test"
        assert agent.role == "tester"
        assert agent.system_prompt == "You are a test agent.\n\nDo work."

    def test_parse_frontmatter_default_output_format(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nname: a\nrole: r\n---\nprompt")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.expected_output_format == "json_block"

    def test_parse_frontmatter_custom_output_format(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nname: a\nrole: r\nexpected_output_format: xml\n---\nprompt")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.expected_output_format == "xml"

    def test_parse_frontmatter_missing_opening(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("no frontmatter")
        with pytest.raises(ValueError, match="Missing YAML frontmatter"):
            AgentLoader._load_file(AgentLoader, path)

    def test_parse_frontmatter_unclosed(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nname: a\nrole: r\n")
        with pytest.raises(ValueError, match="Unclosed"):
            AgentLoader._load_file(AgentLoader, path)

    def test_parse_frontmatter_no_name(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nrole: r\n---\nprompt")
        with pytest.raises(ValueError, match="Missing required field 'name'"):
            AgentLoader._load_file(AgentLoader, path)

    def test_parse_frontmatter_name_from_filename(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "custom_name.md"
        path.write_text("---\nrole: r\n---\nprompt")
        with pytest.raises(ValueError, match="Missing required field 'name'"):
            AgentLoader._load_file(AgentLoader, path)

    def test_parse_frontmatter_skips_comments(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\n# this is a comment\nname: a\nrole: r\n---\nprompt")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.name == "a"

    def test_parse_frontmatter_multiple_values(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nname: a\nrole: r\nextra: value\n---\nprompt")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.name == "a"
        assert agent.role == "r"
        assert agent.expected_output_format == "json_block"

    def test_parse_frontmatter_empty_system_prompt(self, tmp_path):
        from core.agent import AgentLoader

        path = tmp_path / "a.md"
        path.write_text("---\nname: a\nrole: r\n---")
        agent = AgentLoader._load_file(AgentLoader, path)
        assert agent.system_prompt == ""


# ===========================================================================
# core.engine — WorkflowConfig & ExecutionEngine
# ===========================================================================


class TestWorkflowConfig:
    def test_default_initialization(self):
        from core.engine import WorkflowConfig

        wc = WorkflowConfig()
        assert wc.preparation_agents == []
        assert wc.loop_agents == []
        assert wc.finalization_agents == []
        assert wc.end_state_condition == "is_complete == True"
        assert wc.max_loops == 10
        assert wc.finalize_on_abort is False
        assert wc.name is None

    def test_from_dict_all_fields(self):
        from core.engine import WorkflowConfig

        wc = WorkflowConfig.from_dict(
            {
                "preparation_agents": ["prep"],
                "loop_agents": ["a", "b"],
                "finalization_agents": ["fin"],
                "end_state_condition": "payload.get('x') > 5",
                "max_loops": 20,
                "finalize_on_abort": True,
                "name": "my-wf",
                "log_dir": "./logs/my-project",
            }
        )
        assert wc.preparation_agents == ["prep"]
        assert wc.loop_agents == ["a", "b"]
        assert wc.finalization_agents == ["fin"]
        assert wc.end_state_condition == "payload.get('x') > 5"
        assert wc.max_loops == 20
        assert wc.finalize_on_abort is True
        assert wc.name == "my-wf"
        assert wc.log_dir == "./logs/my-project"

    def test_from_dict_empty(self):
        from core.engine import WorkflowConfig

        wc = WorkflowConfig.from_dict({})
        assert wc.preparation_agents == []
        assert wc.loop_agents == []
        assert wc.finalization_agents == []

    def test_to_dict(self):
        from core.engine import WorkflowConfig

        wc = WorkflowConfig(
            preparation_agents=["prep"],
            loop_agents=["a"],
            finalization_agents=["fin"],
            end_state_condition="is_complete == True",
            max_loops=5,
            finalize_on_abort=True,
        )
        d = wc.to_dict()
        assert d["preparation_agents"] == ["prep"]
        assert d["loop_agents"] == ["a"]
        assert d["finalization_agents"] == ["fin"]
        assert d["end_state_condition"] == "is_complete == True"
        assert d["max_loops"] == 5
        assert d["finalize_on_abort"] is True
        assert d["workdir"] is None
        assert d["init_script"] is None
        assert d["name"] is None
        assert d["log_dir"] is None
        assert "opencode_defaults" not in d

    def test_to_dict_with_log_dir(self):
        from core.engine import WorkflowConfig

        wc = WorkflowConfig(
            loop_agents=["a"],
            log_dir="./custom-logs",
        )
        d = wc.to_dict()
        assert d["log_dir"] == "./custom-logs"

    def test_load_from_file(self, tmp_path):
        from core.engine import WorkflowConfig

        wf = tmp_path / "wf.json"
        wf.write_text(json.dumps({"loop_agents": ["a"], "max_loops": 3}))
        wc = WorkflowConfig.load(str(wf))
        assert wc.loop_agents == ["a"]
        assert wc.max_loops == 3

    def test_load_file_not_found(self, tmp_path):
        from core.engine import WorkflowConfig

        with pytest.raises(FileNotFoundError, match="not found"):
            WorkflowConfig.load(str(tmp_path / "no.json"))

    def test_load_invalid_json(self, tmp_path):
        from core.engine import WorkflowConfig

        wf = tmp_path / "wf.json"
        wf.write_text("not json")
        with pytest.raises(ValueError, match="Invalid workflow JSON"):
            WorkflowConfig.load(str(wf))

    def test_load_non_dict_json(self, tmp_path):
        from core.engine import WorkflowConfig

        wf = tmp_path / "wf.json"
        wf.write_text('["list"]')
        with pytest.raises(ValueError, match="must contain a JSON object"):
            WorkflowConfig.load(str(wf))


class TestExecutionEngine:
    def test_default_initialization(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        assert engine.state.current_phase == "preparation"
        assert engine.state.iteration == 0
        assert engine._stop_event is not None

    def test_custom_stop_event(self):
        from core.engine import ExecutionEngine

        stop = threading.Event()
        engine = ExecutionEngine(stop_event=stop)
        assert engine._stop_event is stop

    def test_custom_logger(self):
        from core.engine import ExecutionEngine

        logs = []
        engine = ExecutionEngine(logger=logs.append)
        engine.log("hello")
        assert "hello" in logs

    def test_execute_workflow_empty_loop(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        state = engine.execute_workflow_data(
            {
                "loop_agents": [],
                "max_loops": 5,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.is_complete is True
        assert state.termination_reason == "completed"

    def test_execute_workflow_completes(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "max_loops": 5,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.is_complete is True
        assert state.termination_reason == "completed"

    def test_execute_workflow_max_loops(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "max_loops": 3,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.iteration == 3
        assert state.termination_reason == "max_loops_reached"

    def test_agent_error_sets_termination_reason(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": False, "output": ""},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.termination_reason == "agent_error:a"

    def test_prep_agent_error_aborts_before_loop(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": False, "output": ""},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader(
            {"p": "Prep", "a": "Loop"}
        )
        state = engine.execute_workflow_data(
            {
                "preparation_agents": ["p"],
                "loop_agents": ["a"],
                "max_loops": 5,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.termination_reason == "agent_error:p"
        assert state.iteration == 0

    def test_multiple_prep_agents_stop_on_first_error(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": False, "output": ""},
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader(
            {"p1": "Prep", "p2": "Prep"}
        )
        state = engine.execute_workflow_data(
            {
                "preparation_agents": ["p1", "p2"],
                "loop_agents": [],
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.termination_reason == "agent_error:p1"
        assert engine.runner.run.call_count == 1

    def test_loop_multiple_agents_stop_on_first_error(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": False, "output": ""},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader(
            {"a": "Agent", "b": "Agent"}
        )
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a", "b"],
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.termination_reason == "agent_error:a"
        assert state.iteration == 1
        assert engine.runner.run.call_count == 1

    def test_malformed_agent_output_no_crash(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": "No state update here"},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.is_complete is False
        assert state.iteration == 1

    def test_stop_event_interrupts_loop(self):
        from core.engine import ExecutionEngine
        import time

        stop = threading.Event()
        engine = ExecutionEngine(stop_event=stop)
        responses = [
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
        ]
        idx = [0]

        def slow_run(prompt, opts=None, timeout=None, cwd=None, init_script=None, **kwargs):
            time.sleep(0.05)
            r = responses[idx[0] % len(responses)]
            idx[0] += 1
            return type("R", (), dict(r, error="", exit_code=0 if r["success"] else 1))()

        engine.runner = MagicMock()
        engine.runner.run.side_effect = slow_run
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})

        def delayed_stop():
            time.sleep(0.2)
            stop.set()

        threading.Thread(target=delayed_stop, daemon=True).start()
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "max_loops": 10,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.termination_reason == "stopped"

    def test_preparation_agent_runs_before_loop(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"payload": {"prepped": true}}</state_update>'},
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"prep": "Prep", "worker": "Worker"})
        state = engine.execute_workflow_data(
            {
                "preparation_agent": "prep",
                "loop_agents": ["worker"],
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.payload.get("prepped") is True

    def test_finalization_runs_on_completion(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
                {"success": True, "output": '<state_update>{"payload": {"done": true}}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent", "fin": "Finisher"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "finalization_agent": "fin",
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert state.payload.get("done") is True
        assert state.current_phase == "finalization"

    def test_finalization_skipped_on_max_loops_without_flag(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent", "fin": "Finisher"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "finalization_agent": "fin",
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
                "finalize_on_abort": False,
            }
        )
        assert state.termination_reason == "max_loops_reached"

    def test_finalization_runs_on_max_loops_with_flag(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
                {"success": True, "output": '<state_update>{"payload": {"finalized": true}}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent", "fin": "Finisher"})
        state = engine.execute_workflow_data(
            {
                "loop_agents": ["a"],
                "finalization_agent": "fin",
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
                "finalize_on_abort": True,
            }
        )
        assert state.payload.get("finalized") is True

    def test_build_prompt_includes_state(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        agent = MagicMock()
        agent.system_prompt = "You are an agent."
        prompt = engine._build_prompt(agent)
        assert "You are an agent." in prompt
        assert "# Current State" in prompt
        assert "```json" in prompt
        assert '"current_phase"' in prompt

    def test_evaluate_end_condition_is_complete(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.state.is_complete = True
        assert engine._evaluate_end_condition("is_complete == True") is True

    def test_evaluate_end_condition_not_complete(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.state.is_complete = False
        assert engine._evaluate_end_condition("is_complete == True") is False

    def test_evaluate_end_condition_custom_expression(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.state.payload = {"score": 85}
        result = engine._evaluate_end_condition("payload.get('score', 0) >= 80")
        assert result is True

    def test_evaluate_end_condition_custom_expression_false(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.state.payload = {"score": 50}
        result = engine._evaluate_end_condition("payload.get('score', 0) >= 80")
        assert result is False

    def test_evaluate_end_condition_bad_expression_returns_false(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        result = engine._evaluate_end_condition("not valid python == ")
        assert result is False

    def test_execute_workflow_from_file(self, tmp_path):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
            ]
        )
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        wf = tmp_path / "wf.json"
        wf.write_text(json.dumps({"loop_agents": ["a"], "max_loops": 1, "end_state_condition": "is_complete == True"}))
        state = engine.execute_workflow(str(wf))
        assert state.is_complete is True

    def test_loop_state_passed_between_agents(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        prompts = []

        def capture_run(prompt, timeout=None, **kwargs):
            prompts.append(prompt)
            return self._make_mock_runner(
                [
                    {"success": True, "output": '<state_update>{"payload": {"step": 1}}</state_update>'},
                ]
            ).run(prompt, timeout)

        engine.runner = MagicMock()
        engine.runner.run.side_effect = [
            type("R", (), {"success": True, "output": '<state_update>{"payload": {"step": 1}}</state_update>', "error": "", "exit_code": 0})(),
            type("R", (), {"success": True, "output": '<state_update>{"is_complete": true, "payload": {"step": 2}}</state_update>', "error": "", "exit_code": 0})(),
        ]
        engine.agent_loader = self._make_mock_agent_loader({"a": "First", "b": "Second"})
        engine.execute_workflow_data(
            {
                "loop_agents": ["a", "b"],
                "max_loops": 1,
                "end_state_condition": "is_complete == True",
            }
        )
        assert engine.state.payload.get("step") == 2

    def test_log_method(self):
        from core.engine import ExecutionEngine

        logs = []
        engine = ExecutionEngine(logger=logs.append)
        engine.log("test message")
        assert "test message" in logs
        assert "[OpenLoop]" not in logs[-1]

    def test_opencode_defaults_merged_and_passed_to_runner(self, tmp_path):
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "a.md").write_text("---\nname: a\nrole: test\n---\nYou are A.")

        engine = ExecutionEngine()
        calls = []

        class TrackingRunner:
            PROMPT_FILENAME = "prompt.md"

            def run(self, prompt, opts=None, timeout=None, cwd=None, init_script=None, **kwargs):
                calls.append(opts)
                return type("R", (), {"success": True, "output": '<state_update>{"is_complete": true}</state_update>', "error": "", "exit_code": 0})()

        engine.runner = TrackingRunner()
        engine.agent_loader = type("L", (), {"get_agent": lambda self, name: type("A", (), {"name": name, "can_complete": True, "role": "auditor", "system_prompt": "You are A."})()})()

        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(model="gpt-4", agent="build"),
            "workdir": None,
            "init_script": None,
            "log_dir": ".openloop",
            "no_log_file": False,
        })()

        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
        })

        assert len(calls) == 1  # initial (breaks on first valid state_update)
        passed_opts = calls[0]
        assert passed_opts.model == "gpt-4"
        assert passed_opts.agent == "build"

    def test_opencode_defaults_workflow_overrides_config(self, tmp_path):
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "a.md").write_text("---\nname: a\nrole: test\n---\nYou are A.")

        engine = ExecutionEngine()
        calls = []

        class TrackingRunner:
            PROMPT_FILENAME = "prompt.md"

            def run(self, prompt, opts=None, timeout=None, cwd=None, init_script=None, **kwargs):
                calls.append(opts)
                return type("R", (), {"success": True, "output": '<state_update>{"is_complete": true}</state_update>', "error": "", "exit_code": 0})()

        engine.runner = TrackingRunner()
        engine.agent_loader = type("L", (), {"get_agent": lambda self, name: type("A", (), {"name": name, "can_complete": True, "role": "auditor", "system_prompt": "You are A."})()})()

        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(model="gpt-4", agent="build"),
            "workdir": None,
            "init_script": None,
            "log_dir": ".openloop",
            "no_log_file": False,
        })()

        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
            "opencode_defaults": {"model": "claude"},
        })

        assert len(calls) == 1  # initial (breaks on first valid state_update)
        passed_opts = calls[0]
        assert passed_opts.model == "claude"
        assert passed_opts.agent == "build"

    def test_log_dir_uses_config_default(self, tmp_path):
        from pathlib import Path
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner()
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        expected = tmp_path / "config-logs"
        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(),
            "workdir": None,
            "init_script": None,
            "log_dir": str(expected),
            "no_log_file": False,
        })()
        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
        })
        assert engine._log_dir == expected

    def test_log_dir_workflow_overrides_config(self, tmp_path):
        from pathlib import Path
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        engine = ExecutionEngine()
        engine.runner = self._make_mock_runner()
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        config_dir = tmp_path / "config-logs"
        wf_dir = tmp_path / "wf-logs"
        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(),
            "workdir": None,
            "init_script": None,
            "log_dir": str(config_dir),
            "no_log_file": False,
        })()
        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
            "log_dir": str(wf_dir),
        })
        assert engine._log_dir == wf_dir

    def test_log_dir_cli_overrides_all(self, tmp_path):
        from pathlib import Path
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        config_dir = tmp_path / "config-logs"
        wf_dir = tmp_path / "wf-logs"
        cli_dir = tmp_path / "cli-logs"
        engine = ExecutionEngine(log_dir=str(cli_dir))
        engine.runner = self._make_mock_runner()
        engine.agent_loader = self._make_mock_agent_loader({"a": "Agent"})
        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(),
            "workdir": None,
            "init_script": None,
            "log_dir": str(config_dir),
            "no_log_file": False,
        })()
        engine.execute_workflow_data({
            "loop_agents": ["a"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
            "log_dir": str(wf_dir),
        })
        assert engine._log_dir == cli_dir

    def test_log_phase_banner_inside_agent(self, tmp_path):
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions
        from tools.looplog import LogParser

        log_dir = tmp_path
        engine = ExecutionEngine(log_dir=str(log_dir))
        runner = self._make_mock_runner(
            [
                {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'},
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
                {"success": True, "output": '<state_update>{"is_complete": true}</state_update>'},
            ]
        )
        runner.PROMPT_FILENAME = "current_prompt.md"
        engine.runner = runner
        engine.agent_loader = self._make_mock_agent_loader(
            {"prep": "Prep", "a": "Agent", "fin": "Fin"}
        )
        engine.config = type("C", (), {
            "opencode_defaults": OpenCodeOptions(),
            "workdir": None,
            "init_script": None,
            "log_dir": str(log_dir),
            "no_log_file": False,
            "default_max_loops": 10,
        })()
        state = engine.execute_workflow_data({
            "preparation_agents": ["prep"],
            "loop_agents": ["a"],
            "finalization_agents": ["fin"],
            "max_loops": 1,
            "end_state_condition": "is_complete == True",
            "name": "logtest",
            "log_dir": str(log_dir),
        })
        assert state.termination_reason == "completed"

        log_files = list(log_dir.glob("openloop-run-logtest-*.log"))
        assert len(log_files) == 1
        parser = LogParser(log_files[0])
        sections = parser.parse()
        assert sections and sections[0].tag == "openloop_log"
        root = sections[0]

        def collect(sec):
            yield sec
            for c in sec.children:
                yield from collect(c)

        def text(sec):
            return parser.get_raw_text(sec.start, sec.end)

        all_secs = list(collect(root))

        # Phase label messages plus the banner must live INSIDE the agent.
        phase_labels = ("Preparation phase:", "  Running agent:", "Finalization phase:")
        for sec in all_secs:
            if sec.tag != "system":
                continue
            if not any(label in text(sec) for label in phase_labels):
                continue
            parent = next(
                (p for p in all_secs if any(c is sec for c in p.children)), None
            )
            assert parent is not None and parent.tag == "agent", (
                f"system section with phase label must be inside <agent> "
                f"(found under <{getattr(parent, 'tag', None)}>)"
            )
            assert "=" * 20 in text(sec), (
                "phase label and banner must be in the same system block"
            )

        # Every agent run must contain its label+banner system block.
        agents = [s for s in all_secs if s.tag == "agent"]
        assert len(agents) == 3  # preparation, loop, finalization
        for agent in agents:
            sys_blocks = [c for c in agent.children if c.tag == "system"]
            assert any("=" * 20 in text(s) for s in sys_blocks), (
                f"agent {agent.label} is missing its banner system block"
            )

    # -- correction prompt --

    def test_correction_prompt_attempt1_is_lean(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        prompt = engine._build_correction_prompt("missing", "amala", 1)
        assert prompt.startswith("STATE UPDATE REQUIRED")
        assert "<state_update>" in prompt
        assert "</state_update>" in prompt
        assert "FORMAT RULES" not in prompt
        assert "No further work is needed" not in prompt
        assert "STATE UPDATE REQUIRED" not in prompt[15:]

    def test_correction_prompt_attempt1_uses_failure_hint(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        prompt = engine._build_correction_prompt("missing", "amala", 1)
        assert "no usable state block" in prompt
        assert "Reconstruct the state" in prompt

    def test_correction_prompt_attempt1_no_state_embedded(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        engine.state.payload["summary"] = "secret marker"
        prompt = engine._build_correction_prompt("missing", "amala", 1)
        assert "secret marker" not in prompt

    def test_correction_prompt_attempt2_embeds_initial_state(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        state_json = '{"payload": {"summary": "marker-123"}}'
        prompt = engine._build_correction_prompt("missing", "amala", 2, state_json)
        assert prompt.startswith("FINAL STATE UPDATE REQUIRED")
        assert "marker-123" in prompt
        assert "Previous attempt failed:" in prompt
        assert "at the start of this run" in prompt

    def test_correction_prompt_attempt2_no_template(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        prompt = engine._build_correction_prompt("missing", "amala", 2, "{}")
        assert '{"is_complete": false, "payload": {"summary": "Brief factual summary of completed work"}}' not in prompt
        assert "<state_update>" in prompt
        assert "is_complete, termination_reason, and payload" in prompt

    def test_correction_prompt_attempt2_uses_failure_hint(self):
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()
        prompt = engine._build_correction_prompt("file_reference", "amala", 2, "{}")
        assert "You wrote or referenced a state file" in prompt

    def test_correction_prompt_completion_rules(self):
        from core.agent import AgentDefinition
        from core.engine import ExecutionEngine

        engine = ExecutionEngine()

        can_complete = AgentDefinition(
            name="a", role="auditor", can_complete=True
        )
        prompt = engine._build_correction_prompt("missing", can_complete, 1)
        assert "Set is_complete=true only if" in prompt

        no_complete = AgentDefinition(
            name="b", role="tester", can_complete=False
        )
        prompt = engine._build_correction_prompt("missing", no_complete, 1)
        assert "Set is_complete=false." in prompt
        assert "is_complete=true" not in prompt

    # -- helpers --

    @staticmethod
    def _make_mock_runner(responses: list | None = None):
        responses = responses or [
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'}
        ]
        idx = [0]

        def side_effect(prompt, opts=None, timeout=None, cwd=None, init_script=None, **kwargs):
            r = responses[idx[0] % len(responses)]
            idx[0] += 1
            return type("R", (), dict(r, error="", exit_code=0 if r["success"] else 1))()

        runner = MagicMock()
        runner.run.side_effect = side_effect
        return runner

    @staticmethod
    def _make_mock_agent_loader(agents: dict | None = None):
        agents = agents or {"test": "You are a test agent."}
        loader = MagicMock()

        def get_agent(name):
            prompt = agents.get(name, "You are a default agent.")
            return type("A", (), {"name": name, "can_complete": True, "role": "auditor", "system_prompt": prompt})()

        loader.get_agent.side_effect = get_agent
        return loader


# ===========================================================================
# openloop — parse_args, main, _run_cli, _run_gui
# ===========================================================================


class TestOpenLoopEntryPoint:
    def test_parse_args_defaults(self):
        from openloop import parse_args

        args = parse_args([])
        assert args.cli is False
        assert args.workflow is None
        assert args.config is None

    def test_parse_args_cli_mode(self):
        from openloop import parse_args

        args = parse_args(["--cli"])
        assert args.cli is True
        assert args.workflow is None

    def test_parse_args_workflow(self):
        from openloop import parse_args

        args = parse_args(["--cli", "--workflow", "my_workflow.json"])
        assert args.cli is True
        assert args.workflow == "my_workflow.json"

    def test_parse_args_custom_config(self):
        from openloop import parse_args

        args = parse_args(["--config", "custom.json"])
        assert args.config == "custom.json"

    def test_main_cli_without_workflow_exits(self):
        from openloop import main

        with pytest.raises(SystemExit) as exc:
            main(["--cli"])
        assert exc.value.code == 1

    def test_main_cli_with_workflow_completed(self, tmp_path):
        from openloop import main

        wf = tmp_path / "test.json"
        wf.write_text(json.dumps({"loop_agents": ["a"], "max_loops": 1}))

        mock_config = MagicMock()
        mock_engine = MagicMock()
        mock_state = MagicMock()
        mock_state.termination_reason = "completed"
        mock_state.iteration = 3
        mock_state.is_complete = True
        mock_engine.state = mock_state

        with (
            patch("core.config.Config.load", return_value=mock_config),
            patch("core.engine.ExecutionEngine", return_value=mock_engine),
            pytest.raises(SystemExit) as exc,
        ):
            main(["--cli", "--workflow", str(wf)])
        assert exc.value.code == 0

    def test_main_cli_import_error(self, tmp_path):
        from openloop import main

        wf = tmp_path / "test.json"
        wf.write_text(json.dumps({"loop_agents": ["a"]}))

        with (
            patch("core.config.Config.load", return_value=MagicMock()),
            patch("core.engine.ExecutionEngine", side_effect=ImportError("missing dep")),
            pytest.raises(SystemExit) as exc,
        ):
            main(["--cli", "--workflow", str(wf)])
        assert exc.value.code == 1

    def test_main_gui_mode(self):
        from openloop import main

        mock_config = MagicMock()

        with (
            patch("core.config.Config.load", return_value=mock_config),
            patch("ui.app.WorkflowApp") as mock_app_cls,
        ):
            mock_app = MagicMock()
            mock_app_cls.return_value = mock_app
            main([])
            mock_app.run.assert_called_once()

    def test_main_gui_keyboard_interrupt(self):
        from openloop import main

        mock_config = MagicMock()

        with (
            patch("core.config.Config.load", return_value=mock_config),
            patch("ui.app.WorkflowApp") as mock_app_cls,
        ):
            mock_app = MagicMock()
            mock_app.run.side_effect = KeyboardInterrupt()
            mock_app_cls.return_value = mock_app
            main([])
            mock_app.on_closing.assert_called_once()

    def test_main_gui_import_error(self):
        from openloop import main
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ui.app":
                raise ImportError("no tkinter")
            return real_import(name, *args, **kwargs)

        with (
            patch("core.config.Config.load", return_value=MagicMock()),
            patch("builtins.__import__", side_effect=mock_import),
            pytest.raises(SystemExit) as exc,
        ):
            main([])
        assert exc.value.code == 1


# ===========================================================================
# ui.app — WorkflowApp (smoke tests, heavily mocked)
# ===========================================================================


class TestWorkflowApp:
    def test_initialization(self):
        with (
            patch("ui.app.Tk") as mock_tk,
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp(config_path="test.json")
            assert app._config_path == "test.json"
            assert app._running is False

    def test_run_starts_mainloop(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._root = MagicMock()
            app.run()
            app._root.mainloop.assert_called_once()
            app._root.protocol.assert_called_once_with("WM_DELETE_WINDOW", app.on_closing)

    def test_on_closing_destroys_root(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._root = MagicMock()
            app._running = False
            app.on_closing()
            app._root.destroy.assert_called_once()

    def test_on_closing_stops_if_running(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._root = MagicMock()
            app._running = True
            with patch.object(app, "_stop_execution") as mock_stop:
                app.on_closing()
                mock_stop.assert_called_once()
                app._root.destroy.assert_called_once()

    def _setup_app_open_code_vars(self, app):
        app._workdir_var = MagicMock()
        app._workdir_var.get.return_value = ""
        app._init_script_var = MagicMock()
        app._init_script_var.get.return_value = ""
        app._workflow_name_var = MagicMock()
        app._workflow_name_var.get.return_value = ""
        app._oc_model_var = MagicMock()
        app._oc_model_var.get.return_value = ""
        app._oc_agent_var = MagicMock()
        app._oc_agent_var.get.return_value = ""
        app._oc_variant_var = MagicMock()
        app._oc_variant_var.get.return_value = ""
        app._oc_pure_var = MagicMock()
        app._oc_pure_var.get.return_value = False
        app._log_dir_var = MagicMock()
        app._log_dir_var.get.return_value = ""
        app._no_log_file_var = MagicMock()
        app._no_log_file_var.get.return_value = False

    def test_get_workflow_data(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            self._setup_app_open_code_vars(app)
            app._prep_listbox = MagicMock()
            app._prep_listbox.size.return_value = 1
            app._prep_listbox.get.return_value = ["prepper"]
            app._loop_listbox = MagicMock()
            app._loop_listbox.get.return_value = ["a", "b"]
            app._loop_listbox.size.return_value = 2
            app._final_listbox = MagicMock()
            app._final_listbox.size.return_value = 1
            app._final_listbox.get.return_value = ["finisher"]
            app._max_loops_var = MagicMock()
            app._max_loops_var.get.return_value = "5"
            app._end_condition_var = MagicMock()
            app._end_condition_var.get.return_value = "custom == True"
            app._finalize_on_abort_var = MagicMock()
            app._finalize_on_abort_var.get.return_value = True

            data = app._get_workflow_data()
            assert data["preparation_agents"] == ["prepper"]
            assert data["loop_agents"] == ["a", "b"]
            assert data["finalization_agents"] == ["finisher"]
            assert data["max_loops"] == 5
            assert data["end_state_condition"] == "custom == True"
            assert data["finalize_on_abort"] is True

    def test_get_workflow_data_no_prep_or_final(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            self._setup_app_open_code_vars(app)
            app._prep_listbox = MagicMock()
            app._prep_listbox.get.return_value = ()
            app._prep_listbox.size.return_value = 0
            app._loop_listbox = MagicMock()
            app._loop_listbox.get.return_value = ()
            app._loop_listbox.size.return_value = 0
            app._final_listbox = MagicMock()
            app._final_listbox.get.return_value = ()
            app._final_listbox.size.return_value = 0
            app._max_loops_var = MagicMock()
            app._max_loops_var.get.return_value = "not_a_number"
            app._end_condition_var = MagicMock()
            app._end_condition_var.get.return_value = "cond"
            app._finalize_on_abort_var = MagicMock()
            app._finalize_on_abort_var.get.return_value = False

            data = app._get_workflow_data()
            assert data.get("preparation_agents") == []
            assert data["loop_agents"] == []
            assert data.get("finalization_agents") == []
            assert data["max_loops"] == 10

    def test_load_workflow_into_ui(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            self._setup_app_open_code_vars(app)
            app._prep_listbox = MagicMock()
            app._loop_listbox = MagicMock()
            app._final_listbox = MagicMock()
            app._max_loops_var = MagicMock()
            app._end_condition_var = MagicMock()
            app._finalize_on_abort_var = MagicMock()
            app._workflow_name_var = MagicMock()

            app._load_workflow_into_ui(
                {
                    "name": "my-workflow",
                    "preparation_agents": ["prep"],
                    "loop_agents": ["a", "b"],
                    "finalization_agents": ["fin"],
                    "max_loops": 7,
                    "end_state_condition": "x == 1",
                    "finalize_on_abort": True,
                }
            )
            app._prep_listbox.delete.assert_called_once()
            app._loop_listbox.delete.assert_called_once()
            app._final_listbox.delete.assert_called_once()
            app._prep_listbox.insert.assert_called()
            app._loop_listbox.insert.assert_called()
            app._final_listbox.insert.assert_called()
            app._max_loops_var.set.assert_called_with("7")
            app._end_condition_var.set.assert_called_with("x == 1")
            app._finalize_on_abort_var.set.assert_called_with(True)
            app._workflow_name_var.set.assert_called_with("my-workflow")

    def test_log_queues_message(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._log_queue = MagicMock()
            app._log("hello")
            app._log_queue.put.assert_called_with(("msg", "[OpenLoop] hello"))

    def test_execution_done_updates_buttons(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._start_btn = MagicMock()
            app._stop_btn = MagicMock()
            app._status_dot = MagicMock()
            app._execution_done()
            assert app._running is False
            app._start_btn.configure.assert_called_with(state="normal")
            app._stop_btn.configure.assert_called_with(state="disabled")

    def test_stop_execution(self):
        with (
            patch("ui.app.Tk"),
            patch("ui.app.WorkflowApp._build_ui"),
            patch("ui.app.WorkflowApp._load_config"),
            patch("ui.app.WorkflowApp._refresh_agent_list"),
            patch("ui.app.WorkflowApp._poll_log_queue"),
            patch("ui.app.WorkflowApp._update_title"),
        ):
            from ui.app import WorkflowApp

            app = WorkflowApp()
            app._stop_event = MagicMock()
            app._start_btn = MagicMock()
            app._stop_btn = MagicMock()
            app._stop_execution()
            app._stop_event.set.assert_called_once()
            assert app._running is False


# ===========================================================================
# tools.looplog — Viewer
# ===========================================================================


class TestLoopLogViewer:
    _XML_LOG = (
        "<openloop_log>\n"
        "<iteration number=\"1\" max=\"1\">\n"
        "<agent name=\"amala\" phase=\"loop\" iteration=\"1\" run_id=\"abc\">\n"
        "<stdout>\n"
        "hello from agent\n"
        "</stdout>\n"
        "<system>\n"
        "[OpenLoop]   State updated\n"
        "</system>\n"
        "</agent>\n"
        "</iteration>\n"
        "</openloop_log>\n"
    )

    @pytest.fixture()
    def parser(self, tmp_path):
        from tools.looplog import LogParser

        log = tmp_path / "test.log"
        log.write_text(self._XML_LOG, encoding="utf-8")
        return LogParser(log)

    def _find(self, sections, tag):
        found = []

        def walk(sec):
            if sec.tag == tag:
                found.append(sec)
            for child in sec.children:
                walk(child)

        for sec in sections:
            walk(sec)
        return found

    # -- Point 1: Multi-Select --

    def test_treeview_uses_extended_selectmode(self):
        from tools import looplog

        captured = {}

        def fake_treeview(parent, **kwargs):
            captured.update(kwargs)
            return MagicMock()

        def fake_widget(*args, **kwargs):
            return MagicMock()

        with (
            patch.object(looplog.tk, "Tk", return_value=MagicMock()),
            patch.object(looplog.ttk, "Treeview", side_effect=fake_treeview),
            patch.object(looplog.tk, "Menu", side_effect=fake_widget),
            patch.object(looplog.ttk, "Frame", side_effect=fake_widget),
            patch.object(looplog.ttk, "Label", side_effect=fake_widget),
            patch.object(looplog.ttk, "Combobox", side_effect=fake_widget),
            patch.object(looplog.ttk, "Button", side_effect=fake_widget),
            patch.object(looplog.ttk, "Checkbutton", side_effect=fake_widget),
            patch.object(looplog.ttk, "PanedWindow", side_effect=fake_widget),
            patch.object(looplog.ttk, "Scrollbar", side_effect=fake_widget),
            patch.object(looplog.tk, "Text", side_effect=fake_widget),
            patch.object(looplog.tk, "BooleanVar", side_effect=fake_widget),
            patch.object(looplog.tk, "StringVar", side_effect=fake_widget),
        ):
            app = object.__new__(looplog.LoopLogApp)
            app.root = looplog.tk.Tk()
            app._start_hide_system = False
            app._start_show_all = False
            app._start_wrap_lines = False
            app._start_filter = "all"
            app._build_ui()

        assert captured.get("selectmode") == "extended"

    def test_block_header_contains_label_and_lines(self, parser):
        from tools.looplog import _block_header

        iteration = self._find(parser.parse(), "iteration")[0]
        header = _block_header(iteration)
        assert "=" * 60 in header
        assert iteration.label in header
        assert f"lines {iteration.start + 1}-{iteration.end}" in header

    def test_display_sections_multiselect_shows_headers(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._filter_var = MagicMock()
        app._filter_var.get.return_value = "all"
        app._set_text = MagicMock()

        agent = self._find(sections, "agent")[0]
        system = self._find(sections, "system")[0]
        app._display_sections([agent, system])

        text = app._set_text.call_args[0][0]
        assert "hello from agent" in text
        assert "[OpenLoop]   State updated" in text
        assert agent.label in text
        assert system.label in text
        assert text.count("=" * 60) == 2

    def test_display_sections_single_no_header(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._filter_var = MagicMock()
        app._filter_var.get.return_value = "all"
        app._set_text = MagicMock()

        agent = self._find(sections, "agent")[0]
        app._display_sections([agent])
        text = app._set_text.call_args[0][0]
        assert "hello from agent" in text
        assert "=" * 60 not in text

    # -- Point 2: Hide System Tags --

    def test_update_filter_options_removes_system_when_hidden(self, parser):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app.sections = parser.parse()
        app._hide_system = MagicMock()
        app._hide_system.get.return_value = True
        app._filter_var = MagicMock()
        app._filter_var.get.return_value = "all"
        app._filter_dropdown = MagicMock()
        app._hide_system_check = MagicMock()

        app._update_filter_options()
        values = app._filter_dropdown.configure.call_args.kwargs["values"]
        assert "system" not in values
        assert values == ["all", "stdout", "stderr", "state_update"]

    def test_update_filter_options_resets_system_filter(self, parser):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app.sections = parser.parse()
        app._hide_system = MagicMock()
        app._hide_system.get.return_value = True
        app._filter_var = MagicMock()
        app._filter_var.get.return_value = "system"
        app._filter_dropdown = MagicMock()
        app._hide_system_check = MagicMock()

        app._update_filter_options()
        app._filter_var.set.assert_called_once_with("all")

    def test_insert_node_skips_system_when_hidden(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._hide_system = MagicMock()
        app._hide_system.get.return_value = True
        app._tree = MagicMock()
        app._tree.insert.return_value = "node"
        app._sec_map = {}

        root = sections[0]
        app._insert_node("", root)

        calls = app._tree.insert.call_args_list
        labels = [c.kwargs["text"] for c in calls]
        assert "System" not in labels
        assert "Stdout" in labels
        assert any(label.startswith("Agent: amala") for label in labels)

    def test_insert_node_includes_system_when_not_hidden(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._hide_system = MagicMock()
        app._hide_system.get.return_value = False
        app._tree = MagicMock()
        app._tree.insert.return_value = "node"
        app._sec_map = {}

        root = sections[0]
        app._insert_node("", root)

        calls = app._tree.insert.call_args_list
        labels = [c.kwargs["text"] for c in calls]
        assert "System" in labels

    # -- Point 3: Show entire file -> jump + highlight --

    def test_jump_to_section_scrolls_and_highlights(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._tree = MagicMock()
        app._text = MagicMock()
        app._highlight_after_id = None
        app._sec_map = {}

        agent = self._find(sections, "agent")[0]
        app._sec_map["node"] = agent
        app._tree.selection.return_value = ["node"]

        app._jump_to_section()
        start = f"{agent.start + 1}.0"
        end = f"{max(agent.end, agent.start + 1) + 1}.0"
        app._text.see.assert_called_once_with(start)
        app._text.tag_add.assert_called_once_with("highlight", start, end)
        app._text.after.assert_called_once_with(1500, app._clear_highlight)

    def test_on_select_jumps_when_show_all(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = True
        app._tree = MagicMock()
        app._tree.selection.return_value = ["node"]
        app._text = MagicMock()
        app._highlight_after_id = None
        app._sec_map = {}
        agent = self._find(sections, "agent")[0]
        app._sec_map["node"] = agent
        app._jump_to_section = MagicMock()

        app._on_select(None)
        app._jump_to_section.assert_called_once()

    def test_on_select_multiselect_prunes_child_covered_by_parent(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ["a", "s"]
        app._sec_map = {}
        agent = self._find(sections, "agent")[0]
        system = self._find(sections, "system")[0]
        app._sec_map["a"] = agent
        app._sec_map["s"] = system
        app._display_sections = MagicMock()

        app._on_select(None)
        app._display_sections.assert_called_once_with([agent])

    def test_on_select_multiselect_keeps_unrelated_sections(self, parser):
        from tools.looplog import LoopLogApp
        from tools.looplog import Section

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._sec_map = {}
        # Two sections with non-overlapping line ranges are unrelated.
        alpha = Section("xml", "agent", "alpha", 0, 10)
        beta = Section("xml", "agent", "beta", 20, 30)
        app._sec_map["a"] = alpha
        app._sec_map["b"] = beta
        app._tree.selection.return_value = ["a", "b"]
        app._display_sections = MagicMock()

        app._on_select(None)
        app._display_sections.assert_called_once_with([alpha, beta])

    def test_on_select_multiselect_keeps_sibling_children(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ["o", "s"]
        app._sec_map = {}
        stdout = self._find(sections, "stdout")[0]
        system = self._find(sections, "system")[0]
        app._sec_map["o"] = stdout
        app._sec_map["s"] = system
        app._display_sections = MagicMock()

        app._on_select(None)
        app._display_sections.assert_called_once_with([stdout, system])

    def test_prune_contained_leaves_identical_ranges(self):
        from tools.looplog import Section, _prune_contained

        a = Section("xml", "agent", "a", 0, 10)
        b = Section("xml", "agent", "b", 0, 10)
        assert _prune_contained([a, b]) == [a, b]

    def test_prune_contained_keeps_overlapping_sections(self):
        from tools.looplog import Section, _prune_contained

        a = Section("xml", "agent", "a", 0, 20)
        b = Section("xml", "agent", "b", 5, 25)
        assert _prune_contained([a, b]) == [a, b]

    def test_prune_contained_drops_nested_tail_equal_end(self):
        from tools.looplog import Section, _prune_contained

        parent = Section("xml", "agent", "parent", 0, 30)
        child = Section("xml", "stdout", "child", 10, 30)
        assert _prune_contained([parent, child]) == [parent]

    def test_prune_contained_empty_and_single(self):
        from tools.looplog import Section, _prune_contained

        assert _prune_contained([]) == []
        sec = Section("xml", "agent", "a", 0, 10)
        assert _prune_contained([sec]) == [sec]

    def test_on_select_ignores_unknown_selection(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ["unknown"]
        app._sec_map = {}
        app._display_sections = MagicMock()

        app._on_select(None)
        app._display_sections.assert_not_called()

    # -- Handlers: hide-system, show-all, filter --

    def test_on_hide_system_rebuilds_tree(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._update_filter_options = MagicMock()
        app._rebuild_tree = MagicMock()

        app._on_hide_system()
        app._update_filter_options.assert_called_once()
        app._rebuild_tree.assert_called_once()

    def test_on_show_all_displays_full_text(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = True
        app._set_text = MagicMock()

        app._on_show_all()
        app._set_text.assert_called_once_with(
            parser.get_full_text(strip_markers=False)
        )

    def test_on_show_all_off_redraws_selection(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ["node"]
        app._on_select = MagicMock()

        app._on_show_all()
        app._on_select.assert_called_once_with(None)

    def test_on_show_all_off_selects_first_when_none(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.parser = parser
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ()
        app._tree.get_children.return_value = ["n1"]
        app._on_select = MagicMock()

        app._on_show_all()
        app._tree.selection_set.assert_called_once_with("n1")
        app._on_select.assert_called_once_with(None)

    def test_on_show_all_returns_without_parser(self):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app.parser = None
        app._show_all = MagicMock()
        app._set_text = MagicMock()

        app._on_show_all()
        app._set_text.assert_not_called()

    def test_on_filter_change_disabled_in_show_all(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = True
        app._on_select = MagicMock()

        app._on_filter_change(None)
        app._on_select.assert_not_called()

    def test_on_filter_change_no_sections(self):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app.sections = []
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._on_select = MagicMock()

        app._on_filter_change(None)
        app._on_select.assert_not_called()

    def test_on_filter_change_with_selection(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ["node"]
        app._on_select = MagicMock()

        app._on_filter_change(None)
        app._on_select.assert_called_once_with(None)
        app._tree.selection_set.assert_not_called()

    def test_on_filter_change_selects_first_when_none(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app.sections = sections
        app._show_all = MagicMock()
        app._show_all.get.return_value = False
        app._tree = MagicMock()
        app._tree.selection.return_value = ()
        app._tree.get_children.return_value = ["n1"]
        app._on_select = MagicMock()

        app._on_filter_change(None)
        app._tree.selection_set.assert_called_once_with("n1")
        app._on_select.assert_called_once_with(None)

    def test_load_log_preserves_view_state(self, parser):
        from tools.looplog import LoopLogApp

        sections = parser.parse()
        app = object.__new__(LoopLogApp)
        app._file_label = MagicMock()
        app._hide_system = MagicMock()
        app._show_all = MagicMock()
        app._update_filter_options = MagicMock()
        app._rebuild_tree = MagicMock()

        app.load_log(parser.path)
        app._hide_system.set.assert_not_called()
        app._show_all.set.assert_not_called()
        app._update_filter_options.assert_called_once()
        app._rebuild_tree.assert_called_once()
        assert app.sections is not None

    def test_on_wrap_lines_applies_word_wrap(self):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app._wrap_lines = MagicMock()
        app._wrap_lines.get.return_value = True
        app._text = MagicMock()

        app._on_wrap_lines()
        app._text.config.assert_called_once_with(wrap=tk.WORD)

    def test_on_wrap_lines_applies_no_wrap(self):
        from tools.looplog import LoopLogApp

        app = object.__new__(LoopLogApp)
        app._wrap_lines = MagicMock()
        app._wrap_lines.get.return_value = False
        app._text = MagicMock()

        app._on_wrap_lines()
        app._text.config.assert_called_once_with(wrap=tk.NONE)

    def test_build_ui_initializes_vars_from_start_flags(self):
        from tools import looplog

        checkbox_vars: dict[str, bool] = {}

        class FakeBooleanVar:
            def __init__(self, value=False):
                self._value = value

            def get(self):
                return self._value

        def fake_booleanvar(**kwargs):
            return FakeBooleanVar(kwargs.get("value", False))

        def fake_widget(*args, **kwargs):
            return MagicMock()

        def fake_checkbutton(parent, text, variable, **kwargs):
            checkbox_vars[text] = variable.get()
            return MagicMock()

        with (
            patch.object(looplog.tk, "Tk", return_value=MagicMock()),
            patch.object(looplog.ttk, "Treeview", side_effect=fake_widget),
            patch.object(looplog.tk, "Menu", side_effect=fake_widget),
            patch.object(looplog.ttk, "Frame", side_effect=fake_widget),
            patch.object(looplog.ttk, "Label", side_effect=fake_widget),
            patch.object(looplog.ttk, "Combobox", side_effect=fake_widget),
            patch.object(looplog.ttk, "Button", side_effect=fake_widget),
            patch.object(looplog.ttk, "Checkbutton", side_effect=fake_checkbutton),
            patch.object(looplog.ttk, "PanedWindow", side_effect=fake_widget),
            patch.object(looplog.ttk, "Scrollbar", side_effect=fake_widget),
            patch.object(looplog.tk, "Text", side_effect=fake_widget),
            patch.object(looplog.tk, "BooleanVar", side_effect=fake_booleanvar),
            patch.object(looplog.tk, "StringVar", side_effect=fake_widget),
        ):
            app = object.__new__(looplog.LoopLogApp)
            app.root = looplog.tk.Tk()
            app._start_hide_system = True
            app._start_show_all = True
            app._start_wrap_lines = True
            app._start_filter = "all"
            app._build_ui()

        assert checkbox_vars["Show entire file"] is True
        assert checkbox_vars["Hide system tags"] is True
        assert checkbox_vars["Wrap lines"] is True

    def test_build_ui_uses_wrap_word_when_start_wrap_lines(self):
        from tools import looplog

        text_kwargs = {}

        def fake_text(parent, **kwargs):
            text_kwargs.update(kwargs)
            return MagicMock()

        def fake_widget(*args, **kwargs):
            return MagicMock()

        with (
            patch.object(looplog.tk, "Tk", return_value=MagicMock()),
            patch.object(looplog.ttk, "Treeview", side_effect=fake_widget),
            patch.object(looplog.tk, "Menu", side_effect=fake_widget),
            patch.object(looplog.ttk, "Frame", side_effect=fake_widget),
            patch.object(looplog.ttk, "Label", side_effect=fake_widget),
            patch.object(looplog.ttk, "Combobox", side_effect=fake_widget),
            patch.object(looplog.ttk, "Button", side_effect=fake_widget),
            patch.object(looplog.ttk, "Checkbutton", side_effect=fake_widget),
            patch.object(looplog.ttk, "PanedWindow", side_effect=fake_widget),
            patch.object(looplog.ttk, "Scrollbar", side_effect=fake_widget),
            patch.object(looplog.tk, "Text", side_effect=fake_text),
            patch.object(looplog.tk, "BooleanVar", side_effect=fake_widget),
            patch.object(looplog.tk, "StringVar", side_effect=fake_widget),
        ):
            app = object.__new__(looplog.LoopLogApp)
            app.root = looplog.tk.Tk()
            app._start_hide_system = False
            app._start_show_all = False
            app._start_wrap_lines = True
            app._start_filter = "all"
            app._build_ui()

        assert text_kwargs.get("wrap") == tk.WORD

    def test_build_ui_uses_wrap_none_when_not_start_wrap_lines(self):
        from tools import looplog

        text_kwargs = {}

        def fake_text(parent, **kwargs):
            text_kwargs.update(kwargs)
            return MagicMock()

        def fake_widget(*args, **kwargs):
            return MagicMock()

        with (
            patch.object(looplog.tk, "Tk", return_value=MagicMock()),
            patch.object(looplog.ttk, "Treeview", side_effect=fake_widget),
            patch.object(looplog.tk, "Menu", side_effect=fake_widget),
            patch.object(looplog.ttk, "Frame", side_effect=fake_widget),
            patch.object(looplog.ttk, "Label", side_effect=fake_widget),
            patch.object(looplog.ttk, "Combobox", side_effect=fake_widget),
            patch.object(looplog.ttk, "Button", side_effect=fake_widget),
            patch.object(looplog.ttk, "Checkbutton", side_effect=fake_widget),
            patch.object(looplog.ttk, "PanedWindow", side_effect=fake_widget),
            patch.object(looplog.ttk, "Scrollbar", side_effect=fake_widget),
            patch.object(looplog.tk, "Text", side_effect=fake_text),
            patch.object(looplog.tk, "BooleanVar", side_effect=fake_widget),
            patch.object(looplog.tk, "StringVar", side_effect=fake_widget),
        ):
            app = object.__new__(looplog.LoopLogApp)
            app.root = looplog.tk.Tk()
            app._start_hide_system = False
            app._start_show_all = False
            app._start_wrap_lines = False
            app._start_filter = "all"
            app._build_ui()

        assert text_kwargs.get("wrap") == tk.NONE

    def test_build_ui_initializes_filter_var_from_start_filter(self):
        from tools import looplog

        captured = {}

        class FakeStringVar:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        def fake_stringvar(**kwargs):
            return FakeStringVar(**kwargs)

        def fake_widget(*args, **kwargs):
            return MagicMock()

        with (
            patch.object(looplog.tk, "Tk", return_value=MagicMock()),
            patch.object(looplog.ttk, "Treeview", side_effect=fake_widget),
            patch.object(looplog.tk, "Menu", side_effect=fake_widget),
            patch.object(looplog.ttk, "Frame", side_effect=fake_widget),
            patch.object(looplog.ttk, "Label", side_effect=fake_widget),
            patch.object(looplog.ttk, "Combobox", side_effect=fake_widget),
            patch.object(looplog.ttk, "Button", side_effect=fake_widget),
            patch.object(looplog.ttk, "Checkbutton", side_effect=fake_widget),
            patch.object(looplog.ttk, "PanedWindow", side_effect=fake_widget),
            patch.object(looplog.ttk, "Scrollbar", side_effect=fake_widget),
            patch.object(looplog.tk, "Text", side_effect=fake_widget),
            patch.object(looplog.tk, "BooleanVar", side_effect=fake_widget),
            patch.object(looplog.tk, "StringVar", side_effect=fake_stringvar),
        ):
            app = object.__new__(looplog.LoopLogApp)
            app.root = looplog.tk.Tk()
            app._start_hide_system = False
            app._start_show_all = False
            app._start_wrap_lines = False
            app._start_filter = "system"
            app._build_ui()

        assert captured.get("value") == "system"

    def test_main_with_file_passes_flags_to_app(self, tmp_path, monkeypatch):
        from tools import looplog

        log = tmp_path / "test.log"
        log.write_text("<openloop_log>\n</openloop_log>\n", encoding="utf-8")

        captured = {}
        app_mock = MagicMock()

        def fake_app(path, hide_system=False, show_all=False,
                     wrap_lines=False, filter_tag="all"):
            captured["path"] = path
            captured["hide_system"] = hide_system
            captured["show_all"] = show_all
            captured["wrap_lines"] = wrap_lines
            captured["filter_tag"] = filter_tag
            return app_mock

        monkeypatch.setattr(looplog, "LoopLogApp", fake_app)
        looplog.main(
            [str(log), "--hide-system-tags", "--show-entire-file",
             "--wrap-lines", "--filter", "stdout"]
        )

        assert captured["path"] == log
        assert captured["hide_system"] is True
        assert captured["show_all"] is True
        assert captured["wrap_lines"] is True
        assert captured["filter_tag"] == "stdout"
        app_mock.run.assert_called_once()

    def test_main_without_flags_passes_false(self, tmp_path, monkeypatch):
        from tools import looplog

        log = tmp_path / "test.log"
        log.write_text("<openloop_log>\n</openloop_log>\n", encoding="utf-8")

        captured = {}
        app_mock = MagicMock()

        def fake_app(path, hide_system=False, show_all=False,
                     wrap_lines=False, filter_tag="all"):
            captured["hide_system"] = hide_system
            captured["show_all"] = show_all
            captured["wrap_lines"] = wrap_lines
            captured["filter_tag"] = filter_tag
            return app_mock

        monkeypatch.setattr(looplog, "LoopLogApp", fake_app)
        looplog.main([str(log)])

        assert captured["hide_system"] is False
        assert captured["show_all"] is False
        assert captured["wrap_lines"] is False
        assert captured["filter_tag"] == "all"
        app_mock.run.assert_called_once()

    def test_main_missing_file_exits(self, tmp_path, monkeypatch):
        from tools import looplog

        monkeypatch.setattr(looplog, "LoopLogApp", MagicMock())
        with pytest.raises(SystemExit) as exc:
            looplog.main([str(tmp_path / "missing.log")])
        assert exc.value.code == 1

    def test_main_invalid_filter_exits(self, tmp_path, monkeypatch):
        from tools import looplog

        log = tmp_path / "test.log"
        log.write_text("<openloop_log>\n</openloop_log>\n", encoding="utf-8")

        monkeypatch.setattr(looplog, "LoopLogApp", MagicMock())
        with pytest.raises(SystemExit) as exc:
            looplog.main([str(log), "--filter", "bogus"])
        assert exc.value.code == 2

    def test_main_no_file_uses_none(self, monkeypatch):
        from tools import looplog

        captured = {}
        app_mock = MagicMock()

        def fake_app(path, hide_system=False, show_all=False,
                     wrap_lines=False, filter_tag="all"):
            captured["path"] = path
            captured["wrap_lines"] = wrap_lines
            captured["filter_tag"] = filter_tag
            return app_mock

        monkeypatch.setattr(looplog, "LoopLogApp", fake_app)
        looplog.main([])

        assert captured["path"] is None
        assert captured["wrap_lines"] is False
        assert captured["filter_tag"] == "all"
        app_mock.run.assert_called_once()
