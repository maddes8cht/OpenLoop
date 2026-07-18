import json
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class WorkflowState:
    current_phase: str = "preparation"
    iteration: int = 0
    is_complete: bool = False
    termination_reason: str = ""
    payload: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "WorkflowState":
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("JSON root must be a dict")
        return cls(
            current_phase=str(data.get("current_phase", cls.current_phase)),
            iteration=int(data.get("iteration", cls.iteration)),
            is_complete=bool(data.get("is_complete", cls.is_complete)),
            termination_reason=str(data.get("termination_reason", cls.termination_reason)),
            payload=dict(data.get("payload", {})),
        )

    def merge(self, update: dict) -> None:
        if "current_phase" in update:
            self.current_phase = str(update["current_phase"])
        if "iteration" in update:
            self.iteration = int(update["iteration"])
        if "is_complete" in update:
            self.is_complete = bool(update["is_complete"])
        if "termination_reason" in update:
            self.termination_reason = str(update["termination_reason"])
        if "payload" in update:
            if isinstance(update["payload"], dict):
                self.payload.update(update["payload"])
