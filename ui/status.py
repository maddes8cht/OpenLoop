"""Shared flow-status presentation for the OpenLoop GUI and the LoopLog viewer.

Both windows render the same states, colors, system sounds and native frame
tint so the user gets one consistent signal no matter which window they look
at. Zero dependencies: the Windows-specific helpers use only stdlib
``ctypes``/``winsound`` and degrade silently where the API is unavailable
(system sounds are Windows-only, the DWM frame tint is Windows 11).

The colored status strip beneath the toolbar is only rendered on systems that
can *not* tint the native titlebar (i.e. not Windows): there it is the only
color carrier. On Windows the titlebar itself shows the state color, so the
strip would only waste vertical space and is omitted.
"""

import os
from typing import Dict


# ── State model ─────────────────────────────────────────────────────

#: Ordered flow states. ``idle`` is only reached at startup and after the
#: GUI's "Clear" button reset the status banner.
FLOW_STATES = ("idle", "running", "completed", "interrupted", "error")

STATE_COLORS: Dict[str, str] = {
    "idle": "#8b8680",
    "running": "#1e88e5",
    "completed": "#2e9e44",
    "interrupted": "#e89f1a",
    "error": "#d43b37",
}

#: Short labels used for the window-title prefix and the banner.
STATE_LABELS: Dict[str, str] = {
    "idle": "IDLE",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "interrupted": "INTERRUPTED",
    "error": "ERROR",
}
TERMINAL_STATES = ("completed", "interrupted", "error")


# ── Classification ────────────────────────────────────────────────────

def state_from_reason(reason: str, is_complete: bool = False) -> str:
    """Map an engine termination state to a flow state.

    - ``completed`` (or no reason yet)                -> ``completed``
    - ``stopped``, ``max_loops_reached``, ``timeout`` -> ``interrupted``
    - ``agent_error``, ``missing_state``              -> ``error``
    - anything else                                   -> ``error``
    """
    if is_complete or not reason or reason == "completed":
        return "completed"
    if reason.startswith("timeout") or reason in (
        "stopped", "max_loops_reached"
    ):
        return "interrupted"
    return "error"


def state_from_summary(text: str) -> str:
    """Infer the final flow state from an OpenLoop run's closing summary.

    The summary is the last ``<system>`` block the engine writes before the
    closing ``</openloop_log>``. The checks look for the exact phrases emitted
    by ``Engine._termination_summary_line``.
    """
    text = text or ""
    if "agent error" in text or "no state update" in text:
        return "error"
    if (
        "max loops" in text
        or "stopped by user" in text
        or "timed out" in text
    ):
        return "interrupted"
    return "completed"


def detect_log_state(lines: list[str], summary_text: str) -> str:
    """State of an OpenLoop log file.

    A log without the closing ``</openloop_log>`` is still being written ->
    ``running``. Once closed, the final state is read from the summary block.
    """
    if not lines:
        return "idle"
    if lines[-1].strip() != "</openloop_log>":
        return "running"
    return state_from_summary(summary_text)


# ── Banner widget ─────────────────────────────────────────────────────

def title_native_color_supported() -> bool:
    """True when the native window titlebar can be tinted (Windows only).

    Used to decide whether the colored status strip is rendered at all: where
    the titlebar carries the color the strip adds nothing but vertical space.
    """
    return os.name == "nt"


def create_banner(parent) -> "tkinter.Label":
    """A thin colored status strip pinned to the top of a Tk window.

    Text uses the state label (e.g. ``● RUNNING``) and the background carries
    the state color so both windows render the identical banner.
    """
    import tkinter as tk

    banner = tk.Label(
        parent,
        text=f"● {STATE_LABELS['idle']}",
        anchor="w",
        padx=10,
        pady=4,
        foreground="#ffffff",
        background=STATE_COLORS["idle"],
    )
    return banner


def apply_banner(banner, root, state: str, *, sound: bool = False) -> None:
    """Repaint the status strip and native titlebar for a flow state.

    Both the strip (on machines that render it) and the titlebar always hold
    the steady state color -- they never flash to white, so the status signal
    is not lost. ``sound`` honors the success/problem split: completed uses
    the success sound, interrupted/error use the error sound.
    """
    color = STATE_COLORS[state]
    label = STATE_LABELS.get(state, state.upper())
    if banner is not None and hasattr(banner, "configure"):
        try:
            banner.configure(background=color, text=f"● {label}")
        except Exception:
            banner.configure(background=color)
    _apply_frame_tint(root, color)
    if sound and state in TERMINAL_STATES:
        _play_state_sound(state)


def _play_state_sound(state: str) -> None:
    try:
        import winsound
    except ImportError:
        return
    try:
        if state == "completed":
            winsound.MessageBeep(winsound.MB_OK)
        else:
            winsound.MessageBeep(winsound.MB_ICONERROR)
    except Exception:
        pass


# ── Native window frame tint (Windows 11) ────────────────────────────

def _apply_frame_tint(root, color: str) -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        if not (ctypes and hasattr(ctypes, "windll")):
            return
        hwnd = int(root.winfo_id())
        # The client HWND is not the title bar's; climb to the top window.
        uk = ctypes.windll.user32
        GA_ROOT = 2
        hwnd = uk.GetAncestor(hwnd, GA_ROOT)
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        cref = (b << 16) | (g << 8) | r
        val = ctypes.c_int(cref)
        for attr in (34, 35):  # DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )
    except Exception:
        pass


# ── WinGraphics: close a spawned viewer window ────────────────────────

def close_process_main_window(pid: int) -> bool:
    """Gracefully close the top-level window(s) of *pid* via WM_CLOSE.

    Returns True if at least one window was found and messaged. Used by the
    GUI to close only the LoopLog windows it spawned itself, never windows that
    the user opened independently.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        targets = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            wid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wid))
            if wid.value == pid:
                targets.append(hwnd)
            return True

        user32.EnumWindows(_enum, 0)
        WM_CLOSE = 0x0010
        for hwnd in targets:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return bool(targets)
    except Exception:
        return False