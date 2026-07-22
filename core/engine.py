import json
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
        prep = data.get("preparation_agents")
        if prep is None:
            prep = data.get("preparation_agent")

        if isinstance(prep, str):
            prep = [prep]
        elif not isinstance(prep, list):
            prep = []

        final = data.get("finalization_agents")
        if final is None:
            final = data.get("finalization_agent")

        if isinstance(final, str):
            final = [final]
        elif not isinstance(final, list):
            final = []

        opencode_opts = OpenCodeOptions()
        if "opencode_defaults" in data and isinstance(data["opencode_defaults"], dict):
            opencode_opts = OpenCodeOptions.from_dict(data["opencode_defaults"])

        return cls(
            preparation_agents=prep,
            loop_agents=list(data.get("loop_agents", [])),
            finalization_agents=final,
            end_state_condition=str(
                data.get("end_state_condition", cls.end_state_condition)
            ),
            max_loops=int(data.get("max_loops", cls.max_loops)),
            finalize_on_abort=bool(
                data.get("finalize_on_abort", cls.finalize_on_abort)
            ),
            workdir=data.get("workdir"),
            init_script=data.get("init_script"),
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

    def __init__(
        self,
        config: Optional[Config] = None,
        logger: Optional[LogCallback] = None,
        stop_event: Optional[threading.Event] = None,
        no_log_file: bool = False,
        log_file: Optional[str] = None,
        timeout: Optional[int] = None,
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
        self._no_log_file = no_log_file
        self._log_file_arg = log_file

        self._workdir: Optional[str] = None
        self._init_script: Optional[str] = None
        self._opencode_opts = OpenCodeOptions()

    # ---- File logging ----

    def _init_log(self, workdir: Optional[str] = None) -> None:
        if self._no_log_file:
            return

        if self._log_file_arg:
            self._log_path = Path(self._log_file_arg)
        else:
            log_dir = Path(self.config.log_dir)
            if not log_dir.is_absolute() and workdir:
                log_dir = Path(workdir) / log_dir

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
            return str(meta.get("run_id", ""))

        openloop_meta = self.state.payload.get("_openloop")
        if isinstance(openloop_meta, dict):
            return str(openloop_meta.get("run_id", ""))

        return ""

    # ---- Workflow execution ----

    def execute_workflow(self, workflow_path: str | Path) -> WorkflowState:
        workflow = WorkflowConfig.load(workflow_path)
        return self.execute_workflow_data(workflow.to_dict())

    def execute_workflow_data(self, data: dict) -> WorkflowState:
        workflow = WorkflowConfig.from_dict(data)

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

        if "max_loops" not in data:
            workflow.max_loops = self.config.default_max_loops

        self._opencode_opts = self.config.opencode_defaults.merge(
            workflow.opencode_defaults
        )

        try:
            self._run_preparation(workflow)

            if (
                self.state.termination_reason
                and self.state.termination_reason.startswith("agent_error:")
            ):
                return self.state

            self._run_loop(workflow)

            if (
                self.state.termination_reason
                and self.state.termination_reason.startswith("agent_error:")
            ):
                return self.state

            self._run_finalization(workflow)

            return self.state
        finally:
            self._close_log()

    def _run_preparation(self, workflow: WorkflowConfig) -> None:
        if not workflow.preparation_agents:
            self.log("No preparation agent defined — skipping")
            return

        self.state.current_phase = "preparation"

        for agent_name in workflow.preparation_agents:
            self.log(f"Preparation phase: {agent_name}")
            self._execute_agent(agent_name)

            if (
                self.state.termination_reason
                and self.state.termination_reason.startswith("agent_error:")
            ):
                return

    def _run_loop(self, workflow: WorkflowConfig) -> None:
        if not workflow.loop_agents:
            self.log("No loop agents defined — skipping")
            self.state.is_complete = True
            return

        self.state.current_phase = "loop"

        if (
            self.state.termination_reason
            and self.state.termination_reason.startswith("agent_error:")
        ):
            return

        while self.state.iteration < workflow.max_loops:
            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self.log("Execution stopped by user")
                return

            self.state.iteration += 1
            self.log(
                f"Loop iteration {self.state.iteration}/{workflow.max_loops}"
            )

            for agent_name in workflow.loop_agents:
                self.log(f"  Running agent: {agent_name}")
                self._execute_agent(agent_name)

                if (
                    self.state.termination_reason
                    and self.state.termination_reason.startswith("agent_error:")
                ):
                    return

                if self._evaluate_end_condition(workflow.end_state_condition):
                    self.log(
                        f"  Termination condition met "
                        f"(iteration {self.state.iteration})"
                    )
                    self.state.termination_reason = "completed"
                    return

        self.state.termination_reason = "max_loops_reached"
        self.log(
            f"Max loops ({workflow.max_loops}) reached — terminating loop"
        )

    def _run_finalization(self, workflow: WorkflowConfig) -> None:
        if not workflow.finalization_agents:
            self.log("No finalization agent defined — skipping")
            return

        should_finalize = (
            self.state.termination_reason == "completed"
            or (
                self.state.termination_reason == "max_loops_reached"
                and workflow.finalize_on_abort
            )
        )

        if not should_finalize:
            self.log("Finalization skipped (configured to run on completion only)")
            return

        self.state.current_phase = "finalization"

        for agent_name in workflow.finalization_agents:
            self.log(f"Finalization phase: {agent_name}")
            self._execute_agent(agent_name)

    def _execute_agent(self, agent_name: str) -> None:
        agent = self.agent_loader.get_agent(agent_name)

        self._write_banner(agent_name)

        base_prompt = self._build_prompt(agent)
        prompt = base_prompt

        state_data = None

        for attempt in range(1 + self.MAX_CORRECTIONS):
            result = self.runner.run(
                prompt,
                opts=self._opencode_opts,
                cwd=self._workdir,
                init_script=self._init_script,
                continue_session=(attempt > 0),
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
                return

            # State is now extracted exclusively from the agent response.
            state_data = StateParser.extract_state_update(result.output)

            if state_data is not None:
                break

            if attempt < self.MAX_CORRECTIONS:
                self.log(
                    f"  State update missing or invalid — "
                    f"correction attempt {attempt + 1}/{self.MAX_CORRECTIONS}"
                )
                prompt = self._build_correction_prompt(
                    "No valid <state_update> block was found in the agent output.",
                    agent_name,
                )
            else:
                self.log(
                    "  Max corrections reached — no valid state update found"
                )

        if state_data is not None:
            if "meta" in state_data:
                self.log(
                    "  NOTE: 'meta' is read-only; "
                    "agent-provided meta was ignored."
                )

            KNOWN_KEYS = {
                "current_phase",
                "iteration",
                "is_complete",
                "termination_reason",
                "payload",
                "meta",
            }

            unknown = [k for k in state_data if k not in KNOWN_KEYS]
            if unknown:
                self.log(
                    f"  WARNING: Unknown top-level key(s) in state update from "
                    f"'{agent_name}': {unknown}. "
                    f"These will be ignored. Put custom data inside 'payload' instead."
                )

            self.state.merge(state_data)
            self.log(f"  State updated: {json.dumps(state_data)}")
        else:
            self.log(
                f"  ERROR: No state update found in output from '{agent_name}'"
            )
            self.state.termination_reason = f"no_state:{agent_name}"
            return

    def _build_prompt(self, agent: AgentDefinition) -> str:
        state_json = self.state.to_json()

        return (
            f"{agent.system_prompt}\n\n"
            f"# Current State\n"
            f"```json\n"
            f"{state_json}\n"
            f"```\n\n"
            f"## State Update (MANDATORY)\n\n"
            f"At the very end of your final response, output exactly one valid "
            f"JSON object wrapped in `<state_update>` tags.\n\n"
            f"Example:\n\n"
            f"<state_update>\n"
            f'{{"is_complete": false, "payload": {{"summary": "..."}}}}\n'
            f"</state_update>\n\n"
            f"Rules:\n"
            f"- The JSON must be valid JSON.\n"
            f"- Do not wrap the JSON inside Markdown code fences within the "
            f"`<state_update>` tags.\n"
            f"- Do not ask questions. Do not wait for confirmation.\n"
            f"- Do not write any state file. The state must be returned in your response.\n"
            f"- Do not modify the `meta` block. It is read-only.\n"
            f"- Put all custom data inside `payload`.\n\n"
            f"Valid top-level keys:\n"
            f"- is_complete (bool) — true when the workflow should end\n"
            f"- termination_reason (str) — optional reason\n"
            f"- payload (dict) — all custom data goes here\n"
        )

    def _build_correction_prompt(
        self,
        error: str,
        agent_name: str,
        base_prompt: Optional[str] = None,
    ) -> str:
        # base_prompt wird bewusst nicht mehr verwendet.
        # Der Korrekturlauf läuft mit -c im bereits etablierten Kontext.
        return (
            "SYSTEM NOTICE: Your previous response did not contain a valid state update.\n"
            f"Agent: {agent_name}\n"
            f"Error: {error}\n\n"
            "Based on the work you have already done, reply now with ONLY one valid JSON object "
            "wrapped in <state_update> tags.\n\n"
            "Do not ask questions.\n"
            "Do not redo your original task.\n"
            "Do not repeat your analysis.\n"
            "Do not add explanations outside the tags.\n"
            "Do not write files.\n"
            "Do not use Markdown code fences inside the tags.\n"
            "Do not modify meta.\n"
            "Keep the completion rules from your original role instructions.\n\n"
            "The JSON must be strict: no comments, no trailing commas. Use null for unknown values.\n"
            "Allowed top-level keys: is_complete, termination_reason, payload.\n"
            "If unsure or blocked, still output a state update with \"is_complete\": false "
            "and a short explanation in payload.summary.\n\n"
            "Example:\n"
            "<state_update>\n"
            "{\"is_complete\": false, \"payload\": {\"summary\": \"short factual summary\"}}\n"
            "</state_update>\n"
        )

    def _evaluate_end_condition(self, condition: str) -> bool:
        if condition == "is_complete == True":
            return bool(self.state.is_complete)

        ns = {
            "is_complete": self.state.is_complete,
            "iteration": self.state.iteration,
            "termination_reason": self.state.termination_reason,
            "phase": self.state.current_phase,
            "payload": self.state.payload,
            "meta": getattr(self.state, "meta", {}),
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