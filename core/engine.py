import json
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from core.agent import AgentDefinition, AgentLoader
from core.config import Config, strip_jsonc
from core.parser import StateParser
from core.runner import OpenCodeOptions, OpenCodeRunner, RunResult
from core.state import WorkflowState


MissingStateHandler = Callable[[str, Optional[Path]], bool]


@dataclass
class WorkflowConfig:
    preparation_agents: list[str] = field(default_factory=list)
    loop_agents: list[str] = field(default_factory=list)
    finalization_agents: list[str] = field(default_factory=list)
    end_state_condition: str = "is_complete == True"
    max_loops: int = 10
    finalize_on_abort: bool = False
    workdir: Optional[str] = None
    init_script: Optional[str] = None
    opencode_defaults: OpenCodeOptions = field(default_factory=OpenCodeOptions)

    @staticmethod
    def _clean_agent_list(value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            return []

        cleaned: list[str] = []

        for item in value:
            if item is None:
                continue

            s = str(item).strip()
            if s:
                cleaned.append(s)

        return cleaned

    @staticmethod
    def _clean_optional_str(value: object) -> Optional[str]:
        if value is None:
            return None

        s = str(value).strip()
        return s or None

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowConfig":
        p = Path(path)

        try:
            raw = json.loads(strip_jsonc(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise FileNotFoundError(f"Workflow not found: {p}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid workflow JSON: {exc}")

        if not isinstance(raw, dict):
            raise ValueError("Workflow file must contain a JSON object")

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowConfig":
        # Robust against accidental trailing spaces in keys.
        data = {
            str(k).strip(): v
            for k, v in data.items()
            if isinstance(k, str)
        }

        prep = data.get("preparation_agents")
        if prep is None:
            prep = data.get("preparation_agent")

        final = data.get("finalization_agents")
        if final is None:
            final = data.get("finalization_agent")

        opencode_opts = OpenCodeOptions()
        if "opencode_defaults" in data and isinstance(data["opencode_defaults"], dict):
            opencode_opts = OpenCodeOptions.from_dict(data["opencode_defaults"])

        end_state_condition = cls._clean_optional_str(
            data.get("end_state_condition")
        ) or cls.end_state_condition

        try:
            max_loops = int(data.get("max_loops", cls.max_loops))
        except (TypeError, ValueError):
            max_loops = cls.max_loops

        return cls(
            preparation_agents=cls._clean_agent_list(prep),
            loop_agents=cls._clean_agent_list(data.get("loop_agents")),
            finalization_agents=cls._clean_agent_list(final),
            end_state_condition=end_state_condition,
            max_loops=max_loops,
            finalize_on_abort=bool(
                data.get("finalize_on_abort", cls.finalize_on_abort)
            ),
            workdir=cls._clean_optional_str(data.get("workdir")),
            init_script=cls._clean_optional_str(data.get("init_script")),
            opencode_defaults=opencode_opts,
        )

    def to_dict(self) -> dict:
        d: dict = {
            "preparation_agents": self.preparation_agents,
            "loop_agents": self.loop_agents,
            "finalization_agents": self.finalization_agents,
            "end_state_condition": self.end_state_condition,
            "max_loops": self.max_loops,
            "finalize_on_abort": self.finalize_on_abort,
            "workdir": self.workdir,
            "init_script": self.init_script,
        }

        opts_dict = self.opencode_defaults.to_dict()
        if opts_dict:
            d["opencode_defaults"] = opts_dict

        return d


LogCallback = Callable[[str], None]


class ExecutionEngine:
    MAX_CORRECTIONS = 2

    PROTECTED_STATE_KEYS = {
        "current_phase",
        "iteration",
        "meta",
    }

    KNOWN_STATE_KEYS = {
        "is_complete",
        "termination_reason",
        "payload",
    }

    # Fallback for older agent definitions without explicit can_complete field.
    # For explicit control, add can_complete: true/false to agent frontmatter.
    COMPLETION_ROLES_FALLBACK = {
        "auditor",
        "approver",
        "finalizer",
        "finalization",
    }

    def __init__(
        self,
        config: Optional[Config] = None,
        logger: Optional[LogCallback] = None,
        stop_event: Optional[threading.Event] = None,
        no_log_file: bool = False,
        log_file: Optional[str] = None,
        timeout: Optional[int] = None,
        missing_state_handler: Optional[MissingStateHandler] = None,
    ):
        self.config = config or Config()
        self.logger = logger or (lambda msg: print(f"[OpenLoop] {msg}"))
        self.state = WorkflowState()
        self.agent_loader = AgentLoader(self.config.agents_dir)

        self._timeout = timeout if timeout is not None else self.config.default_timeout
        self.runner = OpenCodeRunner(
            binary=self.config.opencode_binary,
            timeout=self._timeout,
        )

        self._stop_event = stop_event or threading.Event()

        self._log_handle = None
        self._log_path: Optional[Path] = None
        self._log_dir: Optional[Path] = None
        self._no_log_file = no_log_file
        self._log_file_arg = log_file

        self._workdir: Optional[str] = None
        self._init_script: Optional[str] = None
        self._opencode_opts = OpenCodeOptions()

        self._missing_state_handler = missing_state_handler

    # ---- File logging ----

    def _init_log(self, workdir: Optional[str] = None) -> None:
        if self._no_log_file:
            return

        if self._log_file_arg:
            self._log_path = Path(self._log_file_arg)
            self._log_dir = self._log_path.parent
        else:
            log_dir = Path(self.config.log_dir)
            if not log_dir.is_absolute() and workdir:
                log_dir = Path(workdir) / log_dir

            self._log_dir = log_dir

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self._log_path = log_dir / f"openloop-run-{ts}.log"

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("w", encoding="utf-8")

        self._write_log(
            f"OpenLoop run started at "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )

    def _close_log(self) -> None:
        if self._log_handle:
            self._write_log(
                f"\nOpenLoop run finished at "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            self._log_handle.close()
            self._log_handle = None

    def _write_log(self, text: str) -> None:
        if self._log_handle:
            self._log_handle.write(text)
            self._log_handle.flush()

    def _write_banner(self, agent_name: str) -> None:
        run_id = self._get_run_id()

        self._write_log(
            f"{'=' * 70}\n"
            f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"Agent: {agent_name} | Phase: {self.state.current_phase} | "
            f"Iteration: {self.state.iteration} | Run ID: {run_id}\n"
            f"{'=' * 70}\n\n"
        )

    # ---- Run metadata ----

    def _init_run_meta(self) -> None:
        now = datetime.now(timezone.utc)

        run_id = (
            f"{now.strftime('%Y%m%d-%H%M%SZ')}"
            f"-{uuid.uuid4().hex[:6]}"
        )

        meta = {
            "run_id": run_id,
            "started_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

        # Preferred: dedicated top-level meta block, if state.py supports it.
        if hasattr(self.state, "meta"):
            self.state.meta = meta
        else:
            # Fallback for older state definitions without explicit meta field.
            self.state.payload.setdefault("_openloop", {}).update(meta)

    def _get_run_id(self) -> str:
        meta = getattr(self.state, "meta", None)
        if isinstance(meta, dict):
            run_id = meta.get("run_id")
            if run_id:
                return str(run_id)

        openloop_meta = self.state.payload.get("_openloop")
        if isinstance(openloop_meta, dict):
            return str(openloop_meta.get("run_id", ""))

        return ""

    # ---- Missing state policy ----

    def _default_missing_state_handler(
        self,
        agent_name: str,
        log_path: Optional[Path],
    ) -> bool:
        self.log(
            f"  WARNING: Agent '{agent_name}' did not provide "
            f"a valid state update."
        )

        if log_path:
            self.log(f"  Inspect agent output in: {log_path}")

        try:
            if sys.stdin and sys.stdin.isatty():
                answer = input(
                    "Continue workflow anyway at your own risk? [y/N] "
                ).strip().lower()
                return answer in {"y", "yes"}
        except Exception as exc:
            self.log(f"  WARNING: Interactive prompt failed: {exc}")

        self.log(
            "  Non-interactive session — aborting due to missing state update."
        )
        return False

    def _handle_missing_state(self, agent_name: str) -> bool:
        handler = self._missing_state_handler or self._default_missing_state_handler

        try:
            return bool(handler(agent_name, self._log_path))
        except Exception as exc:
            self.log(f"  WARNING: missing-state handler failed: {exc}")
            return False

    # ---- State normalization and authorization ----

    def _agent_may_complete(self, agent: AgentDefinition) -> bool:
        can_complete = getattr(agent, "can_complete", None)

        if isinstance(can_complete, bool):
            return can_complete

        role = str(getattr(agent, "role", "")).strip().lower()
        return role in self.COMPLETION_ROLES_FALLBACK

    def _normalize_state_update(
        self,
        state_data: dict,
        agent: AgentDefinition,
    ) -> dict:
        normalized: dict = {}
        moved: dict = {}

        ignored_protected = [
            k for k in state_data
            if k in self.PROTECTED_STATE_KEYS
        ]

        if ignored_protected:
            self.log(
                f"  NOTE: Ignoring protected key(s) from agent state: "
                f"{ignored_protected}"
            )

        for key, value in state_data.items():
            if key in self.PROTECTED_STATE_KEYS:
                continue

            if key in self.KNOWN_STATE_KEYS:
                normalized[key] = value
            else:
                moved[key] = value

        if moved:
            payload = normalized.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            for k, v in moved.items():
                payload.setdefault(k, v)

            normalized["payload"] = payload

            self.log(
                f"  NOTE: Moved non-top-level key(s) into payload: "
                f"{sorted(moved.keys())}"
            )

        # Protect fallback meta location as well.
        payload = normalized.get("payload")
        if isinstance(payload, dict):
            payload.pop("_openloop", None)

        if (
            not self._agent_may_complete(agent)
            and normalized.get("is_complete") is True
        ):
            self.log(
                f"  WARNING: Agent '{agent.name}' is not allowed to set "
                f"is_complete=true. Forcing false."
            )

            normalized["is_complete"] = False

            payload = normalized.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            payload.setdefault("completion_blocked", True)
            payload.setdefault(
                "completion_blocked_reason",
                f"{agent.name} is not authorized to complete the workflow",
            )

            normalized["payload"] = payload

        return normalized

    # ---- Workflow execution ----

    def execute_workflow(self, workflow_path: str | Path) -> WorkflowState:
        workflow = WorkflowConfig.load(workflow_path)
        return self.execute_workflow_data(workflow.to_dict())

    def execute_workflow_data(self, data: dict) -> WorkflowState:
        clean_data = {
            str(k).strip(): v
            for k, v in data.items()
            if isinstance(k, str)
        }

        workflow = WorkflowConfig.from_dict(clean_data)

        self.state = WorkflowState()
        self._init_run_meta()

        self._workdir = workflow.workdir or self.config.workdir
        self._init_log(self._workdir)

        self.log(f"Loaded workflow: {workflow.loop_agents}")
        self.log(f"Run ID: {self._get_run_id()}")

        raw_init = workflow.init_script or self.config.init_script
        if raw_init:
            p = Path(raw_init)
            if not p.is_absolute() and (Path.cwd() / p).is_file():
                self._init_script = str((Path.cwd() / raw_init).resolve())
            else:
                self._init_script = raw_init
        else:
            self._init_script = None

        if "max_loops" not in clean_data:
            workflow.max_loops = self.config.default_max_loops

        self._opencode_opts = self.config.opencode_defaults.merge(
            workflow.opencode_defaults
        )

        try:
            if not self._run_preparation(workflow):
                return self.state

            if self._evaluate_end_condition(workflow.end_state_condition):
                if not self.state.termination_reason:
                    self.state.termination_reason = "completed"
            else:
                if not self._run_loop(workflow):
                    return self.state

            if not self._run_finalization(workflow):
                return self.state

            return self.state
        finally:
            self._close_log()

    def _run_preparation(self, workflow: WorkflowConfig) -> bool:
        if not workflow.preparation_agents:
            self.log("No preparation agent defined — skipping")
            return True

        self.state.current_phase = "preparation"

        for agent_name in workflow.preparation_agents:
            self.log(f"Preparation phase: {agent_name}")

            if not self._execute_agent(agent_name):
                return False

            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self.log("Execution stopped by user")
                return False

        return True

    def _run_loop(self, workflow: WorkflowConfig) -> bool:
        if not workflow.loop_agents:
            self.log("No loop agents defined — skipping")
            self.state.is_complete = True
            self.state.termination_reason = "completed"
            return True

        self.state.current_phase = "loop"

        while self.state.iteration < workflow.max_loops:
            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self.log("Execution stopped by user")
                return False

            self.state.iteration += 1
            self.log(
                f"Loop iteration {self.state.iteration}/{workflow.max_loops}"
            )

            for agent_name in workflow.loop_agents:
                self.log(f"  Running agent: {agent_name}")

                if not self._execute_agent(agent_name):
                    return False

                if self._stop_event.is_set():
                    self.state.termination_reason = "stopped"
                    self.log("Execution stopped by user")
                    return False

                if self._evaluate_end_condition(workflow.end_state_condition):
                    self.log(
                        f"  Termination condition met "
                        f"(iteration {self.state.iteration})"
                    )
                    self.state.termination_reason = "completed"
                    return True

        self.state.termination_reason = "max_loops_reached"
        self.log(
            f"Max loops ({workflow.max_loops}) reached — terminating loop"
        )
        return True

    def _run_finalization(self, workflow: WorkflowConfig) -> bool:
        if not workflow.finalization_agents:
            self.log("No finalization agent defined — skipping")
            return True

        should_finalize = (
            self.state.termination_reason == "completed"
            or (
                self.state.termination_reason == "max_loops_reached"
                and workflow.finalize_on_abort
            )
        )

        if not should_finalize:
            self.log("Finalization skipped (configured to run on completion only)")
            return True

        self.state.current_phase = "finalization"

        for agent_name in workflow.finalization_agents:
            self.log(f"Finalization phase: {agent_name}")

            if not self._execute_agent(agent_name):
                return False

            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self.log("Execution stopped by user")
                return False

        return True

    def _execute_agent(self, agent_name: str) -> bool:
        agent = self.agent_loader.get_agent(agent_name)

        self._write_banner(agent_name)

        base_prompt = self._build_prompt(agent)
        prompt = base_prompt

        state_data = None

        for attempt in range(1 + self.MAX_CORRECTIONS):
            prompt_file = None
            if self._log_dir is not None:
                prompt_file = self._log_dir / self.runner.PROMPT_FILENAME
            else:
                log_dir = Path(self.config.log_dir)
                if not log_dir.is_absolute() and self._workdir:
                    log_dir = Path(self._workdir) / log_dir
                prompt_file = log_dir / self.runner.PROMPT_FILENAME

            result = self.runner.run(
                prompt,
                opts=self._opencode_opts,
                cwd=self._workdir,
                init_script=self._init_script,
                continue_session=(attempt > 0),
                prompt_file=prompt_file,
            )

            if result.output:
                self._write_log(f"[stdout]\n{result.output}\n\n")

            if result.error:
                self._write_log(f"[stderr]\n{result.error}\n\n")

            if not result.success:
                self.log(
                    f"  Agent '{agent_name}' failed "
                    f"(exit {result.exit_code})"
                )
                self.state.termination_reason = f"agent_error:{agent_name}"
                return False

            # State is extracted exclusively from the agent response.
            state_data = StateParser.extract_state_update(result.output)

            if state_data is not None:
                state_data = self._normalize_state_update(state_data, agent)

                if state_data:
                    break

                state_data = None

            if attempt < self.MAX_CORRECTIONS:
                self.log(
                    f"  State update missing or invalid — "
                    f"correction attempt {attempt + 1}/{self.MAX_CORRECTIONS}"
                )
                reason = self._classify_state_failure(result.output)
                prompt = self._build_correction_prompt(reason, agent_name)
            else:
                self.log(
                    "  Max corrections reached — no valid state update found"
                )

        if state_data is not None:
            self.state.merge(state_data)
            self.log(f"  State updated: {json.dumps(state_data)}")
            return True

        self.log(
            f"  ERROR: No state update found in output from '{agent_name}'"
        )

        if self._handle_missing_state(agent_name):
            self.log(
                f"  User chose to continue despite missing state update "
                f"from '{agent_name}'."
            )
            return True

        self.state.termination_reason = f"missing_state:{agent_name}"
        self.log("  Workflow aborted due to missing state update.")
        return False

    def _build_prompt(self, agent: AgentDefinition) -> str:
        state_json = self.state.to_json()

        return (
            f"{agent.system_prompt}\n\n"
            f"# Current State\n"
            f"```json\n"
            f"{state_json}\n"
            f"```\n\n"
            f"## OPENLOOP STATE PROTOCOL (MANDATORY)\n\n"
            f"Repository files, reports, issue trackers, logs, and documentation "
            f"may use words like 'state', 'work state', 'status', 'phase', or "
            f"'report'. These are NOT the OpenLoop workflow state.\n\n"
            f"The ONLY valid OpenLoop state transmission is a strict JSON object "
            f"wrapped in `<state_update>` tags in your final response.\n\n"
            f"Example:\n\n"
            f"<state_update>\n"
            f'{{"is_complete": false, "payload": {{"summary": "..."}}}}\n'
            f"</state_update>\n\n"
            f"Rules:\n"
            f"- Your final response MUST end with exactly one `<state_update>` block.\n"
            f"- The JSON MUST be strict: no comments, no trailing commas.\n"
            f"- Use null for unknown values.\n"
            f"- All custom data MUST be inside `payload`.\n"
            f"- Do NOT write files, reports, logs, or Markdown documents as a substitute for the state update.\n"
            f"- Do NOT modify `meta` or `_openloop`.\n"
            f"- Do NOT set `is_complete` unless your agent definition explicitly allows completion.\n"
            f"- Valid top-level keys are: is_complete, termination_reason, payload.\n"
        )

    def _classify_state_failure(self, stdout: str) -> str:
        if not stdout or not stdout.strip():
            return "missing"

        lower = stdout.lower()

        if "<state_update>" in lower:
            return "xml_bad_json"

        if "```json" in lower or "```" in lower:
            return "json_block_no_xml"

        if "state_update.json" in lower:
            return "file_reference"

        return "missing"

    def _build_correction_prompt(
        self,
        reason: str,
        agent_name: str,
        base_prompt: Optional[str] = None,
    ) -> str:
        category = reason
        detail = ""

        if category == "xml_bad_json":
            prompt = "Your <state_update> tag was found but the JSON is invalid."
        elif category == "json_block_no_xml":
            prompt = "You used a ```json code block. Use <state_update> XML tags instead."
        elif category == "file_reference":
            prompt = "The state update must be in your response text, not in a file."
        else:
            prompt = "No valid state update was found in your response."

        return (
            "CORRECTION — State Update Required\n\n"
            f"{prompt}\n\n"
            "Output exactly one <state_update> block with valid JSON, and nothing else:\n\n"
            "<state_update>\n"
            '{"is_complete": false, "payload": {"summary": "..."}}\n'
            "</state_update>"
        )

    def _evaluate_end_condition(self, condition: str) -> bool:
        if condition == "is_complete == True":
            return bool(self.state.is_complete)

        meta = getattr(self.state, "meta", None)
        if not isinstance(meta, dict):
            meta = self.state.payload.get("_openloop", {})

        ns = {
            "is_complete": self.state.is_complete,
            "iteration": self.state.iteration,
            "termination_reason": self.state.termination_reason,
            "phase": self.state.current_phase,
            "payload": self.state.payload,
            "meta": meta,
        }

        try:
            return bool(eval(condition, {"__builtins__": {}}, ns))
        except Exception as exc:
            self.log(
                f"  WARNING: end_state_condition evaluation failed: {exc}"
            )
            return False

    def log(self, message: str) -> None:
        self.logger(message)
        self._write_log(f"[OpenLoop] {message}\n")