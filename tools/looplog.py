#!/usr/bin/env python3
"""
LoopLog -- Standalone Tkinter viewer for structured OpenLoop log files.

Usage:
    python tools/looplog.py <logfile>
    python tools/looplog.py              # opens file dialog
"""

import json
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Markers ──────────────────────────────────────────────────────────────

BEGIN_MARKER = "##! BEGIN_AGENT_RUN"
END_MARKER = "##! END_AGENT_RUN"

_BEGIN_RE = re.compile(
    rf"{re.escape(BEGIN_MARKER)}\s+"
    r'agent="([^"]*)"\s+'
    r'phase="([^"]*)"\s+'
    r"iteration=(\d+)\s+"
    r'run_id="([^"]*)"'
)
_END_RE = re.compile(re.escape(END_MARKER))

_BANNER_RE = re.compile(r"^={70,}$")
_BANNER_INFO_RE = re.compile(
    r"Agent:\s*(\S+)\s*\|\s*Phase:\s*(\S+)\s*\|\s*Iteration:\s*(\d+)"
)

_MARKER_LINE_RE = re.compile(r"^##!\s+\S+_AGENT_RUN")


# ── Data model ───────────────────────────────────────────────────────────

@dataclass
class Section:
    kind: str
    label: str
    start: int
    end: int
    children: list["Section"] = field(default_factory=list)

    @property
    def has_children(self) -> bool:
        return bool(self.children)


@dataclass
class _RunBoundary:
    agent: str
    phase: str
    iteration: int
    run_id: str
    start: int
    end: int


# ── Parser ───────────────────────────────────────────────────────────────

class LogParser:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines = path.read_text("utf-8").splitlines(keepends=True)
        self.line_count = len(self.lines)
        self._has_markers = any(
            line.startswith(BEGIN_MARKER) for line in self.lines
        )

    def parse(self) -> list[Section]:
        if self._has_markers:
            runs = self._find_runs_marker()
        else:
            runs = self._find_runs_inference()

        sections: list[Section] = []

        # HEAD
        if runs:
            if runs[0].start > 0:
                sections.append(Section("head", "HEAD", 0, runs[0].start))
        else:
            if self.line_count > 0:
                sections.append(Section("head", "HEAD", 0, self.line_count))
            return sections

        # Build contiguous phase blocks (preserving file order)
        blocks: list[tuple[str, list[_RunBoundary]]] = []
        current_phase: Optional[str] = None
        current_block: list[_RunBoundary] = []
        for r in runs:
            if r.phase != current_phase:
                if current_block:
                    blocks.append((current_phase, current_block))
                current_phase = r.phase
                current_block = []
            current_block.append(r)
        if current_block:
            blocks.append((current_phase, current_block))

        for pname, block_runs in blocks:
            first = block_runs[0].start
            last = block_runs[-1].end

            # Group runs within this block by iteration (loop only, >1 iter)
            iter_map: dict[int, list[Section]] = {}
            for r in block_runs:
                agent_sec = Section(
                    "agent", f"Agent: {r.agent}", r.start, r.end
                )
                iter_map.setdefault(r.iteration, []).append(agent_sec)

            is_loop = pname == "loop"
            sorted_iters = sorted(iter_map.items())
            phase_sec = Section("phase", f"Phase: {pname}", first, last)

            iterations_contiguous = True
            seen_end = 0
            for _inum, agent_list in sorted_iters:
                if agent_list[0].start < seen_end:
                    iterations_contiguous = False
                    break
                seen_end = agent_list[-1].end

            if is_loop and len(sorted_iters) > 1 and iterations_contiguous:
                for inum, agent_list in sorted_iters:
                    iter_first = agent_list[0].start
                    iter_last = agent_list[-1].end
                    iter_sec = Section(
                        "iteration",
                        f"Iteration {inum}",
                        iter_first,
                        iter_last,
                        children=agent_list,
                    )
                    phase_sec.children.append(iter_sec)
            else:
                for _inum, agent_list in sorted_iters:
                    phase_sec.children.extend(agent_list)

            sections.append(phase_sec)

        # Make top-level sections contiguous (fill inter-section gaps)
        for i in range(len(sections) - 1):
            sections[i].end = sections[i + 1].start

        # TAIL — everything after the last top-level section
        if sections:
            last_end = sections[-1].end
            if last_end < self.line_count:
                sections.append(Section("tail", "TAIL", last_end, self.line_count))
        elif self.line_count > 0:
            sections.append(Section("head", "HEAD", 0, self.line_count))

        return sections

    # -- Marker-based run detection (new format) --

    def _find_runs_marker(self) -> list[_RunBoundary]:
        runs: list[_RunBoundary] = []
        current: Optional[_RunBoundary] = None

        for i, line in enumerate(self.lines):
            m = _BEGIN_RE.match(line)
            if m:
                current = _RunBoundary(
                    agent=m.group(1),
                    phase=m.group(2),
                    iteration=int(m.group(3)),
                    run_id=m.group(4),
                    start=i,
                    end=-1,
                )
                continue

            if current is not None and _END_RE.match(line):
                current.end = i + 1
                runs.append(current)
                current = None

        return runs

    # -- Inference-based run detection (old format) --

    def _find_runs_inference(self) -> list[_RunBoundary]:
        runs: list[_RunBoundary] = []
        i = 0
        while i < self.line_count:
            stripped = self.lines[i].strip()
            if not _BANNER_RE.match(stripped):
                i += 1
                continue

            # Info line is always the line immediately after the opening banner
            info_match = None
            info_line_idx = i + 1
            if info_line_idx < self.line_count:
                m = _BANNER_INFO_RE.search(self.lines[info_line_idx])
                if m:
                    info_match = m

            if info_match is None:
                i += 1
                continue

            agent = info_match.group(1)
            phase = info_match.group(2)
            iteration = int(info_match.group(3))
            rid_match = re.search(r"Run ID:\s*(\S+)", self.lines[info_line_idx])
            run_id = rid_match.group(1) if rid_match else ""

            end = self.line_count
            for k in range(i + 1, self.line_count):
                if _BANNER_RE.match(self.lines[k].strip()):
                    # Check if this banner is an *opening* banner
                    # (info line must be immediately after)
                    next_line = k + 1
                    if next_line < self.line_count and _BANNER_INFO_RE.search(self.lines[next_line]):
                        end = k
                        break

            runs.append(_RunBoundary(agent, phase, iteration, run_id, i, end))
            i = end

        return runs

    def get_raw_text(self, start: int, end: int, strip_markers: bool = True) -> str:
        if not strip_markers:
            return "".join(self.lines[start:end])

        out: list[str] = []
        for line in self.lines[start:end]:
            if not _MARKER_LINE_RE.match(line):
                out.append(line)
        return "".join(out)

    def get_full_text(self, strip_markers: bool = True) -> str:
        return self.get_raw_text(0, self.line_count, strip_markers)


# ── GUI ──────────────────────────────────────────────────────────────────

class LoopLogApp:
    _MIN_WIN_W = 900
    _MIN_WIN_H = 500
    _DEF_WIN_W = 1200
    _DEF_WIN_H = 750

    def __init__(self, path: Optional[Path] = None) -> None:
        self.root = tk.Tk()
        self.root.title("LoopLog")
        self.root.minsize(self._MIN_WIN_W, self._MIN_WIN_H)
        self.root.geometry(f"{self._DEF_WIN_W}x{self._DEF_WIN_H}")

        self.parser: Optional[LogParser] = None
        self.sections: list[Section] = []
        self._path: Optional[Path] = path
        self._sec_map: dict[str, Section] = {}

        self._build_ui()
        self._setup_bindings()

        if path:
            self.load_log(path)

    # -- UI construction --

    def _build_ui(self) -> None:
        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open…", command=self._open_file, accelerator="Ctrl+O")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        self.root.config(menu=menubar)

        # Top bar: current file label + All toggle
        top = ttk.Frame(self.root, padding=(8, 4))
        top.pack(fill=tk.X)

        self._file_label = ttk.Label(top, text="No file loaded", foreground="gray")
        self._file_label.pack(side=tk.LEFT)

        self._show_all = tk.BooleanVar(value=False)
        ttk.Button(top, text="Refresh", command=self._refresh).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Checkbutton(
            top, text="Show entire file", variable=self._show_all,
            command=self._on_show_all
        ).pack(side=tk.RIGHT)

        # Main paned window
        self._paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self._paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Left: Treeview
        left_frame = ttk.Frame(self._paned)
        self._tree = ttk.Treeview(
            left_frame, columns=(), show="tree",
            selectmode="browse"
        )
        tree_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._paned.add(left_frame, weight=1)

        # Right: Text widget
        right_frame = ttk.Frame(self._paned)
        self._text = tk.Text(
            right_frame, wrap=tk.NONE, state=tk.DISABLED,
            font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white"
        )
        text_scroll_y = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self._text.yview)
        text_scroll_x = ttk.Scrollbar(right_frame, orient=tk.HORIZONTAL, command=self._text.xview)
        self._text.configure(
            yscrollcommand=text_scroll_y.set,
            xscrollcommand=text_scroll_x.set,
        )
        self._text.grid(row=0, column=0, sticky="nsew")
        text_scroll_y.grid(row=0, column=1, sticky="ns")
        text_scroll_x.grid(row=1, column=0, sticky="ew")
        right_frame.grid_rowconfigure(0, weight=1)
        right_frame.grid_columnconfigure(0, weight=1)
        self._paned.add(right_frame, weight=3)

    def _setup_bindings(self) -> None:
        self._tree.bind("<<TreeviewSelect>>", self._on_select)
        self.root.bind("<Control-o>", lambda e: self._open_file())
        self.root.bind("<Control-O>", lambda e: self._open_file())
        self.root.bind("<F5>", lambda e: self._refresh())

    # -- File loading --

    def _open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select OpenLoop log file",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if path:
            self.load_log(Path(path))

    def _refresh(self) -> None:
        if self._path is not None:
            self.load_log(self._path)

    def load_log(self, path: Path) -> None:
        if not path.is_file():
            messagebox.showerror("Error", f"File not found:\n{path}")
            return

        try:
            self.parser = LogParser(path)
            self.sections = self.parser.parse()
        except Exception as exc:
            messagebox.showerror("Parse Error", str(exc))
            return

        self._path = path
        self._file_label.config(text=str(path.resolve()))
        self._rebuild_tree()
        self._show_all.set(False)

    # -- Tree population --

    def _rebuild_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.config(state=tk.DISABLED)

        if not self.sections:
            return

        for sec in self.sections:
            self._insert_node("", sec)

        # Select the first section (HEAD) by default
        first = self._tree.get_children()
        if first:
            self._tree.selection_set(first[0])
            self._tree.see(first[0])
            self._display_section(self.sections[0])

    def _insert_node(self, parent: str, sec: Section) -> str:
        node_id = self._tree.insert(parent, tk.END, text=sec.label, open=True)
        self._sec_map[node_id] = sec
        for child in sec.children:
            self._insert_node(node_id, child)
        return node_id

    # -- Selection handling --

    def _on_select(self, _event: Optional[tk.Event] = None) -> None:
        if self._show_all.get():
            return
        sel = self._tree.selection()
        if not sel:
            return
        sec = self._sec_map.get(sel[0])
        if sec is not None:
            self._display_section(sec)

    def _display_section(self, sec: Section) -> None:
        if self.parser is None:
            return
        text = self.parser.get_raw_text(sec.start, sec.end)
        self._set_text(text)

    def _on_show_all(self) -> None:
        if self.parser is None:
            return
        if self._show_all.get():
            self._set_text(self.parser.get_full_text(strip_markers=False))
        else:
            sel = self._tree.selection()
            if not sel:
                first = self._tree.get_children()
                if first:
                    self._tree.selection_set(first[0])
            self._on_select(None)

    def _set_text(self, text: str) -> None:
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._text.config(state=tk.DISABLED)

    # -- Main loop --

    def run(self) -> None:
        self.root.mainloop()


# ── CLI entry point ──────────────────────────────────────────────────────

def main() -> None:
    path: Optional[Path] = None
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    app = LoopLogApp(path)
    app.run()


if __name__ == "__main__":
    main()
