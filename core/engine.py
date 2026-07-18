import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from core.agent import AgentDefinition, AgentLoader
from core.config import Config
from core.parser import StateParser
from core.runner import OpenCodeRunner, RunResult
from core.state import WorkflowState


@dataclass
class WorkflowConfig:
    preparation_agent: Optional[str] = None
    loop_agents: list[str] = field(default_factory=list)
    finalization_agent: Optional[str] = None
    end_state_condition: str = "is_complete == True"
    max_loops: int = 10
    finalize_on_abort: bool = False

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
        return cls(
            preparation_agent=data.get("preparation_agent"),
            loop_agents=list(data.get("loop_agents", [])),
            finalization_agent=data.get("finalization_agent"),
            end_state_condition=str(
                data.get("end_state_condition", cls.end_state_condition)
            ),
            max_loops=int(data.get("max_loops", cls.max_loops)),
            finalize_on_abort=bool(
                data.get("finalize_on_abort", cls.finalize_on_abort)
            ),
        )

    def to_dict(self) -> dict:
        return {
            "preparation_agent": self.preparation_agent,
            "loop_agents": self.loop_agents,
            "finalization_agent": self.finalization_agent,
            "end_state_condition": self.end_state_condition,
            "max_loops": self.max_loops,
            "finalize_on_abort": self.finalize_on_abort,
        }


LogCallback = Callable[[str], None]


class ExecutionEngine:
    def __init__(
        self,
        config: Optional[Config] = None,
        logger: Optional[LogCallback] = None,
    ):
        self.config = config or Config()
        self.logger = logger or print
        self.state = WorkflowState()
        self.agent_loader = AgentLoader(self.config.agents_dir)
        self.runner = OpenCodeRunner(
            binary=self.config.opencode_binary,
            timeout=600,
        )

    def execute_workflow(
        self, workflow_path: str | Path
    ) -> WorkflowState:
        workflow = WorkflowConfig.load(workflow_path)
        return self.execute_workflow_data(workflow.to_dict())

    def execute_workflow_data(self, data: dict) -> WorkflowState:
        workflow = WorkflowConfig.from_dict(data)
        self.state = WorkflowState()
        self.log(f"Loaded workflow: {workflow.loop_agents}")

        self._run_preparation(workflow)
        self._run_loop(workflow)
        self._run_finalization(workflow)

        return self.state

    def _run_preparation(self, workflow: WorkflowConfig) -> None:
        if not workflow.preparation_agent:
            self.log("No preparation agent defined — skipping")
            return

        self.state.current_phase = "preparation"
        self.log(f"Preparation phase: {workflow.preparation_agent}")
        self._execute_agent(workflow.preparation_agent)

    def _run_loop(self, workflow: WorkflowConfig) -> None:
        if not workflow.loop_agents:
            self.log("No loop agents defined — skipping")
            self.state.is_complete = True
            return

        self.state.current_phase = "loop"
        while self.state.iteration < workflow.max_loops:
            self.state.iteration += 1
            self.log(
                f"Loop iteration {self.state.iteration}/{workflow.max_loops}"
            )

            for agent_name in workflow.loop_agents:
                self.log(f"  Running agent: {agent_name}")
                self._execute_agent(agent_name)

                if self.state.is_complete:
                    self.log(
                        f"  Termination condition met (iteration {self.state.iteration})"
                    )
                    self.state.termination_reason = "completed"
                    return

        self.state.termination_reason = "max_loops_reached"
        self.log(f"Max loops ({workflow.max_loops}) reached — terminating loop")

    def _run_finalization(self, workflow: WorkflowConfig) -> None:
        if not workflow.finalization_agent:
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
        self.log(f"Finalization phase: {workflow.finalization_agent}")
        self._execute_agent(workflow.finalization_agent)

    def _execute_agent(self, agent_name: str) -> None:
        agent = self.agent_loader.get_agent(agent_name)
        prompt = self._build_prompt(agent)
        result = self.runner.run(prompt)

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

    def log(self, message: str) -> None:
        self.logger(f"[OpenLoop] {message}")
