#!/usr/bin/env python3
"""
LoopLog -- Standalone Tkinter viewer for structured OpenLoop log files.

Usage:
    python tools/looplog.py <logfile> [--hide-system-tags] [--omit-stderr]
                           [--omit-state] [--omit-state-update]
                           [--show-entire-file] [--wrap-lines]
                           [--filter {all,stdout,stderr,system,state_update,state}]
                           [--watch]
    python tools/looplog.py              # opens file dialog
"""

import argparse
import json
import re
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List

# Make the shared flow-status module importable when run via
# ``python tools/looplog.py`` (the repo root is not on sys.path then).
try:
    from ui import status as status
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ui import status as status


# ── Markers (legacy formats) ─────────────────────────────────────────────

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
    kind: str  # "xml" or legacy: "head", "phase", "agent", "iteration", "tail"
    tag: str   # XML tag / legacy kind
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

# Structural XML tags emitted by the engine. `state_update` is NOT structural:
# it is agent content written verbatim inside <stdout>. `state` is structural:
# it holds the effective workflow state the agent is about to receive.
_STRUCTURAL_TAGS = {"openloop_log", "iteration", "agent", "stdout", "stderr", "system", "state"}

# XML tags recognized as sections (structural tags + agent state updates so
# the "state_update" filter can find them).
_ALLOWED_TAGS = _STRUCTURAL_TAGS | {"state_update"}

# Lines that are exactly an engine structural tag (open or close) are stripped
# when displaying raw text. Lines merely *starting* with "<" (tracebacks, HTML,
# agent state updates, comparisons) are content and are preserved.
_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*</?(?:openloop_log|iteration|agent|stdout|stderr|system|state)"
    r"(?:\s+[^>]*)?>\s*$"
)

# Separator drawn above each preview block (multi-select and filter view).
_SEPARATOR = "=" * 60


def _block_header(sec: Section) -> str:
    return f"{_SEPARATOR}\n{sec.label}  (lines {sec.start + 1}-{sec.end})\n"


def _prune_contained(secs: list[Section]) -> list[Section]:
    """Drop sections that are strictly contained in another selected section.

    When a parent node and some of its children are selected together, the
    children are fully covered by the parent's content and would only
    duplicate output in the preview. The parent's selection wins; the covered
    children are ignored in the preview (the treeview selection is untouched).
    """
    pruned: list[Section] = []
    for i, sec in enumerate(secs):
        covered = any(
            j != i
            and other.start <= sec.start
            and sec.end <= other.end
            and (other.start < sec.start or sec.end < other.end)
            for j, other in enumerate(secs)
        )
        if not covered:
            pruned.append(sec)
    return pruned


class LogParser:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lines = path.read_text("utf-8").splitlines(keepends=True)
        self.line_count = len(self.lines)
        self._has_xml = (
            self.line_count > 0
            and self.lines[0].strip().startswith("<openloop_log>")
        )

    def parse(self) -> list[Section]:
        if self._has_xml:
            sections = self._parse_xml()
        else:
            sections = self._parse_legacy()
        self.sections = sections
        self._stderr_lines = self._collect_lines(sections, "stderr")
        self._state_lines = self._collect_lines(sections, "state")
        self._state_update_lines = self._collect_lines(sections, "state_update")
        return sections

    def _collect_lines(
        self, sections: list[Section], tag: str
    ) -> set[int]:
        """Line indices covered by any section with the given tag.

        Used by ``get_raw_text`` to drop content from the preview
        (``omit_stderr``/``omit_state``/``omit_state_update``). These sections
        are always nested inside an ``<agent>``, so no boundary exception (as
        for system tags) is needed.
        """
        covered_lines: set[int] = set()

        def walk(sec: Section) -> None:
            if sec.tag == tag:
                covered_lines.update(range(sec.start, sec.end))
            for child in sec.children:
                walk(child)

        for sec in sections:
            walk(sec)
        return covered_lines

    # -- XML parsing (current format) --

    def _parse_xml(self) -> list[Section]:
        """Parse the log as XML using a resilient stack-based tokenizer.

        The engine wraps everything in well-formed tags, but agent output
        inside <stdout>/<stderr> is arbitrary text (and may itself contain
        <state_update> blocks that can be truncated by a crash). Recovery
        strategy: when a closing tag does not match the top of the stack, pop
        down to the nearest matching opening tag, auto-closing any truncated
        sections in between. Stray closing tags with no matching opener are
        ignored.
        """
        stack: list[Section] = []
        sections: list[Section] = []

        for i, line in enumerate(self.lines):
            pos = 0
            while True:
                lt = line.find("<", pos)
                if lt == -1:
                    break
                gt = line.find(">", lt)
                if gt == -1:
                    break  # malformed rest of line, stop scanning it
                raw = line[lt + 1:gt].strip()
                pos = gt + 1
                if not raw:
                    continue

                is_closing = raw.startswith("/")
                name = (
                    raw[1:].split(" ", 1)[0].strip()
                    if is_closing
                    else raw.split(" ", 1)[0].strip()
                )
                if name not in _ALLOWED_TAGS:
                    continue  # not one of ours, treat as content

                if is_closing:
                    self._close_tag(stack, sections, name, i)
                else:
                    self._open_tag(stack, line, i, raw, name)

        # Auto-close any tags left open by a truncated file.
        while stack:
            sec = stack.pop()
            sec.end = self.line_count
            if stack:
                stack[-1].children.append(sec)
            else:
                sections.append(sec)

        self._merge_system_sections(sections)
        return sections

    def _merge_system_sections(self, sections: list[Section]) -> None:
        """Collapse consecutive sibling <system> sections into one block.

        The engine already writes consecutive system messages as a single
        block; this is a fallback for logs generated before that change.
        """
        for sec in sections:
            if sec.children:
                self._merge_system_sections(sec.children)
            merged: list[Section] = []
            for child in sec.children:
                if merged and merged[-1].tag == "system" and child.tag == "system":
                    merged[-1].end = child.end
                else:
                    merged.append(child)
            sec.children = merged

    def _open_tag(self, stack: list[Section], line: str, i: int,
                  raw: str, name: str) -> None:
        attrs: Dict[str, str] = {}
        if len(stack) == 0 and name != "openloop_log":
            # A structural tag outside the root is malformed; ignore it.
            return
        parts = raw.split(" ", 1)
        if len(parts) > 1:
            for attr in parts[1].split(" "):
                if "=" in attr:
                    k, v = attr.split("=", 1)
                    v = v.strip()
                    if v.startswith('"') and v.endswith('"'):
                        v = v[1:-1].replace('\\"', '"').replace('\\\\', '\\')
                    attrs[k.strip()] = v
        label = self._label_for(name, attrs)
        stack.append(Section("xml", name, label, i, self.line_count))

    def _close_tag(self, stack: list[Section], sections: list[Section],
                   name: str, i: int) -> None:
        if not stack:
            return
        # Find the nearest matching opener (may be below truncated sections).
        idx = -1
        for k in range(len(stack) - 1, -1, -1):
            if stack[k].tag == name:
                idx = k
                break
        if idx == -1:
            return  # stray closing tag with no opener: ignore
        # Auto-close truncated sections above the match.
        while len(stack) - 1 > idx:
            sec = stack.pop()
            sec.end = i
            stack[-1].children.append(sec)
        sec = stack.pop()
        sec.end = i + 1
        if stack:
            stack[-1].children.append(sec)
        else:
            sections.append(sec)

    def _label_for(self, tag: str, attrs: Dict[str, str]) -> str:
        if tag == "openloop_log":
            return "OpenLoop Log"
        if tag == "iteration":
            num = attrs.get("number", "?")
            max_num = attrs.get("max", "?")
            return f"Iteration {num}/{max_num}"
        if tag == "agent":
            name = attrs.get("name", "?")
            phase = attrs.get("phase", "?")
            iteration = attrs.get("iteration", "?")
            return f"Agent: {name} (Phase: {phase}, Iteration: {iteration})"
        if tag == "stdout":
            return "Stdout"
        if tag == "stderr":
            return "Stderr"
        if tag == "system":
            return "System"
        if tag == "state":
            return "State"
        if tag == "state_update":
            return "State Update"
        return tag

    # -- Legacy parsing (##! markers and banner inference) --

    def _parse_legacy(self) -> list[Section]:
        if self._has_markers():
            runs = self._find_runs_marker()
        else:
            runs = self._find_runs_inference()

        sections: list[Section] = []

        # HEAD
        if runs:
            if runs[0].start > 0:
                sections.append(Section("head", "head", "HEAD", 0, runs[0].start))
        else:
            if self.line_count > 0:
                sections.append(Section("head", "head", "HEAD", 0, self.line_count))
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
                agent_sec = Section("agent", "agent", f"Agent: {r.agent}",
                                    r.start, r.end)
                iter_map.setdefault(r.iteration, []).append(agent_sec)

            is_loop = pname == "loop"
            sorted_iters = sorted(iter_map.items())
            phase_sec = Section("phase", "phase", f"Phase: {pname}", first, last)

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
                        "iteration", "iteration", f"Iteration {inum}",
                        iter_first, iter_last, children=agent_list,
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
                sections.append(Section("tail", "tail", "TAIL", last_end,
                                        self.line_count))
        elif self.line_count > 0:
            sections.append(Section("head", "head", "HEAD", 0, self.line_count))

        return sections

    def _has_markers(self) -> bool:
        return any(line.startswith(BEGIN_MARKER) for line in self.lines)

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

    # -- Text extraction --

    def get_raw_text(
        self,
        start: int,
        end: int,
        strip_markers: bool = True,
        omit_stderr: bool = False,
        omit_state: bool = False,
        omit_state_update: bool = False,
    ) -> str:
        out: list[str] = []
        stderr_lines = getattr(self, "_stderr_lines", set())
        state_lines = getattr(self, "_state_lines", set())
        state_update_lines = getattr(self, "_state_update_lines", set())
        for i, line in enumerate(self.lines[start:end]):
            line_idx = start + i
            if omit_stderr and line_idx in stderr_lines:
                continue  # drop stderr output from the preview
            if omit_state and line_idx in state_lines:
                continue  # drop effective-state sections from the preview
            if omit_state_update and line_idx in state_update_lines:
                continue  # drop raw <state_update> blocks from the preview
            if strip_markers:
                if _STRUCTURAL_LINE_RE.match(line):
                    continue  # strip engine structural tags
                if _MARKER_LINE_RE.match(line):
                    continue  # strip legacy ##! markers
            out.append(line)
        return "".join(out)

    def get_full_text(
        self,
        strip_markers: bool = True,
        omit_stderr: bool = False,
        omit_state: bool = False,
        omit_state_update: bool = False,
    ) -> str:
        return self.get_raw_text(
            0, self.line_count, strip_markers, omit_stderr,
            omit_state, omit_state_update,
        )


# ── GUI ──────────────────────────────────────────────────────────────────

class LoopLogApp:
    _MIN_WIN_W = 900
    _MIN_WIN_H = 500
    _DEF_WIN_W = 1200
    _DEF_WIN_H = 750

    _WATCH_INTERVAL_MS = 500

    def __init__(
        self,
        path: Optional[Path] = None,
        hide_system: bool = False,
        omit_stderr: bool = False,
        omit_state: bool = False,
        omit_state_update: bool = False,
        show_all: bool = False,
        wrap_lines: bool = False,
        filter_tag: str = "all",
        watch: bool = False,
    ) -> None:
        self.root = tk.Tk()
        self.root.title("LoopLog")
        self.root.minsize(self._MIN_WIN_W, self._MIN_WIN_H)
        self.root.geometry(f"{self._DEF_WIN_W}x{self._DEF_WIN_H}")

        self.parser: Optional[LogParser] = None
        self.sections: list[Section] = []
        self._path: Optional[Path] = path
        self._sec_map: dict[str, Section] = {}
        self._highlight_after_id: Optional[str] = None
        self._start_hide_system = hide_system
        self._start_omit_stderr = omit_stderr
        self._start_omit_state = omit_state
        self._start_omit_state_update = omit_state_update
        self._start_show_all = show_all
        self._start_wrap_lines = wrap_lines
        self._start_filter = filter_tag
        self._watch = watch
        self._watch_active = False
        self._watch_after_id: Optional[str] = None
        self._watch_signature: Optional[tuple[int, int]] = None
        self._mru_paths: list[str] = []
        self._mru_menu: Optional[tk.Menu] = None
        self._flow_state: str = "idle"
        self._banner: Optional[tk.Label] = None

        self._build_ui()
        self._setup_bindings()

        if path:
            self.load_log(path)

        # DWM tint calls before the toplevel is actually painted are ignored,
        # which made the initial state visible only at the first file update.
        # Re-apply the current state once idle, again shortly after the first
        # paint, and whenever the window gets mapped.
        self.root.after_idle(self._refresh_status)
        self.root.after(300, self._refresh_status)
        self.root.bind("<Map>", lambda _e: self._refresh_status())

    # -- UI construction --

    def _build_ui(self) -> None:
        # Menu bar
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open…", command=self._open_file, accelerator="Ctrl+O")
        self._mru_menu = file_menu
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        # Toggle state shared by the toolbar checkboxes and the View menu.
        self._show_all = tk.BooleanVar(value=self._start_show_all)
        self._hide_system = tk.BooleanVar(value=self._start_hide_system)
        self._omit_stderr = tk.BooleanVar(value=self._start_omit_stderr)
        self._omit_state = tk.BooleanVar(value=self._start_omit_state)
        self._omit_state_update = tk.BooleanVar(value=self._start_omit_state_update)
        self._wrap_lines = tk.BooleanVar(value=self._start_wrap_lines)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_checkbutton(
            label="Show entire file", variable=self._show_all,
            command=self._on_show_all
        )
        view_menu.add_checkbutton(
            label="Hide system tags", variable=self._hide_system,
            command=self._on_hide_system
        )
        view_menu.add_checkbutton(
            label="Omit stderr output", variable=self._omit_stderr,
            command=self._on_omit_stderr
        )
        view_menu.add_checkbutton(
            label="Omit state", variable=self._omit_state,
            command=self._on_omit_state
        )
        view_menu.add_checkbutton(
            label="Omit state update", variable=self._omit_state_update,
            command=self._on_omit_state_update
        )
        view_menu.add_checkbutton(
            label="Wrap lines", variable=self._wrap_lines,
            command=self._on_wrap_lines
        )
        menubar.add_cascade(label="View", menu=view_menu)
        self.root.config(menu=menubar)
        self._load_mru()

        # Flow-status banner: only where the native titlebar cannot carry the
        # color (non-Windows). On Windows the titlebar is tinted instead.
        if status.title_native_color_supported():
            self._banner = None
        else:
            self._banner = status.create_banner(self.root)
            self._banner.pack(fill=tk.X, side=tk.TOP)

        # Top bar: current file label + Filter + All toggle
        top = ttk.Frame(self.root, padding=(8, 4))
        top.pack(fill=tk.X)

        self._file_label = ttk.Label(top, text="No file loaded", foreground="gray")
        self._file_label.pack(side=tk.LEFT)

        # Filter dropdown
        self._filter_var = tk.StringVar(value=self._start_filter)
        self._filter_dropdown = ttk.Combobox(
            top, textvariable=self._filter_var,
            values=["all", "stdout", "stderr", "system", "state_update", "state"],
            state="readonly", width=12
        )
        self._filter_dropdown.pack(side=tk.RIGHT, padx=(0, 8))
        self._filter_dropdown.bind("<<ComboboxSelected>>", self._on_filter_change)

        ttk.Button(top, text="Refresh", command=self._refresh).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Checkbutton(
            top, text="Show entire file", variable=self._show_all,
            command=self._on_show_all
        ).pack(side=tk.RIGHT)
        self._hide_system_check = ttk.Checkbutton(
            top, text="Hide system tags", variable=self._hide_system,
            command=self._on_hide_system
        )
        self._hide_system_check.pack(side=tk.RIGHT, padx=(8, 0))
        self._omit_stderr_check = ttk.Checkbutton(
            top, text="Omit stderr output", variable=self._omit_stderr,
            command=self._on_omit_stderr
        )
        self._omit_stderr_check.pack(side=tk.RIGHT, padx=(8, 0))
        self._omit_state_check = ttk.Checkbutton(
            top, text="Omit state", variable=self._omit_state,
            command=self._on_omit_state
        )
        self._omit_state_check.pack(side=tk.RIGHT, padx=(8, 0))
        self._omit_state_update_check = ttk.Checkbutton(
            top, text="Omit state update", variable=self._omit_state_update,
            command=self._on_omit_state_update
        )
        self._omit_state_update_check.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Checkbutton(
            top, text="Wrap lines", variable=self._wrap_lines,
            command=self._on_wrap_lines
        ).pack(side=tk.RIGHT, padx=(8, 0))

        # Main paned window
        self._paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self._paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        # Left: Treeview
        left_frame = ttk.Frame(self._paned)
        self._tree = ttk.Treeview(
            left_frame, columns=(), show="tree",
            selectmode="extended"
        )
        tree_scroll = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._paned.add(left_frame, weight=1)

        # Right: Text widget
        right_frame = ttk.Frame(self._paned)
        self._text = tk.Text(
            right_frame,
            wrap=tk.WORD if self._start_wrap_lines else tk.NONE,
            state=tk.DISABLED,
            font=("Consolas", 10), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white"
        )
        text_scroll_y = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=self._text.yview)
        text_scroll_x = ttk.Scrollbar(right_frame, orient=tk.HORIZONTAL, command=self._text.xview)
        self._text.configure(
            yscrollcommand=text_scroll_y.set,
            xscrollcommand=text_scroll_x.set,
        )
        self._text.tag_configure("highlight", background="#3a3a3a")
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
        self._update_filter_options()
        self._rebuild_tree()
        self._refresh_status()

        self._stop_watching()
        self._watch_signature = self._file_signature()
        if self._watch and not self._is_log_complete():
            self._start_watching()

        self._record_mru(path)

    # -- MRU (most recently used log files) --

    @staticmethod
    def _mru_config_path() -> Path:
        """Path of the UI preferences file, next to the OpenLoop config.

        Follows the same search order as the config (CWD → next to
        ``openloop.py``) so a separate ``openloop-ui.json`` can be kept
        without touching the JSONC ``openloop.json``.
        """
        candidates = [
            Path("openloop-ui.json"),
            Path(__file__).resolve().parent.parent / "openloop-ui.json",
        ]
        for cand in candidates:
            if cand.exists():
                return cand
        # Persist next to wherever the config would be found.
        config_candidates = [
            Path("openloop.json"),
            Path(__file__).resolve().parent.parent / "openloop.json",
        ]
        for cand in config_candidates:
            if cand.exists():
                return cand.with_name("openloop-ui.json")
        return Path(__file__).resolve().parent.parent / "openloop-ui.json"

    def _load_mru(self) -> None:
        try:
            raw = json.loads(self._mru_config_path().read_text(encoding="utf-8"))
            paths = raw.get("recent_logs", []) if isinstance(raw, dict) else []
            self._mru_paths = [str(p) for p in paths][:5]
        except (OSError, ValueError):
            self._mru_paths = []
        self._update_mru_menu()

    def _save_mru(self) -> None:
        try:
            path = self._mru_config_path()
            path.write_text(
                json.dumps({"recent_logs": self._mru_paths}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _record_mru(self, path: Path) -> None:
        resolved = str(path.resolve())
        self._mru_paths = [resolved] + [
            p for p in self._mru_paths if p != resolved
        ]
        self._mru_paths = self._mru_paths[:5]
        self._update_mru_menu()
        self._save_mru()

    def _update_mru_menu(self) -> None:
        if self._mru_menu is None:
            return
        menu = self._mru_menu
        # Refresh the MRU section: it starts at index 2 (Open…, separator).
        for i in range(menu.index("end") or 0, 1, -1):
            menu.delete(i)
        if not self._mru_paths:
            menu.add_command(label="No recent files", state="disabled")
        else:
            for path in self._mru_paths:
                menu.add_command(
                    label=path,
                    command=lambda p=path: self.load_log(Path(p)),
                )
        menu.add_separator()
        menu.add_command(label="Exit", command=self.root.quit)

    # -- Watch mode (live refresh) --

    def _file_signature(self) -> Optional[tuple[int, int]]:
        """(size, mtime_ns) of the watched file, or None if it is gone."""
        if self._path is None:
            return None
        try:
            stat = self._path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _start_watching(self) -> None:
        if self._watch_active:
            return
        if self._path is None or not self._path.is_file():
            return
        self._watch_active = True
        self._watch_after_id = self.root.after(
            self._WATCH_INTERVAL_MS, self._watch_poll
        )

    def _stop_watching(self) -> None:
        self._watch_active = False
        if self._watch_after_id is not None:
            try:
                self.root.after_cancel(self._watch_after_id)
            except Exception:
                pass
            self._watch_after_id = None

    def _watch_poll(self) -> None:
        self._watch_after_id = None
        if not self._watch_active:
            return

        sig = self._file_signature()
        if sig is not None and sig != self._watch_signature:
            if self._reload_preserving_selection():
                self._watch_signature = sig
                if self._is_log_complete():
                    self._refresh_status(sound=True)
                    self._stop_watching()
                    return

        if self._watch_active:
            self._watch_after_id = self.root.after(
                self._WATCH_INTERVAL_MS, self._watch_poll
            )

    def _is_log_complete(self) -> bool:
        """True when the file has a closing </openloop_log> tag."""
        if self.parser is None or not self.parser.lines:
            return True
        return self.parser.lines[-1].strip() == "</openloop_log>"

    def _summary_text(self) -> str:
        """Raw text of the last <system> section (the closing summary).

        Returns an empty string when no summary block exists yet.
        """
        parser = getattr(self, "parser", None)
        sections = getattr(self, "sections", None) or []
        if parser is None or not sections:
            return ""
        try:
            systems = self._matching_sections("system")
            if not systems:
                return ""
            sec = systems[-1]
            return parser.get_raw_text(sec.start, sec.end).strip()
        except Exception:
            return ""

    def _refresh_status(self, *, sound: bool = False) -> None:
        """Recompute the flow state from the loaded log and repaint the UI.

        A log still being written reads as ``running``; a closed log gets its
        final state from the closing summary block.
        """
        parser = getattr(self, "parser", None)
        if parser is None:
            state = "idle"
        else:
            summary = self._summary_text()
            state = status.detect_log_state(parser.lines, summary)
        self._flow_state = state
        root = getattr(self, "root", None)
        status.apply_banner(
            getattr(self, "_banner", None), root, state,
            sound=sound,
        )
        if root is not None:
            root.title(f"{status.STATE_LABELS[state]} — LoopLog")

    def _reload_preserving_selection(self) -> bool:
        """Re-parse the log and rebuild the tree, keeping the selection.

        Returns True on success, False if the file could not be re-read
        (so the caller keeps the previous signature and can retry).
        """
        if self._path is None:
            return False
        try:
            self.parser = LogParser(self._path)
            self.sections = self.parser.parse()
        except Exception:
            return False

        keys = self._selection_keys()
        self._update_filter_options()
        self._rebuild_tree()
        self._restore_selection(keys)
        self._refresh_status()
        return True

    def _selection_keys(self) -> list[tuple[str, str, int]]:
        """Identifiers of the currently selected sections, by offset."""
        keys: list[tuple[str, str, int]] = []
        for node_id in self._tree.selection():
            sec = self._sec_map.get(node_id)
            if sec is not None:
                keys.append((sec.kind, sec.tag, sec.start))
        return keys

    def _restore_selection(self, keys: list[tuple[str, str, int]]) -> None:
        if not keys:
            return
        to_select: list[str] = []
        for node_id, sec in self._sec_map.items():
            if (sec.kind, sec.tag, sec.start) in keys:
                to_select.append(node_id)
        if not to_select:
            return
        self._tree.selection_set(to_select)
        self._tree.see(to_select[0])
        self._on_select(None)

    def _update_filter_options(self) -> None:
        # Filters only apply to XML-format logs; disable for legacy logs.
        is_xml = any(sec.kind == "xml" for sec in self.sections)
        values = ["all", "stdout", "stderr", "system", "state_update", "state"]
        if self._hide_system.get():
            # With system tags hidden there is no isolated system view.
            values.remove("system")
            if self._filter_var.get() == "system":
                self._filter_var.set("all")
        if self._omit_stderr.get():
            # With stderr omitted there is no isolated stderr view.
            values.remove("stderr")
            if self._filter_var.get() == "stderr":
                self._filter_var.set("all")
        if self._omit_state.get():
            # With effective state omitted there is no isolated state view.
            values.remove("state")
            if self._filter_var.get() == "state":
                self._filter_var.set("all")
        if self._omit_state_update.get():
            # With raw state updates omitted there is no isolated update view.
            values.remove("state_update")
            if self._filter_var.get() == "state_update":
                self._filter_var.set("all")
        self._filter_dropdown.configure(
            values=values,
            state="readonly" if is_xml else "disabled",
        )
        for check in (
            self._hide_system_check,
            self._omit_stderr_check,
            self._omit_state_check,
            self._omit_state_update_check,
        ):
            check.configure(state="normal" if is_xml else "disabled")

    def _on_hide_system(self) -> None:
        self._update_filter_options()
        self._rebuild_tree()

    def _on_omit_stderr(self) -> None:
        self._update_filter_options()
        self._rebuild_tree()

    def _on_omit_state(self) -> None:
        self._update_filter_options()
        self._rebuild_tree()

    def _on_omit_state_update(self) -> None:
        self._update_filter_options()
        self._rebuild_tree()

    def _on_wrap_lines(self) -> None:
        wrap = tk.WORD if self._wrap_lines.get() else tk.NONE
        self._text.config(wrap=wrap)

    # -- Tree population --

    def _rebuild_tree(self) -> None:
        self._tree.delete(*self._tree.get_children())
        self._sec_map.clear()
        self._text.config(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.config(state=tk.DISABLED)

        if not self.sections:
            return

        for sec in self.sections:
            self._insert_node("", sec)

        # Select and display the first top-level section by default
        first = self._tree.get_children()
        if first:
            self._tree.selection_set(first[0])
            self._tree.see(first[0])
        if self._show_all.get():
            if self.parser is not None:
                self._set_text(
                    self.parser.get_full_text(
                        strip_markers=False, **self._omit_kwargs()
                    )
                )
        elif self.sections:
            self._display_section(self.sections[0])

    def _insert_node(
        self,
        parent: str,
        sec: Section,
        boundary_system_ids: Optional[set[int]] = None,
    ) -> str:
        """Insert a section node; ``""`` for parent means a top-level root.

        ``boundary_system_ids`` is the set of direct ``system`` children of a
        root that stay visible even with "Hide system tags" active: the first
        (run header, before any agent) and the last (run summary). Every other
        ``system`` section — including those nested in iterations/agents — is
        hidden as usual.
        """
        if self._hide_system.get() and sec.tag == "system":
            if boundary_system_ids is None or id(sec) not in boundary_system_ids:
                return ""
        omit_stderr = getattr(self, "_omit_stderr", None)
        if omit_stderr is not None and omit_stderr.get() and sec.tag == "stderr":
            return ""
        omit_state = getattr(self, "_omit_state", None)
        if omit_state is not None and omit_state.get() and sec.tag == "state":
            return ""
        omit_state_update = getattr(self, "_omit_state_update", None)
        if (
            omit_state_update is not None
            and omit_state_update.get()
            and sec.tag == "state_update"
        ):
            return ""
        node_id = self._tree.insert(parent, tk.END, text=sec.label, open=True)
        self._sec_map[node_id] = sec

        child_boundary: Optional[set[int]] = None
        if sec.tag == "openloop_log":
            sys_children = [c for c in sec.children if c.tag == "system"]
            if sys_children:
                child_boundary = {id(sys_children[0]), id(sys_children[-1])}

        for child in sec.children:
            self._insert_node(node_id, child, child_boundary)
        return node_id

    # -- Selection handling --

    def _on_select(self, _event: Optional[tk.Event] = None) -> None:
        if self._show_all.get():
            self._jump_to_section()
            return
        sel = self._tree.selection()
        if not sel:
            return
        secs = [self._sec_map[i] for i in sel if i in self._sec_map]
        if secs:
            self._display_sections(_prune_contained(secs))

    def _jump_to_section(self) -> None:
        """Scroll the whole-file view to the selected section and highlight it."""
        if self.parser is None:
            return
        sel = self._tree.selection()
        if not sel:
            return
        sec = self._sec_map.get(sel[0])
        if sec is None:
            return
        omitted = self._omitted_line_indices()
        start = f"{sec.start + 1 - sum(1 for o in omitted if o < sec.start)}.0"
        end_raw = max(sec.end, sec.start + 1)
        end = (
            f"{end_raw + 1 - sum(1 for o in omitted if o < end_raw)}.0"
        )
        self._text.see(start)
        self._text.tag_remove("highlight", "1.0", tk.END)
        self._text.tag_add("highlight", start, end)
        if self._highlight_after_id is not None:
            self._text.after_cancel(self._highlight_after_id)
        self._highlight_after_id = self._text.after(
            1500, self._clear_highlight
        )

    def _omitted_line_indices(self) -> set[int]:
        """Raw file lines dropped from the preview by the active omit flags.

        In "Show entire file" mode the text widget shows ``get_full_text`` with
        omitted sections removed, so raw line numbers no longer match widget
        lines. This returns the raw indices that are hidden, letting
        ``_jump_to_section`` translate section bounds into widget coordinates.
        """
        if self.parser is None:
            return set()
        omitted: set[int] = set()
        if self._omit_stderr.get():
            omitted |= getattr(self.parser, "_stderr_lines", set())
        if self._omit_state.get():
            omitted |= getattr(self.parser, "_state_lines", set())
        if self._omit_state_update.get():
            omitted |= getattr(self.parser, "_state_update_lines", set())
        return omitted

    def _clear_highlight(self) -> None:
        self._text.tag_remove("highlight", "1.0", tk.END)
        self._highlight_after_id = None

    def _matching_sections(self, tag: str) -> list[Section]:
        """All sections in the document with the given tag."""
        matches: list[Section] = []

        def walk(sec: Section) -> None:
            if sec.tag == tag:
                matches.append(sec)
            for child in sec.children:
                walk(child)

        for sec in self.sections:
            walk(sec)
        return matches

    def _display_section(self, sec: Section) -> None:
        self._display_sections([sec])

    def _display_sections(self, secs: list[Section]) -> None:
        if self.parser is None:
            return

        omit_kwargs = self._omit_kwargs()

        filter_tag = self._filter_var.get()
        if filter_tag != "all":
            # Show every matching block across the whole log, not just the
            # first one.
            matches = self._matching_sections(filter_tag)
            if not matches:
                self._set_text(f"No content matching filter: {filter_tag}")
                return
            parts: list[str] = []
            for m in matches:
                text = self.parser.get_raw_text(
                    m.start, m.end, **omit_kwargs
                ).strip()
                parts.append(_block_header(m) + text)
            self._set_text("\n\n".join(parts))
            return

        if len(secs) == 1:
            text = self.parser.get_raw_text(
                secs[0].start, secs[0].end, **omit_kwargs
            )
            self._set_text(text)
            return

        # Multi-select: show every selected node's content, separated by a
        # horizontal ruler labelled with the node's title.
        parts = []
        for sec in secs:
            text = self.parser.get_raw_text(
                sec.start, sec.end, **omit_kwargs
            ).strip()
            parts.append(_block_header(sec) + text)
        self._set_text("\n\n".join(parts))

    def _omit_kwargs(self) -> dict:
        """Preview kwargs honoring the active omit checkboxes."""
        return {
            "omit_stderr": self._omit_stderr.get(),
            "omit_state": self._omit_state.get(),
            "omit_state_update": self._omit_state_update.get(),
        }

    def _on_filter_change(self, _event: Optional[tk.Event] = None) -> None:
        if self._show_all.get():
            return  # Filtering is disabled in "Show entire file" mode
        if not self.sections:
            return
        sel = self._tree.selection()
        if not sel:
            first = self._tree.get_children()
            if first:
                self._tree.selection_set(first[0])
        self._on_select(None)

    def _on_show_all(self) -> None:
        if self.parser is None:
            return
        if self._show_all.get():
            self._set_text(
                self.parser.get_full_text(
                    strip_markers=False, **self._omit_kwargs()
                )
            )
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

def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="looplog",
        description="Standalone Tkinter viewer for structured OpenLoop log files.",
    )
    parser.add_argument(
        "logfile",
        nargs="?",
        type=Path,
        help="path to a log file (optional; opens a file dialog if omitted)",
    )
    parser.add_argument(
        "--hide-system-tags",
        action="store_true",
        help=(
            "activate the 'Hide system tags' checkbox at startup; the first "
            "and last system section of each run root always stay visible"
        ),
    )
    parser.add_argument(
        "--omit-stderr",
        action="store_true",
        help=(
            "activate the 'Omit stderr output' checkbox at startup; stderr "
            "sections are dropped from both the treeview and the preview"
        ),
    )
    parser.add_argument(
        "--omit-state",
        action="store_true",
        help=(
            "activate the 'Omit state' checkbox at startup; effective-state "
            "sections are dropped from both the treeview and the preview"
        ),
    )
    parser.add_argument(
        "--omit-state-update",
        action="store_true",
        help=(
            "activate the 'Omit state update' checkbox at startup; raw "
            "<state_update> blocks are dropped from both the treeview and "
            "the preview"
        ),
    )
    parser.add_argument(
        "--show-entire-file",
        action="store_true",
        help="activate the 'Show entire file' checkbox and render the whole file at startup",
    )
    parser.add_argument(
        "--wrap-lines",
        action="store_true",
        help="wrap long lines in the preview pane at startup",
    )
    parser.add_argument(
        "--filter",
        choices=["all", "stdout", "stderr", "system", "state_update", "state"],
        default="all",
        help="preselect the content filter (default: all)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="auto-refresh the view as the log file grows (stops once the run is complete)",
    )
    args = parser.parse_args(argv)

    path: Optional[Path] = None
    if args.logfile is not None:
        path = args.logfile
        if not path.is_file():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    app = LoopLogApp(
        path,
        hide_system=args.hide_system_tags,
        omit_stderr=args.omit_stderr,
        omit_state=args.omit_state,
        omit_state_update=args.omit_state_update,
        show_all=args.show_entire_file,
        wrap_lines=args.wrap_lines,
        filter_tag=args.filter,
        watch=args.watch,
    )
    app.run()


if __name__ == "__main__":
    main()
