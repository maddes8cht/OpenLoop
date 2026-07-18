import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


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
        timeout: Optional[int] = None,
        cwd: Optional[str] = None,
        init_script: Optional[str] = None,
    ) -> RunResult:
        effective_timeout = timeout if timeout is not None else self.timeout
        cmd = [self.binary, "run", prompt]

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
