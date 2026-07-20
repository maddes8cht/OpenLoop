import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.runner import OpenCodeOptions


_config: Optional["Config"] = None
CONFIG_FILENAME = "openloop.json"
_OPENLOOP_DIR = Path(__file__).resolve().parent.parent


def strip_jsonc(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


@dataclass
class Config:
    agents_dir: str = "./agents"
    workflows_dir: str = "./workflows"
    opencode_binary: str = "opencode"
    default_max_loops: int = 10
    workdir: Optional[str] = None
    init_script: Optional[str] = None
    opencode_defaults: OpenCodeOptions = field(default_factory=OpenCodeOptions)
    log_dir: str = ".openloop"
    no_log_file: bool = False
    default_timeout: int = 1800

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        global _config
        _config = cls._from_file(path)
        _config._validate()
        return _config

    @classmethod
    def _from_file(cls, path: str | Path | None = None) -> "Config":
        if path:
            config_path = Path(path)
        else:
            config_path = Path(CONFIG_FILENAME)

        if not config_path.exists():
            fallback = _OPENLOOP_DIR / CONFIG_FILENAME
            if fallback.exists():
                config_path = fallback
            else:
                return cls()

        try:
            content = config_path.read_text(encoding="utf-8")
            raw = json.loads(strip_jsonc(content))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{config_path.name} is not valid JSON: {exc}"
            ) from exc

        if not isinstance(raw, dict):
            raise ValueError(f"{config_path.name} must contain a JSON object")

        opencode_opts = OpenCodeOptions()
        if "opencode_defaults" in raw and isinstance(raw["opencode_defaults"], dict):
            opencode_opts = OpenCodeOptions.from_dict(raw["opencode_defaults"])

        return cls(
            agents_dir=str(raw.get("agents_dir", cls.agents_dir)),
            workflows_dir=str(raw.get("workflows_dir", cls.workflows_dir)),
            opencode_binary=str(raw.get("opencode_binary", cls.opencode_binary)),
            default_max_loops=int(raw.get("default_max_loops", cls.default_max_loops)),
            workdir=raw.get("workdir"),
            init_script=raw.get("init_script"),
            opencode_defaults=opencode_opts,
            log_dir=str(raw.get("log_dir", cls.log_dir)),
            no_log_file=bool(raw.get("no_log_file", cls.no_log_file)),
            default_timeout=int(raw.get("default_timeout", cls.default_timeout)),
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
