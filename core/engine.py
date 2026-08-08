import json
import sys
import threading
import uuid
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from core.agent import AgentDefinition, AgentLoader
from core.checkpoint import CheckpointData, checkpoint_path_for
from core.config import Config, strip_jsonc
from core.parser import StateParser
from core.runner import OpenCodeOptions, OpenCodeRunner, RunResult
from core.state import WorkflowState


MissingStateHandler = Callable[[str, Optional[Path]], bool]


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
    name: Optional[str] = None
    log_dir: Optional[str] = None

    @staticmethod
    def _clean_agent_list(value: object) -> list[str]:
        if isinstance(value, str):
            value = [value]

        if not isinstance(value, list):
            return []

        cleaned: list[str] = []

        for item in value:
            if item is None:
                continue

            s = str(item).strip()
            if s:
                cleaned.append(s)

        return cleaned

    @staticmethod
    def _clean_optional_str(value: object) -> Optional[str]:
        if value is None:
            return None

        s = str(value).strip()
        return s or None

    @classmethod
    def load(cls, path: str | Path) -> "WorkflowConfig":
        p = Path(path)

        try:
            raw = json.loads(strip_jsonc(p.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise FileNotFoundError(f"Workflow not found: {p}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid workflow JSON: {exc}")

        if not isinstance(raw, dict):
            raise ValueError("Workflow file must contain a JSON object")

        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowConfig":
        # Robust against accidental trailing spaces in keys.
        data = {
            str(k).strip(): v
            for k, v in data.items()
            if isinstance(k, str)
        }

        prep = data.get("preparation_agents")
        if prep is None:
            prep = data.get("preparation_agent")

        final = data.get("finalization_agents")
        if final is None:
            final = data.get("finalization_agent")

        opencode_opts = OpenCodeOptions()
        if "opencode_defaults" in data and isinstance(data["opencode_defaults"], dict):
            opencode_opts = OpenCodeOptions.from_dict(data["opencode_defaults"])

        end_state_condition = cls._clean_optional_str(
            data.get("end_state_condition")
        ) or cls.end_state_condition

        try:
            max_loops = int(data.get("max_loops", cls.max_loops))
        except (TypeError, ValueError):
            max_loops = cls.max_loops

        return cls(
            preparation_agents=cls._clean_agent_list(prep),
            loop_agents=cls._clean_agent_list(data.get("loop_agents")),
            finalization_agents=cls._clean_agent_list(final),
            end_state_condition=end_state_condition,
            max_loops=max_loops,
            finalize_on_abort=bool(
                data.get("finalize_on_abort", cls.finalize_on_abort)
            ),
            workdir=cls._clean_optional_str(data.get("workdir")),
            init_script=cls._clean_optional_str(data.get("init_script")),
            opencode_defaults=opencode_opts,
            name=cls._clean_optional_str(data.get("name")),
            log_dir=cls._clean_optional_str(data.get("log_dir")),
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
            "name": self.name,
            "log_dir": self.log_dir,
        }

        opts_dict = self.opencode_defaults.to_dict()
        if opts_dict:
            d["opencode_defaults"] = opts_dict

        return d


LogCallback = Callable[[str], None]


class ExecutionEngine:
    MAX_CORRECTIONS = 2

    CORRECTION_EXAMPLE = (
        "<state_update>\n"
        '{"is_complete": false, "payload": {"summary": "Brief factual summary of completed work"}}\n'
        "</state_update>"
    )

    CORRECTION_FAILURE_HINTS = {
        "missing": (
            "The previous response contained no usable state block. "
            "Reconstruct the state from the work already done and output it now."
        ),
        "xml_bad_json": (
            "A <state_update> tag was found, but the JSON inside it was invalid. "
            "Resend the same intended state with corrected JSON syntax only. "
            "Do not change the intended meaning unless necessary."
        ),
        "json_block_no_xml": (
            "You used a Markdown JSON block. "
            "Wrap the same JSON object in <state_update> tags and remove code fences."
        ),
        "file_reference": (
            "You wrote or referenced a state file. "
            "The state must be in your response text. "
            "Paste the state block directly. "
            "Do not mention file paths."
        ),
        "truncated_xml": (
            "The previous output appears truncated. "
            "Keep the payload concise. "
            "Omit logs and long reports. "
            "Output the complete <state_update> element with a closing tag."
        ),
    }

    STATE_TAG_OPEN_RE = re.compile(r"<\s*state_update\s*>", re.IGNORECASE)
    STATE_TAG_CLOSE_RE = re.compile(r"</\s*state_update\s*>", re.IGNORECASE)

    PROTECTED_STATE_KEYS = {
        "current_phase",
        "iteration",
        "meta",
    }

    KNOWN_STATE_KEYS = {
        "is_complete",
        "termination_reason",
        "payload",
    }

    # Fallback for older agent definitions without explicit can_complete field.
    # For explicit control, add can_complete: true/false to agent frontmatter.
    COMPLETION_ROLES_FALLBACK = {
        "auditor",
        "approver",
        "finalizer",
        "finalization",
    }

    def __init__(
        self,
        config: Optional[Config] = None,
        logger: Optional[LogCallback] = None,
        stop_event: Optional[threading.Event] = None,
        verbose: bool = False,
        no_log_file: bool = False,
        log_file: Optional[str] = None,
        log_dir: Optional[str] = None,
        timeout: Optional[int] = None,
        missing_state_handler: Optional[MissingStateHandler] = None,
        missing_state_policy: str = "ask",
        state_callback: Optional[Callable[[dict], None]] = None,
        log_path_callback: Optional[Callable[[Path], None]] = None,
    ):
        self.config = config or Config()
        self.logger = logger or (lambda msg: print(f"[OpenLoop] {msg}"))
        self.state = WorkflowState()
        self._state_callback = state_callback
        self._log_path_callback = log_path_callback
        self.agent_loader = AgentLoader(self.config.agents_dir)

        self._timeout = timeout if timeout is not None else self.config.default_timeout
        self.runner = OpenCodeRunner(
            binary=self.config.opencode_binary,
            timeout=self._timeout,
        )

        self._stop_event = stop_event or threading.Event()

        self._log_handle = None
        self._log_path: Optional[Path] = None
        self._log_dir: Optional[Path] = None
        self._system_open = False
        self._start_time: Optional[datetime] = None
        self._verbose = verbose
        self._no_log_file = no_log_file
        self._log_file_arg = log_file
        self._log_dir_arg = log_dir

        self._workdir: Optional[str] = None
        self._init_script: Optional[str] = None
        self._workflow_name: Optional[str] = None
        self._workflow_log_dir: Optional[str] = None
        self._opencode_opts = OpenCodeOptions()

        self._missing_state_handler = missing_state_handler
        self._missing_state_policy = missing_state_policy

        # Resume support (#47)
        self._resuming = False
        self._resume_position: dict = {}
        self._checkpoint_path: Optional[Path] = None
        self._workflow_dict: dict = {}
        self._last_position: dict = {}

    # ---- File logging ----

    def _init_log(
        self, workdir: Optional[str] = None,
        workflow_log_dir: Optional[str] = None,
    ) -> None:
        if self._no_log_file:
            return

        if self._log_file_arg:
            self._log_path = Path(self._log_file_arg)
            self._log_dir = self._log_path.parent
        else:
            # Override chain: CLI --log-dir > workflow log_dir > config log_dir
            effective = (
                self._log_dir_arg
                or workflow_log_dir
                or self.config.log_dir
            )
            log_dir = Path(effective)
            if not log_dir.is_absolute() and workdir:
                log_dir = Path(workdir) / log_dir

            self._log_dir = log_dir

            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            if self._workflow_name:
                self._log_path = (
                    log_dir / f"openloop-run-{self._workflow_name}-{ts}.log"
                )
            else:
                self._log_path = log_dir / f"openloop-run-{ts}.log"

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("w", encoding="utf-8")
        self._system_open = False

        # Checkpoints live next to the log with a swapped extension. A fresh
        # run always starts checkpoint-free so a stale checkpoint from a
        # previous run using the same explicit log file cannot be resumed.
        self._checkpoint_path = checkpoint_path_for(self._log_path)
        if not self._resuming:
            self._delete_checkpoint()

        self._write_log("<openloop_log>\n")
        self._start_time = datetime.now()
        self._log_system(
            f"OpenLoop run started at "
            f"{self._start_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        if self._log_path_callback:
            self._log_path_callback(self._log_path)

    def _init_log_resume(self, log_path: Path) -> None:
        """Open an existing log in append mode for a resumed run.

        The continuation is written as a second ``<openloop_log>`` root so
        the LoopLog viewer renders the original run and the resumed portion
        as two top-level sections. A ``# OPENLOOP RESUMED`` marker line makes
        the boundary greppable in the raw file.
        """
        if self._no_log_file:
            return

        self._log_path = Path(log_path)
        self._log_dir = self._log_path.parent
        self._checkpoint_path = checkpoint_path_for(self._log_path)

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_handle = self._log_path.open("a", encoding="utf-8")
        self._system_open = False

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._write_log(
            f"# OPENLOOP RESUMED at {ts} "
            f"(run_id: {self._get_run_id()}, "
            f"reason: {self.state.termination_reason})\n"
        )
        self._write_log("<openloop_log>\n")
        self._start_time = datetime.now()
        self._log_system(f"OpenLoop run resumed at {ts}")

        if self._log_path_callback:
            self._log_path_callback(self._log_path)

    def _close_log(self) -> None:
        if self._log_handle:
            end_time = datetime.now()
            # Close any dangling <system> block (e.g. after an unexpected
            # exception) so the summary always lands in its own block.
            self._flush_system()
            self._log_system("")
            if self._workflow_name:
                self._log_system(f"Workflow: {self._workflow_name}")
            if self._start_time:
                start_line = (
                    "OpenLoop run started at "
                    f"{self._start_time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
            else:
                start_line = "OpenLoop run started at (unknown)"
            self._log_system(start_line)
            self._log_system(
                f"OpenLoop run finished at "
                f"{end_time.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            self._log_system("")
            self._log_system(
                f"Finished {self.state.iteration} loop iterations"
            )
            if self._start_time:
                self._log_system(
                    f"OpenLoop run duration was "
                    f"{self._format_duration(end_time - self._start_time)}"
                )
            self._log_system("")
            self._log_system(self._termination_summary_line())
            self._flush_system()
            self._write_log("</openloop_log>\n")
            self._log_handle.close()
            self._log_handle = None

    @staticmethod
    def _format_duration(delta: timedelta) -> str:
        """Format a duration as H:MM:SS (e.g. 0:53:46, 2:07:03)."""
        total = int(delta.total_seconds())
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}"

    # ---- Checkpointing (#47) ----

    def _write_checkpoint(
        self, workflow: "WorkflowConfig", phase: str, agent_index: int
    ) -> None:
        """Persist a checkpoint after a completed agent boundary."""
        if not self._checkpoint_path:
            return

        self._workflow_dict = workflow.to_dict()
        self._last_position = {
            "phase": phase,
            "iteration": self.state.iteration,
            "agent_index": agent_index,
        }

        checkpoint = CheckpointData(
            workflow=self._workflow_dict,
            state=asdict(self.state),
            position=dict(self._last_position),
            run_id=self._get_run_id(),
            created_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            log_path=str(self._log_path or ""),
        )
        try:
            checkpoint.save(self._checkpoint_path)
        except OSError as exc:
            self.log(f"  WARNING: Could not write checkpoint: {exc}")

    def _write_terminal_checkpoint(self) -> None:
        """Refresh the checkpoint with the final state on abnormal exit.

        The position stays at the last completed agent boundary (the resume
        point), but the state now carries the real termination reason so the
        resume-reason filter and the resume banner see it.
        """
        if not self._checkpoint_path:
            return

        # The run may stop in a phase after the last *completed* agent
        # boundary, e.g. when the first loop agent fails right after
        # preparation finished. In that case _last_position still points at
        # the earlier phase, and resuming there would needlessly re-run that
        # whole phase. Detect the mismatch via the state's current phase and
        # fall back to a "start of interrupted phase" position.
        position = dict(self._last_position)
        interrupted_phase = self.state.current_phase or ""
        if (
            not position
            or (interrupted_phase and position.get("phase") != interrupted_phase)
        ):
            # agent_index -1 means "re-run from the first agent of this phase".
            position = {
                "phase": interrupted_phase or position.get("phase") or "loop",
                "iteration": self.state.iteration,
                "agent_index": -1,
            }

        checkpoint = CheckpointData(
            workflow=self._workflow_dict,
            state=asdict(self.state),
            position=position,
            run_id=self._get_run_id(),
            created_at=datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            log_path=str(self._log_path or ""),
        )
        try:
            checkpoint.save(self._checkpoint_path)
        except OSError as exc:
            self.log(f"  WARNING: Could not write checkpoint: {exc}")

    def _delete_checkpoint(self) -> None:
        if not self._checkpoint_path:
            return
        try:
            self._checkpoint_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _termination_summary_line(self) -> str:
        """Human-readable summary of why the run ended."""
        reason = self.state.termination_reason or ""
        if reason == "completed":
            return "OpenLoop run completed successfully"
        if reason == "stopped":
            return "OpenLoop run stopped by user"
        if reason == "max_loops_reached":
            return "OpenLoop run stopped: max loops reached"
        if reason.startswith("agent_error:"):
            return f"OpenLoop run stopped: agent error ({reason[12:]})"
        if reason.startswith("timeout:"):
            parts = reason.split(":")
            agent = parts[1] if len(parts) > 1 else "?"
            secs = parts[2] if len(parts) > 2 else "?"
            return f"OpenLoop run stopped: agent '{agent}' timed out after {secs}s"
        if reason.startswith("missing_state:"):
            return (
                f"OpenLoop run stopped: agent '{reason[14:]}' "
                f"returned no state update"
            )
        if reason:
            return f"OpenLoop run finished (reason: {reason})"
        return "OpenLoop run finished"

    def _write_log(self, text: str) -> None:
        if self._log_handle:
            self._log_handle.write(text)
            self._log_handle.flush()

    def _log_system(self, content: str) -> None:
        """Write into a <system> block, merging with any adjacent one."""
        if self._system_open:
            self._write_log(f"{content}\n")
        else:
            self._write_log(f"<system>\n{content}\n")
            self._system_open = True

    def _flush_system(self) -> None:
        """Close the currently open <system> block before a non-system write."""
        if self._system_open:
            self._write_log("</system>\n")
            self._system_open = False

    def _write_banner(self, agent_name: str) -> None:
        run_id = self._get_run_id()
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        phase = self.state.current_phase
        if phase == "preparation":
            label = f"[OpenLoop] Preparation phase: {agent_name}"
        elif phase == "finalization":
            label = f"[OpenLoop] Finalization phase: {agent_name}"
        else:
            label = f"[OpenLoop]   Running agent: {agent_name}"

        self._flush_system()
        self._write_log(
            f"<agent name={json.dumps(agent_name)} "
            f"phase={json.dumps(phase)} "
            f"iteration=\"{self.state.iteration}\" "
            f"run_id={json.dumps(run_id)}>\n"
        )
        self._log_system(
            f"{label}\n"
            f"{'=' * 70}\n"
            f"  {ts} | "
            f"Agent: {agent_name} | Phase: {phase} | "
            f"Iteration: {self.state.iteration} | Run ID: {run_id}\n"
            f"{'=' * 70}"
        )

    # ---- Run metadata ----

    def _init_run_meta(self) -> None:
        now = datetime.now(timezone.utc)

        run_id = (
            f"{now.strftime('%Y%m%d-%H%M%SZ')}"
            f"-{uuid.uuid4().hex[:6]}"
        )

        meta = {
            "run_id": run_id,
            "started_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

        # Preferred: dedicated top-level meta block, if state.py supports it.
        if hasattr(self.state, "meta"):
            self.state.meta = meta
        else:
            # Fallback for older state definitions without explicit meta field.
            self.state.payload.setdefault("_openloop", {}).update(meta)

    def _get_run_id(self) -> str:
        meta = getattr(self.state, "meta", None)
        if isinstance(meta, dict):
            run_id = meta.get("run_id")
            if run_id:
                return str(run_id)

        openloop_meta = self.state.payload.get("_openloop")
        if isinstance(openloop_meta, dict):
            return str(openloop_meta.get("run_id", ""))

        return ""

    # ---- State callback ----

    def _notify_state(self) -> None:
        if self._state_callback:
            self._state_callback(asdict(self.state))

    # ---- Missing state policy ----

    def _default_missing_state_handler(
        self,
        agent_name: str,
        log_path: Optional[Path],
    ) -> bool:
        self.log(
            f"  WARNING: Agent '{agent_name}' did not provide "
            f"a valid state update."
        )

        if log_path:
            self.log(f"  Inspect agent output in: {log_path}")

        try:
            if sys.stdin and sys.stdin.isatty():
                answer = input(
                    "Continue workflow anyway at your own risk? [y/N] "
                ).strip().lower()
                return answer in {"y", "yes"}
        except Exception as exc:
            self.log(f"  WARNING: Interactive prompt failed: {exc}")

        self.log(
            "  Non-interactive session — aborting due to missing state update."
        )
        return False

    def _handle_missing_state(self, agent_name: str) -> bool:
        """Decide whether to continue when an agent returns no state update.

        Policy (``missing_state_policy``) takes precedence over any injected
        handler:

        * ``"continue"`` — always proceed (agent treated as success).
        * ``"abort"`` — always terminate the workflow.
        * ``"ask"`` (default) — delegate to the handler, which prompts
          interactively (CLI) or shows a popup (GUI), aborting when the
          answer is no or no interactive context exists.
        """
        policy = self._missing_state_policy

        if policy == "continue":
            self.log(
                f"  Policy 'continue': proceeding without a state update "
                f"from '{agent_name}'."
            )
            return True

        if policy == "abort":
            self.log(
                f"  Policy 'abort': terminating due to missing state "
                f"update from '{agent_name}'."
            )
            return False

        handler = self._missing_state_handler or self._default_missing_state_handler
        try:
            return bool(handler(agent_name, self._log_path))
        except Exception as exc:
            self.log(f"  WARNING: missing-state handler failed: {exc}")
            return False

    # ---- State normalization and authorization ----

    def _agent_may_complete(self, agent: AgentDefinition) -> bool:
        can_complete = getattr(agent, "can_complete", None)

        if isinstance(can_complete, bool):
            return can_complete

        role = str(getattr(agent, "role", "")).strip().lower()
        return role in self.COMPLETION_ROLES_FALLBACK

    @staticmethod
    def _coerce_is_complete(value: object) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, (int, float)):
            return bool(value)

        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "on"}

        return False

    def _normalize_state_update(
        self,
        state_data: dict,
        agent: AgentDefinition,
    ) -> dict:
        normalized: dict = {}
        moved: dict = {}

        ignored_protected = [
            k for k in state_data
            if k in self.PROTECTED_STATE_KEYS
        ]

        if ignored_protected:
            self.log(
                f"  NOTE: Ignoring protected key(s) from agent state: "
                f"{ignored_protected}"
            )

        for key, value in state_data.items():
            if key in self.PROTECTED_STATE_KEYS:
                continue

            if key in self.KNOWN_STATE_KEYS:
                normalized[key] = value
            else:
                moved[key] = value

        if moved:
            payload = normalized.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            for k, v in moved.items():
                payload.setdefault(k, v)

            normalized["payload"] = payload

            self.log(
                f"  NOTE: Moved non-top-level key(s) into payload: "
                f"{sorted(moved.keys())}"
            )

        # Protect fallback meta location as well.
        payload = normalized.get("payload")
        if isinstance(payload, dict):
            payload.pop("_openloop", None)

        if "is_complete" in normalized:
            if not isinstance(normalized["is_complete"], bool):
                self.log(
                    f"  NOTE: Coercing non-boolean is_complete from "
                    f"'{agent.name}': {normalized['is_complete']!r}"
                )

            normalized["is_complete"] = self._coerce_is_complete(
                normalized["is_complete"]
            )

        if (
            not self._agent_may_complete(agent)
            and normalized.get("is_complete") is True
        ):
            self.log(
                f"  WARNING: Agent '{agent.name}' is not allowed to set "
                f"is_complete=true. Forcing false."
            )

            normalized["is_complete"] = False

            payload = normalized.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            payload.setdefault("completion_blocked", True)
            payload.setdefault(
                "completion_blocked_reason",
                f"{agent.name} is not authorized to complete the workflow",
            )

            normalized["payload"] = payload

        return normalized

    # ---- Workflow execution ----

    def execute_workflow(self, workflow_path: str | Path) -> WorkflowState:
        workflow = WorkflowConfig.load(workflow_path)
        return self.execute_workflow_data(workflow.to_dict())

    def execute_workflow_data(self, data: dict) -> WorkflowState:
        clean_data = {
            str(k).strip(): v
            for k, v in data.items()
            if isinstance(k, str)
        }

        workflow = WorkflowConfig.from_dict(clean_data)

        self._resuming = False
        self._resume_position = {}
        self._workflow_dict = workflow.to_dict()

        self.state = WorkflowState()
        self._init_run_meta()

        self._workdir = workflow.workdir or self.config.workdir
        self._workflow_name = workflow.name
        self._workflow_log_dir = workflow.log_dir
        self._init_log(self._workdir, workflow_log_dir=workflow.log_dir)

        self.log(f"Loaded workflow: {workflow.loop_agents}")
        self.log(f"Run ID: {self._get_run_id()}")

        raw_init = workflow.init_script or self.config.init_script
        if raw_init:
            p = Path(raw_init)
            if not p.is_absolute() and (Path.cwd() / p).is_file():
                self._init_script = str((Path.cwd() / raw_init).resolve())
            else:
                self._init_script = raw_init
        else:
            self._init_script = None

        if "max_loops" not in clean_data:
            workflow.max_loops = self.config.default_max_loops

        self._opencode_opts = self.config.opencode_defaults.merge(
            workflow.opencode_defaults
        )

        try:
            if not self._run_preparation(workflow):
                return self.state

            if self._evaluate_end_condition(workflow.end_state_condition):
                if not self.state.termination_reason:
                    self.state.termination_reason = "completed"
            else:
                if not self._run_loop(workflow):
                    return self.state

            if not self._run_finalization(workflow):
                return self.state

            return self.state
        finally:
            self._close_log()
            if self.state.termination_reason == "completed":
                self._delete_checkpoint()
            else:
                self._write_terminal_checkpoint()

    def execute_resume(
        self,
        checkpoint_path: str | Path,
        *,
        max_loops_override: Optional[int] = None,
    ) -> WorkflowState:
        """Resume an interrupted run from a checkpoint file.

        The workflow definition, state, and execution position are read from
        the checkpoint. ``max_loops_override`` allows continuing past a
        ``max_loops_reached`` termination with a higher limit.
        """
        checkpoint = CheckpointData.load(checkpoint_path)
        if checkpoint is None:
            raise FileNotFoundError(
                f"Checkpoint not found or invalid: {checkpoint_path}"
            )

        workflow_data = dict(checkpoint.workflow)
        if max_loops_override is not None:
            workflow_data["max_loops"] = max_loops_override

        workflow = WorkflowConfig.from_dict(workflow_data)

        reason = str(checkpoint.state.get("termination_reason", ""))
        if not self.config.allows_resume(reason):
            self.log(
                f"  Resume not allowed for termination reason '{reason}'"
            )
            raise ValueError(
                f"Resume not allowed for reason: {reason}"
            )

        self._resuming = True
        self._resume_position = dict(checkpoint.position or {})
        self._workflow_dict = workflow.to_dict()

        self.state = WorkflowState.from_json(json.dumps(checkpoint.state))

        self._workdir = workflow.workdir or self.config.workdir
        self._workflow_name = workflow.name
        self._workflow_log_dir = workflow.log_dir

        raw_init = workflow.init_script or self.config.init_script
        if raw_init:
            p = Path(raw_init)
            if not p.is_absolute() and (Path.cwd() / p).is_file():
                self._init_script = str((Path.cwd() / raw_init).resolve())
            else:
                self._init_script = raw_init
        else:
            self._init_script = None

        self._opencode_opts = self.config.opencode_defaults.merge(
            workflow.opencode_defaults
        )

        log_path = Path(checkpoint.log_path or "")
        if not log_path.is_file():
            raise FileNotFoundError(
                f"Log file for resume not found: {log_path or 'unknown'}"
            )
        self._init_log_resume(log_path)

        self.log(f"Resuming run {self._get_run_id()} from checkpoint")
        self.log(f"  Previous termination: {reason}")

        try:
            resume_phase = self._resume_position.get("phase")

            if resume_phase in ("preparation", None):
                if not self._run_preparation(workflow):
                    return self.state
            # loop/finalization: preparation already completed

            if resume_phase in ("preparation", "loop", None):
                if self._evaluate_end_condition(workflow.end_state_condition):
                    if not self.state.termination_reason:
                        self.state.termination_reason = "completed"
                elif not self._run_loop(workflow):
                    return self.state

            # finalization always runs last; its internal logic decides
            # whether the configured finalize_on_abort policy applies.
            if not self._run_finalization(workflow):
                return self.state

            # finalization-phase resumes skip the loop, so the state still
            # carries the interrupted run's reason; mark completion now that
            # the remaining finalization agents have succeeded.
            if resume_phase == "finalization":
                self.state.termination_reason = "completed"
            elif not self.state.termination_reason:
                self.state.termination_reason = "completed"

            return self.state
        finally:
            self._close_log()
            if self.state.termination_reason == "completed":
                self._delete_checkpoint()
            else:
                self._write_terminal_checkpoint()

    def _run_preparation(self, workflow: WorkflowConfig) -> bool:
        if not workflow.preparation_agents:
            self.log("No preparation agent defined — skipping")
            return True

        self.state.current_phase = "preparation"
        self._notify_state()

        start = 0
        if self._resuming and self._resume_position.get("phase") == "preparation":
            start = self._resume_position.get("agent_index", -1) + 1
            if 0 < start < len(workflow.preparation_agents):
                self.log(
                    f"  Resuming preparation from agent "
                    f"'{workflow.preparation_agents[start]}'"
                )

        for idx in range(start, len(workflow.preparation_agents)):
            if not self._execute_agent(workflow.preparation_agents[idx]):
                return False

            self._write_checkpoint(workflow, "preparation", idx)

            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self._notify_state()
                self.log("Execution stopped by user")
                return False

        return True

    def _run_loop(self, workflow: WorkflowConfig) -> bool:
        if not workflow.loop_agents:
            self.log("No loop agents defined — skipping")
            self.state.is_complete = True
            self.state.termination_reason = "completed"
            return True

        self.state.current_phase = "loop"
        self._notify_state()

        # Resume inside a partially completed iteration (#47). The iteration
        # number is NOT re-incremented; only the remaining agents run.
        if self._resuming and self._resume_position.get("phase") == "loop":
            agents = workflow.loop_agents
            start_agent = self._resume_position.get("agent_index", -1)
            if 0 <= start_agent < len(agents) - 1:
                iteration = self._resume_position.get(
                    "iteration", self.state.iteration
                )
                self.log(
                    f"  Resuming iteration {iteration}, continuing with "
                    f"agent '{agents[start_agent + 1]}'"
                )
                self._flush_system()
                self._write_log(
                    f"<iteration number=\"{iteration}\" "
                    f"max=\"{workflow.max_loops}\">\n"
                )
                self._log_system(
                    f"Resumed loop iteration {iteration}/{workflow.max_loops}"
                )

                for idx in range(start_agent + 1, len(agents)):
                    if not self._execute_agent(agents[idx]):
                        self._flush_system()
                        self._write_log("</iteration>\n")
                        return False

                    self._write_checkpoint(workflow, "loop", idx)

                    if self._stop_event.is_set():
                        self.state.termination_reason = "stopped"
                        self._notify_state()
                        self.log("Execution stopped by user")
                        self._flush_system()
                        self._write_log("</iteration>\n")
                        return False

                    if self._evaluate_end_condition(workflow.end_state_condition):
                        self.log(
                            f"  Termination condition met "
                            f"(iteration {iteration})"
                        )
                        self.state.termination_reason = "completed"
                        self._notify_state()
                        self._flush_system()
                        self._write_log("</iteration>\n")
                        return True

                self._flush_system()
                self._write_log("</iteration>\n")

        while self.state.iteration < workflow.max_loops:
            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self._notify_state()
                self.log("Execution stopped by user")
                return False

            self.state.iteration += 1
            self._notify_state()
            self._flush_system()
            self._write_log(
                f"<iteration number=\"{self.state.iteration}\" max=\"{workflow.max_loops}\">\n"
            )
            self._log_system(
                f"Loop iteration {self.state.iteration}/{workflow.max_loops}"
            )

            for idx, agent_name in enumerate(workflow.loop_agents):
                if not self._execute_agent(agent_name):
                    self._flush_system()
                    self._write_log("</iteration>\n")
                    return False

                self._write_checkpoint(workflow, "loop", idx)

                if self._stop_event.is_set():
                    self.state.termination_reason = "stopped"
                    self._notify_state()
                    self.log("Execution stopped by user")
                    self._flush_system()
                    self._write_log("</iteration>\n")
                    return False

                if self._evaluate_end_condition(workflow.end_state_condition):
                    self.log(
                        f"  Termination condition met "
                        f"(iteration {self.state.iteration})"
                    )
                    self.state.termination_reason = "completed"
                    self._notify_state()
                    self._flush_system()
                    self._write_log("</iteration>\n")
                    return True

            self._flush_system()
            self._write_log("</iteration>\n")

        self.state.termination_reason = "max_loops_reached"
        self._notify_state()
        self.log(
            f"Max loops ({workflow.max_loops}) reached — terminating loop"
        )
        return True

    def _run_finalization(self, workflow: WorkflowConfig) -> bool:
        if not workflow.finalization_agents:
            self.log("No finalization agent defined — skipping")
            return True

        resuming_in_finalization = (
            self._resuming
            and self._resume_position.get("phase") == "finalization"
        )
        should_finalize = (
            self.state.termination_reason == "completed"
            or (
                self.state.termination_reason == "max_loops_reached"
                and workflow.finalize_on_abort
            )
            or resuming_in_finalization
        )

        if not should_finalize:
            self.log("Finalization skipped (configured to run on completion only)")
            return True

        self.state.current_phase = "finalization"
        self._notify_state()

        start = 0
        if self._resuming and self._resume_position.get("phase") == "finalization":
            start = self._resume_position.get("agent_index", -1) + 1
            if 0 < start < len(workflow.finalization_agents):
                self.log(
                    f"  Resuming finalization from agent "
                    f"'{workflow.finalization_agents[start]}'"
                )

        for idx in range(start, len(workflow.finalization_agents)):
            if not self._execute_agent(workflow.finalization_agents[idx]):
                return False

            self._write_checkpoint(workflow, "finalization", idx)

            if self._stop_event.is_set():
                self.state.termination_reason = "stopped"
                self._notify_state()
                self.log("Execution stopped by user")
                return False

        return True

    def _execute_agent(self, agent_name: str) -> bool:
        agent = self.agent_loader.get_agent(agent_name)

        self._write_banner(agent_name)

        # Write the effective state the agent is about to receive as a
        # dedicated <state> block directly before <stdout>. The state is
        # constant during a single agent run (correction attempts do not
        # merge), so one block per agent is complete.
        self._flush_system()
        self._write_log(f"<state>\n{self.state.to_json()}\n</state>\n\n")

        base_prompt = self._build_prompt(agent)
        prompt = base_prompt
        initial_state_json = self.state.to_json()

        state_data = None

        for attempt in range(1 + self.MAX_CORRECTIONS):
            prompt_file = None
            if self._log_dir is not None:
                prompt_file = self._log_dir / self.runner.PROMPT_FILENAME
            else:
                effective = (
                    self._log_dir_arg
                    or self._workflow_log_dir
                    or self.config.log_dir
                )
                log_dir = Path(effective)
                if not log_dir.is_absolute() and self._workdir:
                    log_dir = Path(self._workdir) / log_dir
                prompt_file = log_dir / self.runner.PROMPT_FILENAME

            opts = self._opencode_opts
            if attempt > 0:
                opts = opts.merge(OpenCodeOptions(pure=True))

            result = self.runner.run(
                prompt,
                opts=opts,
                cwd=self._workdir,
                init_script=self._init_script,
                continue_session=(attempt > 0),
                prompt_file=prompt_file,
            )

            if result.output:
                self._flush_system()
                self._write_log(f"<stdout>\n{result.output}\n</stdout>\n\n")
                if self._verbose:
                    print(result.output)

            if result.error:
                self._flush_system()
                self._write_log(f"<stderr>\n{result.error}\n</stderr>\n\n")
                if self._verbose:
                    print(result.error, file=sys.stderr)

            if getattr(result, "timed_out", False):
                seconds = self._timeout if self._timeout else 0
                self.log(
                    f"  Agent '{agent_name}' timed out after "
                    f"{seconds or 'unlimited'}s"
                )
                self.state.termination_reason = (
                    f"timeout:{agent_name}:{seconds}"
                )
                self._notify_state()
                self._flush_system()
                self._write_log("</agent>\n")
                return False

            if not result.success:
                self.log(
                    f"  Agent '{agent_name}' failed "
                    f"(exit {result.exit_code})"
                )
                self.state.termination_reason = f"agent_error:{agent_name}"
                self._notify_state()
                self._flush_system()
                self._write_log("</agent>\n")
                return False

            # State is extracted exclusively from the agent response.
            state_data = StateParser.extract_state_update(result.output)

            if state_data is not None:
                state_data = self._normalize_state_update(state_data, agent)

                if state_data:
                    break

                state_data = None

            if attempt < self.MAX_CORRECTIONS:
                self.log(
                    f"  State update missing or invalid — "
                    f"correction attempt {attempt + 1}/{self.MAX_CORRECTIONS}"
                )
                reason = self._classify_state_failure(result.output)
                prompt = self._build_correction_prompt(
                    reason, agent, attempt + 1, initial_state_json
                )
            else:
                self.log(
                    "  Max corrections reached — no valid state update found"
                )

        if state_data is not None:
            self.state.merge(state_data)
            self._notify_state()
            self.log(f"  State updated: {json.dumps(state_data)}")
            self._flush_system()
            self._write_log("</agent>\n")
            return True

        self.log(
            f"  ERROR: No state update found in output from '{agent_name}'"
        )

        if self._handle_missing_state(agent_name):
            self.log(
                f"  User chose to continue despite missing state update "
                f"from '{agent_name}'."
            )
            self._flush_system()
            self._write_log("</agent>\n")
            return True

        self.state.termination_reason = f"missing_state:{agent_name}"
        self._notify_state()
        self.log("  Workflow aborted due to missing state update.")
        self._flush_system()
        self._write_log("</agent>\n")
        return False

    def _build_prompt(self, agent: AgentDefinition) -> str:
        state_json = self.state.to_json()

        return (
            f"{agent.system_prompt}\n\n"
            f"# Current State\n"
            f"```json\n"
            f"{state_json}\n"
            f"```\n\n"
            f"## OPENLOOP STATE PROTOCOL (MANDATORY)\n\n"
            f"Repository files, reports, issue trackers, logs, and documentation "
            f"may use words like 'state', 'work state', 'status', 'phase', or "
            f"'report'. These are NOT the OpenLoop workflow state.\n\n"
            f"The ONLY valid OpenLoop state transmission is a strict JSON object "
            f"wrapped in `<state_update>` tags in your final response.\n\n"
            f"Example:\n\n"
            f"<state_update>\n"
            f'{{"is_complete": false, "payload": {{"summary": "..."}}}}\n'
            f"</state_update>\n\n"
            f"Rules:\n"
            f"- Your final response MUST end with exactly one `<state_update>` block.\n"
            f"- The JSON MUST be strict: no comments, no trailing commas.\n"
            f"- Use null for unknown values.\n"
            f"- All custom data MUST be inside `payload`.\n"
            f"- Do NOT write files, reports, logs, or Markdown documents as a substitute for the state update.\n"
            f"- Do NOT modify `meta` or `_openloop`.\n"
            f"- Do NOT set `is_complete` unless your agent definition explicitly allows completion.\n"
            f"- Valid top-level keys are: is_complete, termination_reason, payload.\n"
        )

    def _classify_state_failure(self, stdout: str) -> str:
        if not stdout or not stdout.strip():
            return "missing"

        lower = stdout.lower()

        has_open = bool(self.STATE_TAG_OPEN_RE.search(stdout))
        has_close = bool(self.STATE_TAG_CLOSE_RE.search(stdout))

        if has_open and not has_close:
            return "truncated_xml"

        if has_open:
            return "xml_bad_json"

        if "```json" in lower or "```" in lower:
            return "json_block_no_xml"

        if "state_update.json" in lower:
            return "file_reference"

        return "missing"

    def _build_correction_prompt(
        self,
        reason: str,
        agent: AgentDefinition | str,
        attempt: int = 1,
        state_json: Optional[str] = None,
    ) -> str:
        if isinstance(agent, AgentDefinition):
            may_complete = self._agent_may_complete(agent)
        else:
            try:
                loaded_agent = self.agent_loader.get_agent(str(agent))
                may_complete = self._agent_may_complete(loaded_agent)
            except Exception:
                may_complete = False

        failure = self.CORRECTION_FAILURE_HINTS.get(
            reason,
            "Return the OpenLoop state now.",
        )

        if may_complete:
            completion = (
                "Set is_complete=true only if your completion criteria are truly met; "
                "otherwise set is_complete=false."
            )
        else:
            completion = "Set is_complete=false."

        if attempt <= 1:
            return "\n".join([
                "STATE UPDATE REQUIRED",
                "",
                failure,
                "",
                "Return the state update as a single strict JSON object inside "
                "<state_update> tags, exactly in this shape:",
                "",
                self.CORRECTION_EXAMPLE,
                "",
                completion,
            ])

        state_block = state_json or "{}"
        return "\n".join([
            "FINAL STATE UPDATE REQUIRED",
            "",
            "Something seems really hard to produce a valid state update. Let us try again.",
            "",
            f"Previous attempt failed: {failure}",
            "",
            "This was the current state you received at the start of this run:",
            "",
            "```json",
            state_block,
            "```",
            "",
            "Please provide the updated state based on your recent work.",
            "Return it as a single strict JSON object inside <state_update> tags. "
            "Use only is_complete, termination_reason, and payload as top-level keys, "
            "keeping the payload structure from the state shown above.",
            "",
            completion,
        ])

    def _evaluate_end_condition(self, condition: str) -> bool:
        if condition == "is_complete == True":
            return bool(self.state.is_complete)

        meta = getattr(self.state, "meta", None)
        if not isinstance(meta, dict):
            meta = self.state.payload.get("_openloop", {})

        ns = {
            "is_complete": self.state.is_complete,
            "iteration": self.state.iteration,
            "termination_reason": self.state.termination_reason,
            "phase": self.state.current_phase,
            "payload": self.state.payload,
            "meta": meta,
        }

        try:
            return bool(eval(condition, {"__builtins__": {}}, ns))
        except Exception as exc:
            self.log(
                f"  WARNING: end_state_condition evaluation failed: {exc}"
            )
            return False

    def log(self, message: str) -> None:
        self.logger(message)
        self._log_system(f"[OpenLoop] {message}")