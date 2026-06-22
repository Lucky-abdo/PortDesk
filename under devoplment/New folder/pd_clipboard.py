"""
pd_clipboard.py — system clipboard get/set for PortDesk (extracted module).

Platform-native: xclip/xsel (Linux), pbcopy/pbpaste (macOS), clip/powershell
(Windows). Self-contained — stdlib only.
"""
from __future__ import annotations
import platform
import subprocess

# ── Built-in clipboard — replaces pyperclip dependency ─────────────────────────
# Uses platform-native commands (xclip/xsel on Linux, pbcopy/pbpaste on macOS,
# clip/powershell on Windows). Falls back to pyperclip if available.
def _clipboard_copy(text: str) -> bool:
    """Copy text to system clipboard. Returns True on success."""
    _sys = platform.system()
    try:
        if _sys == 'Linux':
            for cmd in ('xclip', 'xsel'):
                if subprocess.run(['which', cmd], capture_output=True).returncode == 0:
                    if cmd == 'xclip':
                        p = subprocess.Popen(['xclip', '-selection', 'clipboard'],
                                             stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    else:
                        p = subprocess.Popen(['xsel', '--clipboard', '--input'],
                                             stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    p.communicate(text.encode(), timeout=2)
                    return p.returncode == 0
        elif _sys == 'Darwin':
            p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            p.communicate(text.encode(), timeout=2)
            return p.returncode == 0
        elif _sys == 'Windows':
            subprocess.run(['clip'], input=text.encode(), check=True, timeout=2)
            return True
    except Exception:
        pass
    return False

def _clipboard_paste() -> str | None:
    """Read text from system clipboard. Returns text or None."""
    _sys = platform.system()
    try:
        if _sys == 'Linux':
            for cmd in ('xclip', 'xsel'):
                if subprocess.run(['which', cmd], capture_output=True).returncode == 0:
                    if cmd == 'xclip':
                        r = subprocess.run(['xclip', '-selection', 'clipboard', '-o'],
                                           capture_output=True, text=True, timeout=2)
                    else:
                        r = subprocess.run(['xsel', '--clipboard', '--output'],
                                           capture_output=True, text=True, timeout=2)
                    if r.returncode == 0: return r.stdout
        elif _sys == 'Darwin':
            r = subprocess.run(['pbpaste'], capture_output=True, text=True, timeout=2)
            if r.returncode == 0: return r.stdout
        elif _sys == 'Windows':
            r = subprocess.run(['powershell', '-Command', 'Get-Clipboard'],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0: return r.stdout
    except Exception:
        pass
    return None
