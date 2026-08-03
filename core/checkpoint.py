import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


CHECKPOINT_SCHEMA = 1

CHECKPOINT_EXT = ".json"


def checkpoint_path_for(path: str | Path) -> Path:
    """Derive the checkpoint path for a log or checkpoint file.

    Accepts either an OpenLoop log (``.log``) or an existing checkpoint
    (``.json``). For a log, the extension is swapped to ``.json`` so the
    checkpoint lives next to the log with the same base name. For a
    checkpoint, the path is returned unchanged.
    """
    p = Path(path)
    if p.suffix.lower() == CHECKPOINT_EXT:
        return p
    return p.with_suffix(CHECKPOINT_EXT)


@dataclass
class CheckpointData:
    """Serialized snapshot used to resume an interrupted workflow run.

    The checkpoint stores the complete state, the execution position (the
    last completed agent boundary), and the full workflow definition so a
    resume can re-bootstrap the engine without the original workflow file.
    """

    schema: int = CHECKPOINT_SCHEMA
    workflow: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    position: dict = field(default_factory=dict)
    run_id: str = ""
    created_at: str = ""
    log_path: str = ""

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "workflow": self.workflow,
            "state": self.state,
            "position": self.position,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointData":
        if not isinstance(data, dict):
            raise ValueError("Checkpoint must be a JSON object")

        return cls(
            schema=int(data.get("schema", CHECKPOINT_SCHEMA)),
            workflow=dict(data.get("workflow") or {}),
            state=dict(data.get("state") or {}),
            position=dict(data.get("position") or {}),
            run_id=str(data.get("run_id", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
            log_path=str(data.get("log_path", "") or ""),
        )

    def save(self, path: str | Path) -> Path:
        """Write the checkpoint atomically (tmp file + rename)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".{p.name}.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2), encoding="utf-8"
        )
        os.replace(tmp, p)
        return p

    @classmethod
    def load(cls, path: str | Path) -> Optional["CheckpointData"]:
        p = Path(path)
        if not p.exists():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(raw, dict):
            return None
        return cls.from_dict(raw)
