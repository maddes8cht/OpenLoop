"""Comprehensive pytest suite for every function in the OpenLoop codebase."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest


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
            assert cmd == ["my-opencode", "run", "some prompt"]


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
            }
        )
        assert wc.preparation_agents == ["prep"]
        assert wc.loop_agents == ["a", "b"]
        assert wc.finalization_agents == ["fin"]
        assert wc.end_state_condition == "payload.get('x') > 5"
        assert wc.max_loops == 20
        assert wc.finalize_on_abort is True

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
        assert "opencode_defaults" not in d

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
        assert "[OpenLoop] hello" in logs

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
        assert state.termination_reason == ""

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

        def slow_run(prompt, opts=None, timeout=None, cwd=None, init_script=None):
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

        def capture_run(prompt, timeout=None):
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
        assert "[OpenLoop] test message" in logs

    def test_opencode_defaults_merged_and_passed_to_runner(self, tmp_path):
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        agents = tmp_path / "agents"
        agents.mkdir()
        (agents / "a.md").write_text("---\nname: a\nrole: test\n---\nYou are A.")

        engine = ExecutionEngine()
        calls = []

        class TrackingRunner:
            def run(self, prompt, opts=None, timeout=None, cwd=None, init_script=None):
                calls.append(opts)
                return type("R", (), {"success": True, "output": '<state_update>{"is_complete": true}</state_update>', "error": "", "exit_code": 0})()

        engine.runner = TrackingRunner()
        engine.agent_loader = type("L", (), {"get_agent": lambda self, name: type("A", (), {"name": name, "role": "", "system_prompt": "You are A."})()})()

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

        assert len(calls) == 1
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
            def run(self, prompt, opts=None, timeout=None, cwd=None, init_script=None):
                calls.append(opts)
                return type("R", (), {"success": True, "output": '<state_update>{"is_complete": true}</state_update>', "error": "", "exit_code": 0})()

        engine.runner = TrackingRunner()
        engine.agent_loader = type("L", (), {"get_agent": lambda self, name: type("A", (), {"name": name, "role": "", "system_prompt": "You are A."})()})()

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

        assert len(calls) == 1
        passed_opts = calls[0]
        assert passed_opts.model == "claude"
        assert passed_opts.agent == "build"

    # -- helpers --

    @staticmethod
    def _make_mock_runner(responses: list | None = None):
        responses = responses or [
            {"success": True, "output": '<state_update>{"is_complete": false}</state_update>'}
        ]
        idx = [0]

        def side_effect(prompt, opts=None, timeout=None, cwd=None, init_script=None):
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
            return type("A", (), {"name": name, "role": "", "system_prompt": prompt})()

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
        app._oc_model_var = MagicMock()
        app._oc_model_var.get.return_value = ""
        app._oc_agent_var = MagicMock()
        app._oc_agent_var.get.return_value = ""
        app._oc_variant_var = MagicMock()
        app._oc_variant_var.get.return_value = ""
        app._oc_pure_var = MagicMock()
        app._oc_pure_var.get.return_value = False

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

            app._load_workflow_into_ui(
                {
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
