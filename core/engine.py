import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.agent import AgentDefinition, AgentLoader
from core.config import Config
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
            raw = json.loads(p.read_text(encoding="utf-8"))
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
    def __init__(
        self,
        config: Optional[Config] = None,
        logger: Optional[LogCallback] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self.config = config or Config()
        self.logger = logger or print
        self.state = WorkflowState()
        self.agent_loader = AgentLoader(self.config.agents_dir)
        self.runner = OpenCodeRunner(
            binary=self.config.opencode_binary,
            timeout=600,
        )
        self._stop_event = stop_event or threading.Event()

    def execute_workflow(
        self, workflow_path: str | Path
    ) -> WorkflowState:
        workflow = WorkflowConfig.load(workflow_path)
        return self.execute_workflow_data(workflow.to_dict())

    def execute_workflow_data(self, data: dict) -> WorkflowState:
        workflow = WorkflowConfig.from_dict(data)
        self.state = WorkflowState()
        self.log(f"Loaded workflow: {workflow.loop_agents}")

        self._workdir = workflow.workdir or self.config.workdir
        self._init_script = workflow.init_script or self.config.init_script
        if "max_loops" not in data:
            workflow.max_loops = self.config.default_max_loops
        self._opencode_opts = self.config.opencode_defaults.merge(
            workflow.opencode_defaults
        )

        self._run_preparation(workflow)
        self._run_loop(workflow)
        self._run_finalization(workflow)

        return self.state

    def _run_preparation(self, workflow: WorkflowConfig) -> None:
        if not workflow.preparation_agents:
            self.log("No preparation agent defined — skipping")
            return

        self.state.current_phase = "preparation"
        for agent_name in workflow.preparation_agents:
            self.log(f"Preparation phase: {agent_name}")
            self._execute_agent(agent_name)

    def _run_loop(self, workflow: WorkflowConfig) -> None:
        if not workflow.loop_agents:
            self.log("No loop agents defined — skipping")
            self.state.is_complete = True
            return

        self.state.current_phase = "loop"
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

                if self.state.termination_reason and self.state.termination_reason.startswith("agent_error:"):
                    return

                if self._evaluate_end_condition(
                    workflow.end_state_condition
                ):
                    self.log(
                        f"  Termination condition met (iteration {self.state.iteration})"
                    )
                    self.state.termination_reason = "completed"
                    return

        self.state.termination_reason = "max_loops_reached"
        self.log(f"Max loops ({workflow.max_loops}) reached — terminating loop")

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
        prompt = self._build_prompt(agent)
        result = self.runner.run(
            prompt,
            opts=getattr(self, "_opencode_opts", None),
            cwd=getattr(self, "_workdir", None),
            init_script=getattr(self, "_init_script", None),
        )

        if not result.success:
            self.log(f"  Agent '{agent_name}' failed (exit {result.exit_code})")
            self.state.termination_reason = f"agent_error:{agent_name}"
            return

        update = StateParser.extract_state_update(result.output)
        if update is not None:
            self.state.merge(update)
            self.log(f"  State updated: {json.dumps(update)}")
        else:
            self.log(
                f"  WARNING: No state update found in output from '{agent_name}'"
            )

    def _build_prompt(self, agent: AgentDefinition) -> str:
        state_json = self.state.to_json()
        return (
            f"{agent.system_prompt}\n\n"
            f"# Current State\n"
            f"```json\n"
            f"{state_json}\n"
            f"```\n\n"
            f"Execute your role based on the instructions above. "
            f"After completing your task, output a state update "
            f"inside a <state_update> XML tag with any changes "
            f"to the state (especially set is_complete to true "
            f"when you are satisfied)."
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
        }
        try:
            return bool(eval(condition, {"__builtins__": {}}, ns))
        except Exception as exc:
            self.log(
                f"  WARNING: end_state_condition evaluation failed: {exc}"
            )
            return False

    def log(self, message: str) -> None:
        self.logger(f"[OpenLoop] {message}")
