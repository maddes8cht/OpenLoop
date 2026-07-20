import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="openloop",
        description="OpenLoop — Orchestration engine for OpenCode",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in CLI mode (headless, no GUI)",
    )
    parser.add_argument(
        "--workflow",
        type=str,
        default=None,
        help="Path to workflow JSON file (pre-loads in GUI, executes in CLI mode)",
    )
    parser.add_argument(
        "--workdir",
        type=str,
        default=None,
        help="Working directory for agent subprocess (overrides config/workflow)",
    )
    parser.add_argument(
        "--init-script",
        type=str,
        dest="init_script",
        default=None,
        help="Init script or command to run before each agent (overrides config/workflow)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to configuration file (default: openloop.json in CWD, "
             "falls back to openloop.json next to openloop.py)",
    )
    parser.add_argument(
        "--opencode-defaults",
        type=str,
        dest="opencode_defaults",
        default=None,
        help="JSON string overriding opencode defaults for all agents "
             "(e.g., '{\"model\":\"gpt-4o\",\"agent\":\"plan\"}')",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="Start GUI maximized",
    )
    parser.add_argument(
        "--layout",
        choices=["default", "preview", "output"],
        default="default",
        help="GUI layout preset (default: %(default)s)",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Disable file logging",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Explicit log file path (auto-generated timestamp name by default)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        from core.config import Config as _Config

        config = _Config.load(args.config)
    except ImportError:
        config = None

    if args.cli:
        _run_cli(args, config)
    else:
        _run_gui(args, config)


def _run_cli(args: argparse.Namespace, config) -> None:
    if not args.workflow:
        print("Error: --workflow is required in CLI mode")
        sys.exit(1)

    try:
        from json import loads as json_loads
        from pathlib import Path
        from core.config import Config
        from core.engine import ExecutionEngine
        from core.runner import OpenCodeOptions

        cfg = config if isinstance(config, Config) else Config.load(args.config)
        data = json_loads(Path(args.workflow).read_text(encoding="utf-8"))

        if args.workdir:
            data["workdir"] = str(Path(args.workdir).resolve())
        if args.init_script:
            data["init_script"] = args.init_script
        if args.opencode_defaults:
            try:
                raw = json_loads(args.opencode_defaults)
                if isinstance(raw, dict):
                    data.setdefault("opencode_defaults", {})
                    existing = data["opencode_defaults"]
                    if isinstance(existing, dict):
                        existing.update(raw)
                    else:
                        data["opencode_defaults"] = raw
            except ValueError as exc:
                print(f"Error: Invalid --opencode-defaults JSON: {exc}")
                sys.exit(1)

        engine = ExecutionEngine(
            config=cfg,
            no_log_file=args.no_log_file,
            log_file=args.log_file,
        )
        engine.execute_workflow_data(data)

        state = engine.state
        print(f"\nWorkflow finished: {state.termination_reason}")
        print(f"  Iterations: {state.iteration}")
        print(f"  Is complete: {state.is_complete}")
        if state.termination_reason == "completed":
            sys.exit(0)
        else:
            sys.exit(1)
    except ImportError as exc:
        print(f"Error: Missing core module — {exc}")
        sys.exit(1)


def _run_gui(args: argparse.Namespace, config) -> None:
    try:
        from ui.app import WorkflowApp
    except ImportError as exc:
        print(f"Error: Cannot start GUI — {exc}")
        print("Install Tkinter or use --cli mode")
        sys.exit(1)

    app = WorkflowApp(
        config_path=args.config,
        workflow_path=args.workflow,
        workdir=args.workdir,
        init_script=args.init_script,
        opencode_defaults_raw=args.opencode_defaults,
        fullscreen=args.fullscreen,
        layout=args.layout,
        no_log_file=args.no_log_file,
        log_file=args.log_file,
    )
    try:
        app.run()
    except KeyboardInterrupt:
        app.on_closing()


if __name__ == "__main__":
    main()
