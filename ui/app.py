import json
import queue
import threading
from pathlib import Path
from tkinter import (
    END,
    LEFT,
    N,
    S,
    W,
    E,
    BooleanVar,
    Button,
    Checkbutton,
    Entry,
    Frame,
    Label,
    Listbox,
    Scrollbar,
    StringVar,
    Text,
    Tk,
    filedialog,
    messagebox,
    ttk,
)
from typing import Optional


class WorkflowApp:
    def __init__(self, config_path: str = "config.json") -> None:
        self._root = Tk()
        self._root.title("OpenLoop — Workflow Builder")
        self._root.geometry("1100x700")
        self._root.minsize(1000, 800)

        self._config_path = config_path
        self._config = None
        self._engine = None
        self._workflow_path: Optional[str] = None
        self._execution_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._load_config()
        self._refresh_agent_list()
        self._poll_log_queue()

    # ---- Public API ----

    def run(self) -> None:
        self._root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._root.mainloop()

    def on_closing(self) -> None:
        if self._running:
            self._stop_execution()
        self._root.destroy()

    # ---- UI Construction ----

    def _build_ui(self) -> None:
        self._root.columnconfigure(0, weight=1)
        self._root.rowconfigure(1, weight=1)

        toolbar = Frame(self._root)
        toolbar.grid(row=0, column=0, sticky=(W, E), padx=4, pady=2)
        toolbar.columnconfigure(5, weight=1)

        Button(toolbar, text="Load Workflow", command=self._load_workflow).pack(
            side=LEFT, padx=2
        )
        Button(toolbar, text="Save Workflow", command=self._save_workflow).pack(
            side=LEFT, padx=2
        )
        Label(toolbar, text="  |  ").pack(side=LEFT)
        self._start_btn = Button(
            toolbar, text="Start Execution", command=self._start_execution
        )
        self._start_btn.pack(side=LEFT, padx=2)
        self._stop_btn = Button(
            toolbar,
            text="Stop Execution",
            command=self._stop_execution,
            state="disabled",
        )
        self._stop_btn.pack(side=LEFT, padx=2)

        # Main content area
        content = Frame(self._root)
        content.grid(row=1, column=0, sticky=(N, S, W, E), padx=4, pady=2)
        content.columnconfigure(2, weight=1)
        content.rowconfigure(0, weight=1)

        # Column 0: Agent Pool
        agent_frame = ttk.LabelFrame(content, text="Agent Pool", padding=4)
        agent_frame.grid(row=0, column=0, sticky=(N, S, W), padx=2)
        agent_frame.rowconfigure(0, weight=1)

        self._agent_listbox = Listbox(agent_frame, width=22)
        self._agent_listbox.grid(row=0, column=0, sticky=(N, S, W, E), pady=2)
        self._agent_listbox.bind("<<ListboxSelect>>", lambda e: self._show_preview(self._agent_listbox))

        agent_btn_frame = Frame(agent_frame)
        agent_btn_frame.grid(row=1, column=0, pady=2)
        Button(
            agent_btn_frame,
            text="→ Prep",
            width=8,
            command=lambda: self._add_to_zone("prep"),
        ).pack(side=LEFT, padx=1)
        Button(
            agent_btn_frame,
            text="→ Loop",
            width=8,
            command=lambda: self._add_to_zone("loop"),
        ).pack(side=LEFT, padx=1)
        Button(
            agent_btn_frame,
            text="→ Final",
            width=8,
            command=lambda: self._add_to_zone("final"),
        ).pack(side=LEFT, padx=1)

        # Column 1: Workflow Builder
        builder = Frame(content)
        builder.grid(row=0, column=1, sticky=(N, S, W, E), padx=2)
        builder.columnconfigure(0, weight=1)
        builder.rowconfigure(0, weight=0)
        builder.rowconfigure(1, weight=1)
        builder.rowconfigure(2, weight=0)

        self._build_zone(
            builder, "Preparation Agent", "prep", 0, listbox_height=5
        )
        self._build_zone(builder, "Loop Agents", "loop", 1)
        self._build_zone(
            builder, "Finalization Agent", "final", 2, listbox_height=5
        )

        # Configuration (below Finalization in builder column)
        config_frame = ttk.LabelFrame(
            builder, text="Configuration", padding=4
        )
        config_frame.grid(row=3, column=0, sticky=(W, E), pady=2)
        config_frame.columnconfigure(1, weight=1)

        row = 0
        Label(config_frame, text="Max Loops:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        self._max_loops_var = StringVar(value="10")
        Entry(config_frame, textvariable=self._max_loops_var, width=10).grid(
            row=row, column=1, sticky=W, padx=4
        )
        row += 1

        Label(config_frame, text="End Condition:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        self._end_condition_var = StringVar(value="is_complete == True")
        Entry(
            config_frame, textvariable=self._end_condition_var, width=24
        ).grid(row=row, column=1, sticky=W, padx=4)
        row += 1

        self._finalize_on_abort_var = BooleanVar(value=False)
        Checkbutton(
            config_frame,
            text="Finalize on Abort",
            variable=self._finalize_on_abort_var,
        ).grid(row=row, column=0, columnspan=2, sticky=W, pady=2)
        row += 1

        # Workflow file
        sep = ttk.Separator(config_frame, orient="horizontal")
        sep.grid(row=row, column=0, columnspan=2, sticky=(W, E), pady=6)
        row += 1

        Label(config_frame, text="Workflow File:").grid(
            row=row, column=0, columnspan=2, sticky=W, pady=1
        )
        row += 1

        wf_file_frame = Frame(config_frame)
        wf_file_frame.grid(row=row, column=0, columnspan=2, sticky=(W, E))
        wf_file_frame.columnconfigure(0, weight=1)

        self._workflow_path_var = StringVar()
        Entry(
            wf_file_frame, textvariable=self._workflow_path_var
        ).pack(side=LEFT, fill="x", expand=True)
        Button(
            wf_file_frame, text="Browse", command=self._browse_workflow
        ).pack(side=LEFT, padx=2)

        # Column 2: Agent Preview
        preview_frame = ttk.LabelFrame(content, text="Agent Preview", padding=4)
        preview_frame.grid(row=0, column=2, sticky=(N, S, W, E), padx=2)
        preview_frame.rowconfigure(0, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        self._preview_text = Text(preview_frame, wrap="word", state="disabled")
        preview_scroll = Scrollbar(
            preview_frame, command=self._preview_text.yview
        )
        self._preview_text.configure(yscrollcommand=preview_scroll.set)

        self._preview_text.grid(row=0, column=0, sticky=(N, S, W, E))
        preview_scroll.grid(row=0, column=1, sticky=(N, S))

        self._preview_text.configure(state="normal")
        self._preview_text.insert("1.0", "Select an agent to preview")
        self._preview_text.configure(state="disabled")

        # Bottom: Log
        log_frame = ttk.LabelFrame(
            self._root, text="Execution Log", padding=4
        )
        log_frame.grid(
            row=2, column=0, sticky=(N, S, W, E), padx=4, pady=2
        )
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log_text = Text(
            log_frame, height=10, wrap="word", state="disabled"
        )
        log_scroll = Scrollbar(log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll.set)

        self._log_text.grid(row=0, column=0, sticky=(N, S, W, E))
        log_scroll.grid(row=0, column=1, sticky=(N, S))

    def _build_zone(
        self,
        parent: Frame,
        label: str,
        zone: str,
        row: int,
        listbox_height: int = 10,
    ) -> None:
        frame = ttk.LabelFrame(parent, text=label, padding=4)
        frame.grid(row=row, column=0, sticky=(N, S, W, E), pady=2)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        listbox = Listbox(frame, width=22, height=listbox_height)
        listbox.grid(row=0, column=0, sticky=(N, S, W, E))
        listbox.bind("<<ListboxSelect>>", lambda e, lb=listbox: self._show_preview(lb))
        setattr(self, f"_{zone}_listbox", listbox)

        btn_frame = Frame(frame)
        btn_frame.grid(row=1, column=0, pady=2)

        def make_remove(z=zone):
            return lambda: self._remove_from_zone(z)

        Button(btn_frame, text="Remove", width=7, command=make_remove()).pack(
            side=LEFT, padx=1
        )

        Button(
            btn_frame,
            text="▲ Up",
            width=5,
            command=lambda z=zone: self._move_agent(z, -1),
        ).pack(side=LEFT, padx=1)
        Button(
            btn_frame,
            text="▼ Down",
            width=5,
            command=lambda z=zone: self._move_agent(z, 1),
        ).pack(side=LEFT, padx=1)

    # ---- Agent / Zone Management ----

    def _refresh_agent_list(self) -> None:
        self._agent_listbox.delete(0, END)
        if self._config is None:
            return
        try:
            from core.agent import AgentLoader

            loader = AgentLoader(self._config.agents_dir)
            for name in loader.list_agents():
                self._agent_listbox.insert(END, name)
        except ImportError:
            pass

    def _show_preview(self, source: Listbox) -> None:
        for lb in [
            self._agent_listbox,
            self._prep_listbox,
            self._loop_listbox,
            self._final_listbox,
        ]:
            if lb != source:
                lb.selection_clear(0, END)

        sel = source.curselection()
        if not sel:
            self._preview_text.configure(state="normal")
            self._preview_text.delete("1.0", END)
            self._preview_text.insert("1.0", "Select an agent to preview")
            self._preview_text.configure(state="disabled")
            return

        name = source.get(sel[0])
        if self._config is None:
            return
        agent_path = Path(self._config.agents_dir) / f"{name}.md"
        if not agent_path.exists():
            self._preview_text.configure(state="normal")
            self._preview_text.delete("1.0", END)
            self._preview_text.insert("1.0", f"File not found: {agent_path}")
            self._preview_text.configure(state="disabled")
            return

        content = agent_path.read_text(encoding="utf-8")
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", END)
        self._preview_text.insert("1.0", content)
        self._preview_text.configure(state="disabled")

    def _add_to_zone(self, zone: str) -> None:
        sel = self._agent_listbox.curselection()
        if not sel:
            return
        name = self._agent_listbox.get(sel[0])
        listbox = getattr(self, f"_{zone}_listbox")
        listbox.insert(END, name)

    def _remove_from_zone(self, zone: str) -> None:
        listbox = getattr(self, f"_{zone}_listbox")
        sel = listbox.curselection()
        if sel:
            listbox.delete(sel[0])

    def _move_agent(self, zone: str, direction: int) -> None:
        lb = getattr(self, f"_{zone}_listbox")
        sel = lb.curselection()
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= lb.size():
            return
        name = lb.get(idx)
        lb.delete(idx)
        lb.insert(new_idx, name)
        lb.selection_set(new_idx)

    # ---- Workflow Load / Save ----

    def _get_workflow_data(self) -> dict:
        data: dict = {}
        data["preparation_agents"] = list(self._prep_listbox.get(0, END))
        data["loop_agents"] = list(self._loop_listbox.get(0, END))
        data["finalization_agents"] = list(self._final_listbox.get(0, END))
        try:
            data["max_loops"] = int(self._max_loops_var.get())
        except ValueError:
            data["max_loops"] = 10
        data["end_state_condition"] = self._end_condition_var.get()
        data["finalize_on_abort"] = bool(
            self._finalize_on_abort_var.get()
        )
        return data

    def _load_workflow_into_ui(self, data: dict) -> None:
        self._prep_listbox.delete(0, END)
        self._loop_listbox.delete(0, END)
        self._final_listbox.delete(0, END)

        prep = data.get("preparation_agents")
        if prep is None:
            prep = data.get("preparation_agent")
        if isinstance(prep, str):
            self._prep_listbox.insert(END, prep)
        elif prep:
            for agent in prep:
                self._prep_listbox.insert(END, agent)

        for agent in data.get("loop_agents", []):
            self._loop_listbox.insert(END, agent)

        final = data.get("finalization_agents")
        if final is None:
            final = data.get("finalization_agent")
        if isinstance(final, str):
            self._final_listbox.insert(END, final)
        elif final:
            for agent in final:
                self._final_listbox.insert(END, agent)

        self._max_loops_var.set(str(data.get("max_loops", 10)))
        self._end_condition_var.set(
            str(
                data.get(
                    "end_state_condition", "is_complete == True"
                )
            )
        )
        self._finalize_on_abort_var.set(
            bool(data.get("finalize_on_abort", False))
        )

    def _load_workflow(self) -> None:
        path = filedialog.askopenfilename(
            title="Load Workflow",
            filetypes=[("JSON files", "*.json")],
            initialdir=self._config.workflows_dir
            if self._config
            else "./workflows",
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._load_workflow_into_ui(data)
            self._workflow_path = path
            self._workflow_path_var.set(path)
            self._log(f"Loaded workflow: {path}")
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            messagebox.showerror("Error", str(exc))

    def _save_workflow(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save Workflow",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialdir=self._config.workflows_dir
            if self._config
            else "./workflows",
        )
        if not path:
            return
        data = self._get_workflow_data()
        Path(path).write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        self._workflow_path = path
        self._workflow_path_var.set(path)
        self._log(f"Saved workflow: {path}")

    def _browse_workflow(self) -> None:
        self._load_workflow()

    # ---- Configuration ----

    def _load_config(self) -> None:
        try:
            from core.config import Config

            self._config = Config.load(self._config_path)
        except ImportError:
            self._config = None
        except Exception as exc:
            self._log(f"Config warning: {exc}")
            self._config = None

    # ---- Execution ----

    def _start_execution(self) -> None:
        if self._running:
            return

        data = self._get_workflow_data()
        if not data.get("loop_agents"):
            messagebox.showwarning(
                "No Loop Agents",
                "Add at least one agent to the Loop zone.",
            )
            return

        try:
            from core.config import Config
            from core.engine import ExecutionEngine

            cfg = self._config if isinstance(self._config, Config) else Config()
            self._stop_event.clear()
            self._engine = ExecutionEngine(
                config=cfg, logger=self._log, stop_event=self._stop_event
            )
        except ImportError as exc:
            messagebox.showerror("Error", f"Missing core module: {exc}")
            return

        self._running = True
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")

        self._log("--- Execution started ---")
        self._execution_thread = threading.Thread(
            target=self._run_engine,
            args=(data,),
            daemon=True,
        )
        self._execution_thread.start()

    def _run_engine(self, workflow_data: dict) -> None:
        try:
            self._engine.execute_workflow_data(workflow_data)
            state = self._engine.state
            self._log(
                f"--- Execution finished: {state.termination_reason} ---"
            )
            self._log(
                f"  Iterations: {state.iteration}, "
                f"Complete: {state.is_complete}"
            )
        except Exception as exc:
            self._log(f"Execution error: {exc}")
        finally:
            self._log_queue.put(("__done__", None))

    def _stop_execution(self) -> None:
        self._log("Stop requested — finishing current agent...")
        self._stop_event.set()
        self._running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

    def _execution_done(self) -> None:
        self._running = False
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")

    # ---- Logging ----

    def _log(self, message: str) -> None:
        self._log_queue.put(("msg", f"[OpenLoop] {message}"))

    def _poll_log_queue(self) -> None:
        while True:
            try:
                kind, data = self._log_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "__done__":
                self._execution_done()
            elif kind == "msg":
                self._log_text.configure(state="normal")
                self._log_text.insert(END, data + "\n")
                self._log_text.see(END)
                self._log_text.configure(state="disabled")

        self._root.after(100, self._poll_log_queue)
