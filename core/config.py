import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_config: Optional["Config"] = None


@dataclass
class Config:
    agents_dir: str = "./agents"
    workflows_dir: str = "./workflows"
    opencode_binary: str = "opencode"
    default_max_loops: int = 10
    workdir: Optional[str] = None
    init_script: Optional[str] = None

    @classmethod
    def load(cls, path: str | Path = "config.json") -> "Config":
        global _config
        _config = cls._from_file(path)
        _config._validate()
        return _config

    @classmethod
    def _from_file(cls, path: str | Path) -> "Config":
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"config.json is not valid JSON: {exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise ValueError("config.json must contain a JSON object")

        return cls(
            agents_dir=str(raw.get("agents_dir", cls.agents_dir)),
            workflows_dir=str(raw.get("workflows_dir", cls.workflows_dir)),
            opencode_binary=str(raw.get("opencode_binary", cls.opencode_binary)),
            default_max_loops=int(raw.get("default_max_loops", cls.default_max_loops)),
            workdir=raw.get("workdir"),
            init_script=raw.get("init_script"),
        )

    def _validate(self) -> None:
        for key, path_str in (
            ("agents_dir", self.agents_dir),
            ("workflows_dir", self.workflows_dir),
        ):
            p = Path(path_str)
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ValueError(
                    f"Cannot access {key} ({path_str}): {exc}"
                ) from exc

        if self.default_max_loops < 1:
            raise ValueError(
                f"default_max_loops must be >= 1, got {self.default_max_loops}"
            )


def get_config() -> Config:
    if _config is None:
        raise RuntimeError("Configuration not loaded. Call Config.load() first.")
    return _config
