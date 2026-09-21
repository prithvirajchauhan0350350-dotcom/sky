"""
automation.py — Screen & app automation actions for Spy.
-----------------------------------------------------------
Plain, side-effect-only helpers. Two other layers sit around them:

  - AssistantBrain (spy.py) decides WHEN to call these, via Claude's
    native tool use / function calling.
  - The GUI confirmation step decides WHETHER they're allowed to run.
    Nothing in this module executes until the user approves.

SAFETY NOTES:
  * pyautogui.FAILSAFE is enabled: slam the mouse into the TOP-LEFT
    corner of the screen to instantly abort any running automation.
  * type_text / click_at / press_key act on whatever window currently
    has focus — the caller must warn the user about this before approval.
"""

import os
import subprocess
from datetime import datetime

import pyautogui

pyautogui.FAILSAFE = True   # mouse to top-left corner = emergency abort
pyautogui.PAUSE = 0.15      # small pause after every pyautogui action

SCREENSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "spy_screenshots")

# Well-known apps -> exact launch command. Anything not listed falls back
# to Windows' shell "start" lookup (works for anything on PATH / registered).
APP_COMMANDS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "cmd": ["cmd.exe"],
    "command prompt": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "task manager": ["taskmgr.exe"],
    "chrome": ["cmd", "/c", "start", "chrome"],
    "google chrome": ["cmd", "/c", "start", "chrome"],
    "edge": ["cmd", "/c", "start", "msedge"],
    "microsoft edge": ["cmd", "/c", "start", "msedge"],
    "spotify": ["cmd", "/c", "start", "spotify"],
    "vs code": ["cmd", "/c", "start", "code"],
    "vscode": ["cmd", "/c", "start", "code"],
    "word": ["cmd", "/c", "start", "winword"],
    "excel": ["cmd", "/c", "start", "excel"],
}


def active_window_title() -> str:
    """Title of the currently focused window, or '' if unavailable.
    Used to warn the user about WHERE an action will land."""
    try:
        win = pyautogui.getActiveWindow()
        return win.title if win else ""
    except Exception:
        return ""


def screen_size() -> dict:
    w, h = pyautogui.size()
    return {"width": w, "height": h}


def open_app(name: str) -> dict:
    """Launch an application by friendly name. Non-blocking (Popen)."""
    name = (name or "").strip().lower()
    if not name:
        return {"ok": False, "message": "No app name given."}
    try:
        cmd = APP_COMMANDS.get(name)
        if cmd:
            subprocess.Popen(cmd)
            return {"ok": True, "message": f"Launched {name}."}
        # Fallback: let Windows resolve it (PATH, App Paths, Start Menu).
        subprocess.Popen(f'start "" "{name}"', shell=True)
        return {"ok": True, "message": f"Asked Windows to open '{name}'."}
    except Exception as e:
        return {"ok": False, "message": f"Could not open {name}: {e}"}


def type_text(text: str, interval: float = 0.03) -> dict:
    """Type text into the CURRENTLY FOCUSED window, character by character."""
    if not text:
        return {"ok": False, "message": "Nothing to type."}
    try:
        pyautogui.write(text, interval=interval)
        return {"ok": True, "message": f"Typed {len(text)} characters into the active window."}
    except Exception as e:
        return {"ok": False, "message": f"Typing failed: {e}"}
    # Note: pyautogui.write is US-keyboard-layout and ASCII-oriented; exotic
    # unicode characters may come out wrong.


def click_at(x: int, y: int, button: str = "left", double: bool = False) -> dict:
    """Move the mouse and click at absolute screen coordinates."""
    w, h = pyautogui.size()
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return {"ok": False, "message": f"Invalid coordinates: ({x!r}, {y!r})."}
    if not (0 <= x < w and 0 <= y < h):
        return {"ok": False, "message": f"Coordinates ({x}, {y}) are off-screen (screen is {w}x{h})."}
    try:
        pyautogui.click(x=x, y=y, clicks=2 if double else 1, button=button)
        return {"ok": True, "message": f"Clicked at ({x}, {y})."}
    except Exception as e:
        return {"ok": False, "message": f"Click failed: {e}"}


def press_key(key: str) -> dict:
    """Press a single keyboard key: 'enter', 'esc', 'tab', 'ctrl+c' style
    combos via hotkey, etc. Acts on the focused window."""
    key = (key or "").strip().lower()
    if not key:
        return {"ok": False, "message": "No key given."}
    try:
        if "+" in key:  # combos like ctrl+s, alt+f4
            parts = [p.strip() for p in key.split("+") if p.strip()]
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(key)
        return {"ok": True, "message": f"Pressed '{key}'."}
    except Exception as e:
        return {"ok": False, "message": f"Key press failed: {e}"}


def take_screenshot() -> dict:
    """Save a timestamped screenshot; returns the file path (the brain
    sends the image back to Claude so it can see the screen)."""
    try:
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        path = os.path.join(
            SCREENSHOT_DIR,
            f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
        )
        pyautogui.screenshot(path)
        return {"ok": True, "message": f"Screenshot saved to {path}", "path": path}
    except Exception as e:
        return {"ok": False, "message": f"Screenshot failed: {e}"}
