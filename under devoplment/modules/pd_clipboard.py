"""
pd_clipboard.py — system clipboard get/set for PortDesk (extracted module).

Platform-native: xclip/xsel (Linux), pbcopy/pbpaste (macOS), PowerShell
(Windows). Self-contained — stdlib only.

Phase 7: Windows path rewritten to use Set-Clipboard / Get-Clipboard via
PowerShell instead of the legacy `clip` command. The old `clip` round-trip
corrupted Arabic/CJK text because `clip` interprets its stdin as the system
ANSI codepage (CP1252/CP720) and re-encodes it. Set-Clipboard accepts a
UTF-16 string from PowerShell, which is Unicode-clean end-to-end.

Get-Clipboard | Out-String emits UTF-16LE on stdout; we decode it as such
and rstrip the trailing newline that Out-String adds.
"""
from __future__ import annotations
import platform
import subprocess

# ── Built-in clipboard — replaces pyperclip dependency ─────────────────────────
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
                    # UTF-8 is the only sane encoding for xclip/xsel — they
                    # store raw bytes and the X selection is encoding-agnostic,
                    # so any compliant client (including ours) decodes as UTF-8.
                    p.communicate(text.encode('utf-8'), timeout=2)
                    return p.returncode == 0
        elif _sys == 'Darwin':
            p = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            # pbcopy on macOS expects UTF-8 input by default.
            p.communicate(text.encode('utf-8'), timeout=2)
            return p.returncode == 0
        elif _sys == 'Windows':
            # Phase 7: Set-Clipboard stores the string as a proper UTF-16
            # CF_UNICODETEXT entry in the Windows clipboard. The old `clip`
            # command corrupted Arabic/CJK text because it round-tripped
            # through the system ANSI codepage.
            #
            # We feed the text to PowerShell via stdin (UTF-8 encoded) rather
            # than embedding it in the -Command string. This avoids all
            # shell-quoting pitfalls AND keeps Arabic punctuation intact.
            # The PowerShell script reads $input (the piped stdin), converts
            # it to a single string, and calls Set-Clipboard.
            ps_script = (
                '$t = [Console]::In.ReadToEnd();'
                'Set-Clipboard -Value $t'
            )
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
                input=text.encode('utf-8'),
                capture_output=True,
                timeout=3,
            )
            if r.returncode == 0:
                return True
            # Fallback: try the old `clip` command for ASCII-only text.
            # If the text contains non-ASCII chars and Set-Clipboard failed,
            # we don't want to silently corrupt it — return False so the
            # caller knows the copy didn't work.
            try:
                text.encode('ascii')
            except UnicodeEncodeError:
                return False
            subprocess.run(['clip'], input=text.encode('cp1252', errors='replace'),
                            check=True, timeout=2)
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
                                           capture_output=True, timeout=2)
                    else:
                        r = subprocess.run(['xsel', '--clipboard', '--output'],
                                           capture_output=True, timeout=2)
                    if r.returncode == 0:
                        return r.stdout.decode('utf-8', errors='replace')
        elif _sys == 'Darwin':
            r = subprocess.run(['pbpaste'], capture_output=True, timeout=2)
            if r.returncode == 0:
                return r.stdout.decode('utf-8', errors='replace')
        elif _sys == 'Windows':
            # Phase 7: Get-Clipboard emits CF_UNICODETEXT as a UTF-16 string.
            # We force PowerShell's output stream to UTF-8 ([Console]::OutputEncoding)
            # so we can decode the bytes cleanly without relying on the system
            # codepage. Out-String adds a trailing newline — we rstrip it.
            ps_script = (
                '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;'
                'Get-Clipboard | Out-String'
            )
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_script],
                capture_output=True, timeout=3,
            )
            if r.returncode == 0:
                # Decode the UTF-8 PowerShell output. rstrip the trailing
                # \r\n that Out-String unconditionally appends.
                text = r.stdout.decode('utf-8', errors='replace')
                if text.endswith('\r\n'):
                    text = text[:-2]
                elif text.endswith('\n'):
                    text = text[:-1]
                return text
            # Fallback: legacy Get-Clipboard (text mode, codepage-dependent).
            # Only used if the explicit-UTF-8 path fails (shouldn't happen
            # on any modern Windows install).
            r = subprocess.run(['powershell', '-NoProfile', '-Command', 'Get-Clipboard'],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                return r.stdout
    except Exception:
        pass
    return None
