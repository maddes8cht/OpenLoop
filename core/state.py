import json
from dataclasses import dataclass, field, asdict


@dataclass
class WorkflowState:
    current_phase: str = "preparation"
    iteration: int = 0
    is_complete: bool = False
    termination_reason: str = ""
    payload: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            self.payload = {}
        if not isinstance(self.meta, dict):
            self.meta = {}

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "WorkflowState":
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise ValueError("JSON root must be a dict")

        payload = data.get("payload")
        meta = data.get("meta")

        return cls(
            current_phase=str(data.get("current_phase", "preparation")),
            iteration=int(data.get("iteration", 0)),
            is_complete=bool(data.get("is_complete", False)),
            termination_reason=str(data.get("termination_reason", "")),
            payload=dict(payload) if isinstance(payload, dict) else {},
            meta=dict(meta) if isinstance(meta, dict) else {},
        )

    def merge(self, update: dict) -> None:
        if not isinstance(update, dict):
            return

        if "current_phase" in update:
            self.current_phase = str(update["current_phase"])

        if "iteration" in update:
            self.iteration = int(update["iteration"])

        if "is_complete" in update:
            self.is_complete = bool(update["is_complete"])

        if "termination_reason" in update:
            self.termination_reason = str(update["termination_reason"])

        if "payload" in update and isinstance(update["payload"], dict):
            self.payload.update(update["payload"])

        # meta is intentionally read-only for agents and is NOT merged.