import subprocess
from dataclasses import dataclass
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
        self, prompt: str, timeout: Optional[int] = None
    ) -> RunResult:
        effective_timeout = timeout if timeout is not None else self.timeout
        cmd = [self.binary, "run", prompt]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
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
