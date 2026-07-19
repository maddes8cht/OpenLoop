import json
import os
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
    HORIZONTAL,
    VERTICAL,
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
    def __init__(
        self,
        config_path: Optional[str] = None,
        workflow_path: Optional[str] = None,
        workdir: Optional[str] = None,
        init_script: Optional[str] = None,
        opencode_defaults_raw: Optional[str] = None,
    ) -> None:
        self._root = Tk()
        self._root.title("OpenLoop — Workflow Builder")
        self._root.geometry("1024x720")
        self._root.minsize(1024, 600)

        self._config_path = config_path
        self._config = None
        self._engine = None
        self._workflow_path: Optional[str] = None
        self._execution_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._running = False
        self._log_queue: queue.Queue = queue.Queue()
        self._opencode_defaults_raw = opencode_defaults_raw

        self._build_ui()
        self._root.after_idle(self._init_preview_collapsed)
        self._root.after_idle(self._init_log_collapsed)
        self._load_config()
        self._refresh_agent_list()
        self._poll_log_queue()

        if workflow_path:
            self._load_workflow_from_path(workflow_path)
        if workdir:
            self._workdir_var.set(str(Path(workdir).resolve()))
        if init_script:
            self._init_script_var.set(init_script)
        self._update_title()

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
        Label(toolbar, text="  WF:").pack(side=LEFT)
        self._workflow_path_var = StringVar()
        wf_entry = Entry(toolbar, textvariable=self._workflow_path_var)
        wf_entry.pack(side=LEFT, fill="x", expand=True, padx=2)
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

        self._log_collapsed = BooleanVar(value=True)
        self._log_toggle_btn = Button(toolbar, text="Log ▼", width=6, command=self._toggle_log)
        self._log_toggle_btn.pack(side=LEFT, padx=2)

        self._preview_collapsed = BooleanVar(value=True)
        self._preview_toggle_btn = Button(toolbar, text="Preview ▼", width=8, command=self._toggle_preview)
        self._preview_toggle_btn.pack(side=LEFT, padx=2)

        # Main content area + Log in a vertical PanedWindow
        self._root_paned = ttk.PanedWindow(self._root, orient=VERTICAL)
        self._root_paned.grid(row=1, column=0, sticky=(N, S, W, E), padx=4, pady=2)
        self._log_ratio = None
        self._root_paned.bind("<ButtonRelease-1>", self._on_log_sash_drag)

        # Top pane: Content (Agent Pool + Builder + Preview)
        content = Frame(self._root_paned)
        content.columnconfigure(0, weight=0)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        # Column 0: Agent Pool (fixed-ish width)
        agent_frame = ttk.LabelFrame(content, text="Agent Pool", padding=4)
        agent_frame.grid(row=0, column=0, sticky=(N, S, W), padx=2)
        agent_frame.rowconfigure(0, weight=1)

        self._agent_listbox = Listbox(agent_frame, width=22)
        self._agent_listbox.grid(row=0, column=0, sticky=(N, S, W, E), pady=2)
        agent_scroll = Scrollbar(agent_frame, command=self._agent_listbox.yview)
        self._agent_listbox.configure(yscrollcommand=agent_scroll.set)
        agent_scroll.grid(row=0, column=1, sticky=(N, S))
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

        # Column 1: Workflow Builder + Preview in a PanedWindow (horizontal)
        self._main_paned = ttk.PanedWindow(content, orient=HORIZONTAL)
        self._main_paned.grid(row=0, column=1, sticky=(N, S, W, E), padx=2)
        self._preview_ratio = None
        self._main_paned.bind("<ButtonRelease-1>", self._on_sash_drag)

        # Left pane: Workflow Builder
        builder = Frame(self._main_paned)
        builder.columnconfigure(0, weight=1)
        builder.rowconfigure(0, weight=0)
        builder.rowconfigure(1, weight=1)
        builder.rowconfigure(2, weight=0)
        builder.rowconfigure(3, weight=0)

        self._main_paned.add(builder, weight=3)

        self._build_zone(
            builder, "Preparation Agent", "prep", 0, listbox_height=4
        )
        self._build_zone(builder, "Loop Agents", "loop", 1)
        self._build_zone(
            builder, "Finalization Agent", "final", 2, listbox_height=4
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

        sep = ttk.Separator(config_frame, orient="horizontal")
        sep.grid(row=row, column=0, columnspan=2, sticky=(W, E), pady=4)
        row += 1

        Label(config_frame, text="Workdir:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        wd_frame = Frame(config_frame)
        wd_frame.grid(row=row, column=1, sticky=(W, E), padx=4)
        wd_frame.columnconfigure(0, weight=1)
        self._workdir_var = StringVar(value=os.getcwd())
        Entry(wd_frame, textvariable=self._workdir_var).pack(
            side=LEFT, fill="x", expand=True
        )
        Button(
            wd_frame, text="Browse", command=self._browse_workdir
        ).pack(side=LEFT, padx=1)
        row += 1

        Label(config_frame, text="Init Script:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        is_frame = Frame(config_frame)
        is_frame.grid(row=row, column=1, sticky=(W, E), padx=4)
        is_frame.columnconfigure(0, weight=1)
        self._init_script_var = StringVar()
        Entry(is_frame, textvariable=self._init_script_var).pack(
            side=LEFT, fill="x", expand=True
        )
        Button(
            is_frame, text="Browse", command=self._browse_init_script
        ).pack(side=LEFT, padx=1)
        row += 1

        sep2 = ttk.Separator(config_frame, orient="horizontal")
        sep2.grid(row=row, column=0, columnspan=2, sticky=(W, E), pady=4)
        row += 1

        Label(config_frame, text="Model:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        self._oc_model_var = StringVar()
        Entry(config_frame, textvariable=self._oc_model_var, width=24).grid(
            row=row, column=1, sticky=W, padx=4
        )
        row += 1

        Label(config_frame, text="Agent:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        self._oc_agent_var = StringVar()
        Entry(
            config_frame, textvariable=self._oc_agent_var, width=24
        ).grid(row=row, column=1, sticky=W, padx=4)
        row += 1

        Label(config_frame, text="Variant:").grid(
            row=row, column=0, sticky=W, pady=2
        )
        self._oc_variant_var = StringVar()
        Entry(
            config_frame, textvariable=self._oc_variant_var, width=24
        ).grid(row=row, column=1, sticky=W, padx=4)
        row += 1

        self._oc_pure_var = BooleanVar(value=False)
        Checkbutton(
            config_frame,
            text="Pure Mode (no plugins)",
            variable=self._oc_pure_var,
        ).grid(row=row, column=0, columnspan=2, sticky=W, pady=2)
        row += 1

        # Right pane: Agent Preview (always added to paned window)
        self._preview_frame = ttk.LabelFrame(self._main_paned, text="Agent Preview", padding=4)
        self._preview_frame.rowconfigure(0, weight=1)
        self._preview_frame.columnconfigure(0, weight=1)

        self._preview_text = Text(self._preview_frame, wrap="word", state="disabled")
        preview_scroll = Scrollbar(
            self._preview_frame, command=self._preview_text.yview
        )
        self._preview_text.configure(yscrollcommand=preview_scroll.set)

        self._preview_text.grid(row=0, column=0, sticky=(N, S, W, E))
        preview_scroll.grid(row=0, column=1, sticky=(N, S))

        self._preview_text.configure(state="normal")
        self._preview_text.insert("1.0", "Select an agent to preview")
        self._preview_text.configure(state="disabled")

        self._main_paned.add(self._preview_frame, weight=1)
        # Collapse preview on first idle event (after layout is complete)

        self._root_paned.add(content, weight=3)

        # Bottom: Log (always in paned window, collapsed via sash position)
        self._log_frame = ttk.LabelFrame(
            self._root_paned, text="Execution Log", padding=4
        )
        self._log_frame.rowconfigure(0, weight=1)
        self._log_frame.columnconfigure(0, weight=1)

        self._log_text = Text(
            self._log_frame, height=10, wrap="word", state="disabled"
        )
        log_scroll = Scrollbar(self._log_frame, command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_scroll.set)

        self._log_text.grid(row=0, column=0, sticky=(N, S, W, E))
        log_scroll.grid(row=0, column=1, sticky=(N, S))

        self._root_paned.add(self._log_frame, weight=1)

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
        scroll = Scrollbar(frame, command=listbox.yview)
        listbox.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky=(N, S))
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

        from core.markdown_renderer import render as md_render

        content = agent_path.read_text(encoding="utf-8")
        md_render(self._preview_text, content)

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
        wd = self._workdir_var.get().strip()
        if wd:
            data["workdir"] = wd
        iscript = self._init_script_var.get().strip()
        if iscript:
            data["init_script"] = iscript

        oc_defaults: dict = {}
        model = self._oc_model_var.get().strip()
        if model:
            oc_defaults["model"] = model
        agent = self._oc_agent_var.get().strip()
        if agent:
            oc_defaults["agent"] = agent
        variant = self._oc_variant_var.get().strip()
        if variant:
            oc_defaults["variant"] = variant
        if self._oc_pure_var.get():
            oc_defaults["pure"] = True
        if oc_defaults:
            data["opencode_defaults"] = oc_defaults

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
        if "workdir" in data and data["workdir"]:
            self._workdir_var.set(str(Path(data["workdir"]).resolve()))
        if "init_script" in data:
            self._init_script_var.set(data["init_script"] or "")

        oc_defaults = data.get("opencode_defaults", {})
        if isinstance(oc_defaults, dict):
            self._oc_model_var.set(str(oc_defaults.get("model", "")))
            self._oc_agent_var.set(str(oc_defaults.get("agent", "")))
            self._oc_variant_var.set(str(oc_defaults.get("variant", "")))
            self._oc_pure_var.set(bool(oc_defaults.get("pure", False)))

    def _load_workflow_from_path(self, path: str) -> None:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._load_workflow_into_ui(data)
            self._workflow_path = path
            self._workflow_path_var.set(path)
            self._update_title()
            self._log(f"Loaded workflow: {path}")
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            if self._workflow_path:
                messagebox.showerror("Error", str(exc))

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
        self._load_workflow_from_path(path)

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

    def _update_title(self) -> None:
        wd = self._workdir_var.get().strip()
        title = "OpenLoop — Workflow Builder"
        if wd:
            title += f" — {wd}"
        self._root.title(title)

    def _browse_workdir(self) -> None:
        path = filedialog.askdirectory(
            title="Select Working Directory",
        )
        if path:
            self._workdir_var.set(path)
            self._update_title()

    def _browse_init_script(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Init Script",
            filetypes=[
                ("Script files", "*.ps1 *.bat *.cmd *.sh"),
                ("PowerShell", "*.ps1"),
                ("Batch", "*.bat *.cmd"),
                ("Shell", "*.sh"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._init_script_var.set(path)

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
        if self._opencode_defaults_raw:
            try:
                import json as _json
                raw = _json.loads(self._opencode_defaults_raw)
                if isinstance(raw, dict):
                    data.setdefault("opencode_defaults", {})
                    existing = data["opencode_defaults"]
                    if isinstance(existing, dict):
                        existing.update(raw)
                    else:
                        data["opencode_defaults"] = raw
            except ValueError:
                pass
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

    def _toggle_log(self) -> None:
        if self._log_collapsed.get():
            self._log_collapsed.set(False)
            self._log_toggle_btn.configure(text="Log ▲")
            self._restore_log_ratio()
        else:
            self._save_log_ratio()
            self._log_collapsed.set(True)
            self._log_toggle_btn.configure(text="Log ▼")
            self._collapse_log()

    def _init_log_collapsed(self) -> None:
        self._log_ratio = None
        self._collapse_log()

    def _save_log_ratio(self) -> None:
        total = self._root_paned.winfo_height()
        if total > 50:
            pos = self._root_paned.sashpos(0)
            if 50 < pos < total - 50:
                self._log_ratio = pos / total

    def _restore_log_ratio(self) -> None:
        if self._log_collapsed.get():
            return
        self._root_paned.update_idletasks()
        total = self._root_paned.winfo_height()
        if total < 50:
            self._root_paned.after(20, self._restore_log_ratio)
            return
        if self._log_ratio is not None:
            pos = int(total * self._log_ratio)
        else:
            pos = int(total * 0.7)
        if pos < 100:
            pos = int(total * 0.7)
        if pos > total - 20:
            pos = int(total * 0.7)
        self._root_paned.sashpos(0, pos)

    def _collapse_log(self) -> None:
        if not self._log_collapsed.get():
            return
        self._root_paned.update_idletasks()
        total = self._root_paned.winfo_height()
        if total < 50:
            self._root_paned.after(20, self._collapse_log)
            return
        self._root_paned.sashpos(0, total - 2)

    def _on_log_sash_drag(self, event) -> None:
        total = self._root_paned.winfo_height()
        if total > 50:
            pos = self._root_paned.sashpos(0)
            if 50 < pos < total - 50:
                self._log_ratio = pos / total

    def _toggle_preview(self) -> None:
        if self._preview_collapsed.get():
            self._preview_collapsed.set(False)
            self._preview_toggle_btn.configure(text="Preview ▲")
            self._restore_preview_ratio()
        else:
            self._save_preview_ratio()
            self._preview_collapsed.set(True)
            self._preview_toggle_btn.configure(text="Preview ▼")
            self._collapse_preview()

    def _init_preview_collapsed(self) -> None:
        self._preview_ratio = None
        self._collapse_preview()

    def _save_preview_ratio(self) -> None:
        total = self._main_paned.winfo_width()
        if total > 50:
            pos = self._main_paned.sashpos(0)
            if 50 < pos < total - 50:
                self._preview_ratio = pos / total

    def _restore_preview_ratio(self) -> None:
        if self._preview_collapsed.get():
            return
        self._main_paned.update_idletasks()
        total = self._main_paned.winfo_width()
        if total < 50:
            self._main_paned.after(20, self._restore_preview_ratio)
            return
        if self._preview_ratio is not None:
            pos = int(total * self._preview_ratio)
        else:
            pos = int(total * 0.75)
        if pos < 150:
            pos = int(total * 0.75)
        if pos > total - 10:
            pos = int(total * 0.75)
        self._main_paned.sashpos(0, pos)

    def _collapse_preview(self) -> None:
        if not self._preview_collapsed.get():
            return
        self._main_paned.update_idletasks()
        total = self._main_paned.winfo_width()
        if total < 50:
            self._main_paned.after(20, self._collapse_preview)
            return
        self._main_paned.sashpos(0, total - 2)

    def _on_sash_drag(self, event) -> None:
        total = self._main_paned.winfo_width()
        if total > 50:
            pos = self._main_paned.sashpos(0)
            if 50 < pos < total - 50:
                self._preview_ratio = pos / total
