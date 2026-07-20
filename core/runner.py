import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class OpenCodeOptions:
    model: Optional[str] = None
    agent: Optional[str] = None
    variant: Optional[str] = None
    pure: bool = False
    log_level: Optional[str] = None
    extra_args: list[str] = field(default_factory=list)

    def to_cli_args(self) -> list[str]:
        args: list[str] = []
        if self.model:
            args += ["-m", self.model]
        if self.agent:
            args += ["--agent", self.agent]
        if self.variant:
            args += ["--variant", self.variant]
        if self.pure:
            args += ["--pure"]
        if self.log_level:
            args += ["--log-level", self.log_level]
        args += self.extra_args
        return args

    def merge(self, override: "OpenCodeOptions") -> "OpenCodeOptions":
        merged = OpenCodeOptions(
            model=override.model or self.model,
            agent=override.agent or self.agent,
            variant=override.variant or self.variant,
            pure=override.pure if override.pure else self.pure,
            log_level=override.log_level or self.log_level,
            extra_args=self.extra_args + override.extra_args,
        )
        return merged

    def to_dict(self) -> dict:
        d: dict = {}
        if self.model is not None:
            d["model"] = self.model
        if self.agent is not None:
            d["agent"] = self.agent
        if self.variant is not None:
            d["variant"] = self.variant
        if self.pure:
            d["pure"] = True
        if self.log_level is not None:
            d["log_level"] = self.log_level
        if self.extra_args:
            d["extra_args"] = self.extra_args
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "OpenCodeOptions":
        return cls(
            model=data.get("model"),
            agent=data.get("agent"),
            variant=data.get("variant"),
            pure=bool(data.get("pure", False)),
            log_level=data.get("log_level"),
            extra_args=list(data.get("extra_args", [])),
        )


@dataclass
class RunResult:
    success: bool
    output: str
    error: str
    exit_code: int


class OpenCodeRunner:
    def __init__(self, binary: str = "opencode", timeout: int = 600):
        self.binary = binary
        self.timeout = timeout

    def run(
        self,
        prompt: str,
        opts: Optional[OpenCodeOptions] = None,
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        init_script: Optional[str] = None,
        continue_session: bool = False,
    ) -> RunResult:
        effective_timeout = timeout if timeout is not None else self.timeout
        if effective_timeout == 0:
            effective_timeout = None
        cmd = [self.binary, "run"]
        if continue_session:
            cmd += ["-c"]
        if opts:
            cmd += opts.to_cli_args()
        cmd.append(prompt)

        if init_script:
            if Path(init_script).is_file():
                ext = Path(init_script).suffix.lower()
                if ext == ".ps1":
                    prefix = (
                        f'pwsh -ExecutionPolicy Bypass -File "{init_script}"'
                    )
                elif ext in (".bat", ".cmd"):
                    prefix = f'call "{init_script}"'
                elif ext == ".sh":
                    prefix = f'sh "{init_script}"'
                else:
                    prefix = f'"{init_script}"'
            else:
                prefix = init_script
            run_args: dict = dict(
                args=f"{prefix} && {subprocess.list2cmdline(cmd)}",
                shell=True,
            )
        else:
            run_args = dict(args=cmd)

        run_args.update(
            capture_output=True, text=True, timeout=effective_timeout
        )
        if cwd:
            run_args["cwd"] = cwd

        try:
            proc = subprocess.run(**run_args)
            return RunResult(
                success=proc.returncode == 0,
                output=proc.stdout,
                error=proc.stderr,
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                success=False,
                output="",
                error=(
                    f"Process timed out after {effective_timeout}s"
                ),
                exit_code=-1,
            )
        except FileNotFoundError:
            return RunResult(
                success=False,
                output="",
                error=(
                    f"Binary '{self.binary}' not found. "
                    "Is opencode installed and in PATH?"
                ),
                exit_code=-1,
            )
        except OSError as exc:
            return RunResult(
                success=False,
                output="",
                error=f"Failed to execute '{self.binary}': {exc}",
                exit_code=-1,
            )
