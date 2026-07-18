import re
import webbrowser

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
CODE_RE = re.compile(r"`(.+?)`")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
BULLET_RE = re.compile(r"^[-*+]\s+(.+)$")
HR_RE = re.compile(r"^[-*_]{3,}$")
TABLE_RE = re.compile(r"^\|.+")
FENCE_RE = re.compile(r"^```")


def _tag_cfg(w):
    w.tag_configure("h1", font=("TkDefaultFont", 16, "bold"))
    w.tag_configure("h2", font=("TkDefaultFont", 14, "bold"))
    w.tag_configure("h3", font=("TkDefaultFont", 12, "bold"))
    w.tag_configure("h4", font=("TkDefaultFont", 11, "bold"))
    w.tag_configure("h5", font=("TkDefaultFont", 10, "bold"))
    w.tag_configure("h6", font=("TkDefaultFont", 10, "bold"), foreground="#555555")
    w.tag_configure("bold", font=("TkDefaultFont", 10, "bold"))
    w.tag_configure("italic", font=("TkDefaultFont", 10, "italic"))
    w.tag_configure("code", font=("TkFixedFont", 10))
    w.tag_configure("codeblock", font=("TkFixedFont", 10), background="#f0f0f0")
    w.tag_configure("blockquote", lmargin1=30, lmargin2=35,
                    font=("TkDefaultFont", 10, "italic"))
    w.tag_configure("list", lmargin1=20, lmargin2=30)
    w.tag_configure("link", foreground="blue", underline=True)
    w.tag_configure("frontmatter", foreground="#888888")
    w.tag_configure("table", font=("TkFixedFont", 10))


_link_map: dict = {}


def _on_link_click(w, event):
    idx = w.index(f"@{event.x},{event.y}")
    for (s, e), url in _link_map.get(id(w), {}).items():
        if w.compare(s, "<=", idx) and w.compare(idx, "<=", e):
            webbrowser.open(url)
            return


def _insert_inline(w, text, base_tag=None):
    pieces = []
    for m in LINK_RE.finditer(text):
        pieces.append((m.start(), m.end(), m.group(1), "link", m.group(2), m))
    for m in BOLD_RE.finditer(text):
        pieces.append((m.start(), m.end(), m.group(1), "bold", None, m))
    for m in ITALIC_RE.finditer(text):
        pieces.append((m.start(), m.end(), m.group(1), "italic", None, m))
    for m in CODE_RE.finditer(text):
        pieces.append((m.start(), m.end(), m.group(1), "code", None, m))

    pieces.sort(key=lambda x: x[0])
    filtered = []
    last_end = 0
    for p in pieces:
        if p[0] >= last_end:
            filtered.append(p)
            last_end = p[1]

    pos = 0
    for start, end, content, tag, url, _ in filtered:
        if start > pos:
            tags = [base_tag] if base_tag else []
            w.insert("end", text[pos:start], *([t for t in tags if t]))
        tags = [t for t in ([base_tag] if base_tag else []) + [tag] if t]
        if url:
            s_idx = w.index("end - 1c")
            w.insert("end", content, *tags)
            e_idx = w.index("end - 1c")
            _link_map.setdefault(id(w), {})[(s_idx, e_idx)] = url
            w.tag_bind("link", "<Button-1>",
                       lambda ev, ww=w: _on_link_click(ww, ev))
        else:
            w.insert("end", content, *tags)
        pos = end

    if pos < len(text):
        tags = [base_tag] if base_tag else []
        w.insert("end", text[pos:], *([t for t in tags if t]))


def _insert_line(w, line, tag):
    w.insert("end", line + "\n", tag)


def _render_line(w, line, _line_num):
    s = line.strip()

    if not s:
        w.insert("end", "\n")
        return

    m = HEADING_RE.match(line)
    if m:
        level = min(len(m.group(1)), 6)
        w.insert("end", m.group(2) + "\n", f"h{level}")
        return

    if HR_RE.match(s):
        w.insert("end", "─" * 50 + "\n", "code")
        return

    m = BLOCKQUOTE_RE.match(line)
    if m:
        _insert_inline(w, m.group(1), "blockquote")
        w.insert("end", "\n")
        return

    m = BULLET_RE.match(s)
    if m:
        w.insert("end", "  •  ", "list")
        _insert_inline(w, m.group(1), "list")
        w.insert("end", "\n")
        return

    if TABLE_RE.match(s):
        w.insert("end", "  " + line.strip() + "\n", "table")
        return

    _insert_inline(w, line)
    w.insert("end", "\n")


def render(text_widget, markdown_text):
    text_widget.configure(state="normal")
    text_widget.delete("1.0", "end")
    _tag_cfg(text_widget)

    lines = markdown_text.split("\n")
    in_fm = False
    in_code = False

    for line in lines:
        s = line.strip()

        if s == "---" and not in_code:
            in_fm = not in_fm
            continue

        if FENCE_RE.match(s):
            in_code = not in_code
            continue

        if in_fm:
            _insert_line(text_widget, line, "frontmatter")
        elif in_code:
            _insert_line(text_widget, line, "codeblock")
        else:
            _render_line(text_widget, line, 0)

    text_widget.configure(state="disabled")
