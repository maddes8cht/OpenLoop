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
from typing import Optional, Dict, List


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
# it is agent content written verbatim inside <stdout>.
_STRUCTURAL_TAGS = {"openloop_log", "iteration", "agent", "stdout", "stderr", "system"}

# XML tags recognized as sections (structural tags + agent state updates so
# the "state_update" filter can find them).
_ALLOWED_TAGS = _STRUCTURAL_TAGS | {"state_update"}

# Lines that are exactly an engine structural tag (open or close) are stripped
# when displaying raw text. Lines merely *starting* with "<" (tracebacks, HTML,
# agent state updates, comparisons) are content and are preserved.
_STRUCTURAL_LINE_RE = re.compile(
    r"^\s*</?(?:openloop_log|iteration|agent|stdout|stderr|system)"
    r"(?:\s+[^>]*)?>\s*$"
)

# Separator drawn above each preview block (multi-select and filter view).
_SEPARATOR = "=" * 60


def _block_header(sec: Section) -> str:
    return f"{_SEPARATOR}\n{sec.label}  (lines {sec.start + 1}-{sec.end})\n"


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
            return self._parse_xml()
        return self._parse_legacy()

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

    def get_raw_text(self, start: int, end: int, strip_markers: bool = True) -> str:
        if not strip_markers:
            return "".join(self.lines[start:end])

        out: list[str] = []
        for line in self.lines[start:end]:
            if _STRUCTURAL_LINE_RE.match(line):
                continue  # strip engine structural tags
            if _MARKER_LINE_RE.match(line):
                continue  # strip legacy ##! markers
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
        self._highlight_after_id: Optional[str] = None

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

        # Top bar: current file label + Filter + All toggle
        top = ttk.Frame(self.root, padding=(8, 4))
        top.pack(fill=tk.X)

        self._file_label = ttk.Label(top, text="No file loaded", foreground="gray")
        self._file_label.pack(side=tk.LEFT)

        # Filter dropdown
        self._filter_var = tk.StringVar(value="all")
        self._filter_dropdown = ttk.Combobox(
            top, textvariable=self._filter_var,
            values=["all", "stdout", "stderr", "system", "state_update"],
            state="readonly", width=12
        )
        self._filter_dropdown.pack(side=tk.RIGHT, padx=(0, 8))
        self._filter_dropdown.bind("<<ComboboxSelected>>", self._on_filter_change)

        self._show_all = tk.BooleanVar(value=False)
        ttk.Button(top, text="Refresh", command=self._refresh).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Checkbutton(
            top, text="Show entire file", variable=self._show_all,
            command=self._on_show_all
        ).pack(side=tk.RIGHT)
        self._hide_system = tk.BooleanVar(value=False)
        self._hide_system_check = ttk.Checkbutton(
            top, text="Hide system tags", variable=self._hide_system,
            command=self._on_hide_system
        )
        self._hide_system_check.pack(side=tk.RIGHT, padx=(8, 0))

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
        self._hide_system.set(False)
        self._show_all.set(False)
        self._update_filter_options()
        self._rebuild_tree()

    def _update_filter_options(self) -> None:
        # Filters only apply to XML-format logs; disable for legacy logs.
        is_xml = any(sec.kind == "xml" for sec in self.sections)
        values = ["all", "stdout", "stderr", "system", "state_update"]
        if self._hide_system.get():
            # With system tags hidden there is no isolated system view.
            values.remove("system")
            if self._filter_var.get() == "system":
                self._filter_var.set("all")
        self._filter_dropdown.configure(
            values=values,
            state="readonly" if is_xml else "disabled",
        )
        self._hide_system_check.configure(
            state="normal" if is_xml else "disabled"
        )

    def _on_hide_system(self) -> None:
        self._update_filter_options()
        self._rebuild_tree()

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
                self._set_text(self.parser.get_full_text(strip_markers=False))
        elif self.sections:
            self._display_section(self.sections[0])

    def _insert_node(self, parent: str, sec: Section) -> str:
        if self._hide_system.get() and sec.tag == "system":
            return ""
        node_id = self._tree.insert(parent, tk.END, text=sec.label, open=True)
        self._sec_map[node_id] = sec
        for child in sec.children:
            self._insert_node(node_id, child)
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
            self._display_sections(secs)

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
        start = f"{sec.start + 1}.0"
        end = f"{max(sec.end, sec.start + 1) + 1}.0"
        self._text.see(start)
        self._text.tag_remove("highlight", "1.0", tk.END)
        self._text.tag_add("highlight", start, end)
        if self._highlight_after_id is not None:
            self._text.after_cancel(self._highlight_after_id)
        self._highlight_after_id = self._text.after(
            1500, self._clear_highlight
        )

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
                text = self.parser.get_raw_text(m.start, m.end).strip()
                parts.append(_block_header(m) + text)
            self._set_text("\n\n".join(parts))
            return

        if len(secs) == 1:
            text = self.parser.get_raw_text(secs[0].start, secs[0].end)
            self._set_text(text)
            return

        # Multi-select: show every selected node's content, separated by a
        # horizontal ruler labelled with the node's title.
        parts = []
        for sec in secs:
            text = self.parser.get_raw_text(sec.start, sec.end).strip()
            parts.append(_block_header(sec) + text)
        self._set_text("\n\n".join(parts))

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
