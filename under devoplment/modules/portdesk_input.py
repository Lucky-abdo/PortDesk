"""
portdesk_input.py — Native platform input control for PortDesk.

This module replaces the pyautogui dependency with native platform implementations
using only the Python standard library (ctypes, subprocess, os, time, threading, platform).

Supported platforms:
  - Windows: ctypes + win32 SendInput API
  - Linux:   subprocess + xdotool
  - macOS:   subprocess + osascript / cliclick

All public functions are thread-safe (an internal RLock is used so that the
server's _pyautogui_lock pattern continues to work correctly).

Module-level configuration:
  FAILSAFE = False   — always False for remote-desktop use
  PAUSE    = 0       — always 0 (no artificial pauses)
"""
from __future__ import annotations

import os
import platform
import struct
import subprocess
import threading
import time
from collections import namedtuple

# ---------------------------------------------------------------------------
# Module-level configuration (matches pyautogui interface)
# ---------------------------------------------------------------------------
FAILSAFE = False
PAUSE = 0

# Named tuple returned by position()
_Point = namedtuple("Point", ["x", "y"])

# Internal lock for thread-safety
_lock = threading.RLock()

# ---------------------------------------------------------------------------
# Platform detection (once, at import time)
# ---------------------------------------------------------------------------
_PLATFORM = platform.system().lower()  # 'windows', 'linux', 'darwin'

# ---------------------------------------------------------------------------
# Key-name mappings
# ---------------------------------------------------------------------------

# --- Windows Virtual Key Codes -------------------------------------------
_WIN_VK = {
    # Modifiers
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B, "winleft": 0x5B, "command": 0x5B, "cmd": 0x5B,
    "winright": 0x5C,
    # Navigation / editing
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E, "del": 0x2E,
    "escape": 0x1B, "esc": 0x1B,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    # Arrow keys
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    # Function keys
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    # Special
    "printscreen": 0x2C, "prntscrn": 0x2C,
    "scrolllock": 0x91,
    "pause": 0x13,
    "capslock": 0x14,
    "numlock": 0x90,
    # Numpad
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    # Media / volume keys (VK_* media controls)
    "volumemute": 0xAD, "volumedown": 0xAE, "volumeup": 0xAF,
    "nexttrack": 0xB0, "prevtrack": 0xB1, "prevtrack2": 0xB1,
    "stop": 0xB2, "playpause": 0xB3, "mediaplaypause": 0xB3,
    "medianext": 0xB0, "mediaprev": 0xB1, "mediastop": 0xB2,
}

# Keys that MUST carry the extended-key bit on Windows (E0-prefixed scancodes).
# Without KEYEVENTF_EXTENDEDKEY many apps ignore arrows / nav / media keys.
_WIN_EXTENDED = {
    0x21, 0x22, 0x23, 0x24,        # PageUp PageDown End Home
    0x25, 0x26, 0x27, 0x28,        # Left Up Right Down
    0x2D, 0x2E,                    # Insert Delete
    0x5B, 0x5C, 0x5D,              # LWin RWin Apps
    0xAD, 0xAE, 0xAF,              # volume mute/down/up
    0xB0, 0xB1, 0xB2, 0xB3,        # media next/prev/stop/playpause
}

# Add a-z and 0-9
for _c in "abcdefghijklmnopqrstuvwxyz":
    _WIN_VK[_c] = 0x41 + (ord(_c) - ord("a"))
for _d in range(10):
    _WIN_VK[str(_d)] = 0x30 + _d

# --- Linux xdotool key names --------------------------------------------
_XDO_KEY = {
    "ctrl": "ctrl", "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "super", "winleft": "super", "command": "super", "cmd": "super",
    "winright": "super_r",
    "enter": "Return", "return": "Return",
    "tab": "Tab",
    "space": "space",
    "backspace": "BackSpace",
    "delete": "Delete", "del": "Delete",
    "escape": "Escape", "esc": "Escape",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
    "printscreen": "Print", "prntscrn": "Print",
    "scrolllock": "Scroll_Lock",
    "pause": "Pause",
    "capslock": "Caps_Lock",
    "numlock": "Num_Lock",
    # Media / volume keys (X11 XF86 keysyms used by xdotool)
    "volumemute": "XF86AudioMute", "volumedown": "XF86AudioLowerVolume",
    "volumeup": "XF86AudioRaiseVolume",
    "playpause": "XF86AudioPlay", "mediaplaypause": "XF86AudioPlay",
    "nexttrack": "XF86AudioNext", "prevtrack": "XF86AudioPrev",
    "stop": "XF86AudioStop", "mediastop": "XF86AudioStop",
    "medianext": "XF86AudioNext", "mediaprev": "XF86AudioPrev",
}

# Add a-z and 0-9
for _c in "abcdefghijklmnopqrstuvwxyz":
    _XDO_KEY[_c] = _c
for _d in range(10):
    _XDO_KEY[str(_d)] = str(_d)

# --- macOS key codes ----------------------------------------------------
_MAC_KEYCODE = {
    "ctrl": 0x3B, "control": 0x3B,
    "alt": 0x3A, "option": 0x3A,
    "shift": 0x38,
    "win": 0x37, "winleft": 0x37, "command": 0x37, "cmd": 0x37,
    "winright": 0x36,
    "enter": 0x24, "return": 0x24,
    "tab": 0x30,
    "space": 0x31,
    "backspace": 0x33,
    "delete": 0x75, "del": 0x75,
    "escape": 0x35, "esc": 0x35,
    "insert": 0x72,
    "home": 0x73,
    "end": 0x77,
    "pageup": 0x74,
    "pagedown": 0x79,
    "up": 0x7E, "down": 0x7D, "left": 0x7B, "right": 0x7C,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76,
    "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64,
    "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "printscreen": 0x69, "prntscrn": 0x69,
    "scrolllock": 0x6C,
    "pause": 0x71,
    "capslock": 0x39,
    "numlock": 0x47,
}

# Add a-z (macOS key codes for letters)
_mac_letter_codes = {
    "a": 0x00, "b": 0x0B, "c": 0x08, "d": 0x02, "e": 0x0E,
    "f": 0x03, "g": 0x05, "h": 0x04, "i": 0x22, "j": 0x26,
    "k": 0x28, "l": 0x25, "m": 0x2E, "n": 0x2D, "o": 0x1F,
    "p": 0x23, "q": 0x0C, "r": 0x0F, "s": 0x01, "t": 0x11,
    "u": 0x20, "v": 0x09, "w": 0x0D, "x": 0x07, "y": 0x10,
    "z": 0x06,
}
for _c, _code in _mac_letter_codes.items():
    _MAC_KEYCODE[_c] = _code

# Number key codes (top row)
_mac_number_codes = {
    "0": 0x1D, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
    "5": 0x17, "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19,
}
for _d, _code in _mac_number_codes.items():
    _MAC_KEYCODE[_d] = _code


# =========================================================================
#  WINDOWS IMPLEMENTATION  (ctypes + SendInput)
# =========================================================================
if _PLATFORM == "windows":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    _SM_SWAPBUTTON = 23
    _BUTTONS_SWAPPED = bool(user32.GetSystemMetrics(_SM_SWAPBUTTON))

    # --- Constants --------------------------------------------------------
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    MOUSEEVENTF_MOVE       = 0x0001
    MOUSEEVENTF_LEFTDOWN   = 0x0002
    MOUSEEVENTF_LEFTUP     = 0x0004
    MOUSEEVENTF_RIGHTDOWN  = 0x0008
    MOUSEEVENTF_RIGHTUP    = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP   = 0x0040
    MOUSEEVENTF_WHEEL      = 0x0800
    MOUSEEVENTF_ABSOLUTE   = 0x8000

    WHEEL_DELTA = 120

    KEYEVENTF_KEYUP   = 0x0002
    KEYEVENTF_SCANCODE = 0x0008

    # --- Structures -------------------------------------------------------
    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("union", _INPUT_UNION),
        ]

    def _make_mouse_input(dx=0, dy=0, mouse_data=0, flags=0):
        """Create an INPUT structure for a mouse event."""
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp.union.mi = MOUSEINPUT(
            dx=dx, dy=dy,
            mouseData=mouse_data,
            dwFlags=flags,
            time=0,
            dwExtraInfo=ctypes.cast(None, ctypes.POINTER(ctypes.c_ulong)),
        )
        return inp

    def _make_keyboard_input(vk=0, scan=0, flags=0):
        """Create an INPUT structure for a keyboard event."""
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki = KEYBDINPUT(
            wVk=vk,
            wScan=scan,
            dwFlags=flags,
            time=0,
            dwExtraInfo=ctypes.cast(None, ctypes.POINTER(ctypes.c_ulong)),
        )
        return inp

    def _send(*inputs):
        """Send one or more INPUT structures via SendInput."""
        n = len(inputs)
        arr = (INPUT * n)(*inputs)
        user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))

    def _vk_for_key(key):
        """Resolve a pyautogui key name to a Windows VK code."""
        k = key.lower().strip()
        if k in _WIN_VK:
            return _WIN_VK[k]
        # Single character fallback
        if len(k) == 1:
            return ord(k.upper())
        return 0

    def _scan_for_vk(vk):
        """Get scan code for a VK code (MapVirtualKey)."""
        return user32.MapVirtualKeyW(vk, 0) & 0xFF

    KEYEVENTF_EXTENDEDKEY = 0x0001

    def _emit_key(vk, down=True):
        """Emit one key event with correct flags.

        - Extended keys (arrows/nav/media) get KEYEVENTF_EXTENDEDKEY.
        - Media/volume keys have NO useful scancode → send by VK (no SCANCODE
          flag) so they actually register. Others use scancode for reliability.
        """
        if vk == 0:
            return
        up = KEYEVENTF_KEYUP if not down else 0
        scan = _scan_for_vk(vk)
        if scan == 0:
            # No scancode (e.g. media keys) — send by virtual-key code.
            flags = up
            if vk in _WIN_EXTENDED:
                flags |= KEYEVENTF_EXTENDEDKEY
            _send(_make_keyboard_input(vk=vk, scan=0, flags=flags))
        else:
            flags = KEYEVENTF_SCANCODE | up
            if vk in _WIN_EXTENDED:
                flags |= KEYEVENTF_EXTENDEDKEY
            _send(_make_keyboard_input(vk=vk, scan=scan, flags=flags))

    # --- Public API -------------------------------------------------------

    def moveRel(dx, dy, duration=0):
        """Move mouse relative to current position."""
        with _lock:
            if duration > 0:
                steps = max(int(duration / 0.01), 1)
                step_dx = dx / steps
                step_dy = dy / steps
                for i in range(steps):
                    _send(_make_mouse_input(dx=int(step_dx), dy=int(step_dy), flags=MOUSEEVENTF_MOVE))
                    time.sleep(duration / steps)
            else:
                _send(_make_mouse_input(dx=int(dx), dy=int(dy), flags=MOUSEEVENTF_MOVE))

    def click():
        with _lock:
            _dn = MOUSEEVENTF_RIGHTDOWN if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTDOWN
            _up = MOUSEEVENTF_RIGHTUP   if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTUP
            _send(_make_mouse_input(flags=_dn), _make_mouse_input(flags=_up))

    def rightClick():
        with _lock:
            _dn = MOUSEEVENTF_LEFTDOWN if _BUTTONS_SWAPPED else MOUSEEVENTF_RIGHTDOWN
            _up = MOUSEEVENTF_LEFTUP   if _BUTTONS_SWAPPED else MOUSEEVENTF_RIGHTUP
            _send(_make_mouse_input(flags=_dn), _make_mouse_input(flags=_up))

    def doubleClick():
        with _lock:
            _dn = MOUSEEVENTF_RIGHTDOWN if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTDOWN
            _up = MOUSEEVENTF_RIGHTUP   if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTUP
            _send(
                _make_mouse_input(flags=_dn), _make_mouse_input(flags=_up),
                _make_mouse_input(flags=_dn), _make_mouse_input(flags=_up),
            )

    def mouseDown():
        with _lock:
            _f = MOUSEEVENTF_RIGHTDOWN if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTDOWN
            _send(_make_mouse_input(flags=_f))

    def mouseUp():
        with _lock:
            _f = MOUSEEVENTF_RIGHTUP if _BUTTONS_SWAPPED else MOUSEEVENTF_LEFTUP
            _send(_make_mouse_input(flags=_f))

    def middleClick():
        with _lock:
            _send(
                _make_mouse_input(flags=MOUSEEVENTF_MIDDLEDOWN),
                _make_mouse_input(flags=MOUSEEVENTF_MIDDLEUP),
            )

    def scroll(dy):
        with _lock:
            amount = int(dy) * WHEEL_DELTA
            _send(_make_mouse_input(mouse_data=amount, flags=MOUSEEVENTF_WHEEL))

    def position():
        """Return named tuple (x, y) of current mouse position."""
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return _Point(pt.x, pt.y)

    def press(key):
        """Press and release a single key."""
        with _lock:
            vk = _vk_for_key(key)
            _emit_key(vk, down=True)
            _emit_key(vk, down=False)

    def hotkey(*keys):
        """Press multiple keys simultaneously (e.g., Ctrl+C)."""
        with _lock:
            vks = [_vk_for_key(k) for k in keys]
            for vk in vks:            # press in order
                _emit_key(vk, down=True)
            for vk in reversed(vks):  # release in reverse
                _emit_key(vk, down=False)

    def keyDown(key):
        """Press and hold a key."""
        with _lock:
            _emit_key(_vk_for_key(key), down=True)

    def keyUp(key):
        """Release a held key."""
        with _lock:
            _emit_key(_vk_for_key(key), down=False)

    def write(text, interval=0.02):
        """Type a string character by character."""
        KEYEVENTF_UNICODE = 0x04
        with _lock:
            for ch in text:
                vk = _vk_for_key(ch)
                scan = _scan_for_vk(vk) if vk else ord(ch.upper())
                if vk and len(ch) == 1 and ch.isalnum():
                    _send(
                        _make_keyboard_input(vk=vk, scan=scan, flags=KEYEVENTF_SCANCODE),
                        _make_keyboard_input(vk=vk, scan=scan, flags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP),
                    )
                elif ord(ch) > 0xFFFF:
                    # Supplementary plane character — encode as UTF-16LE surrogate pair
                    encoded = ch.encode('utf-16-le')
                    high = struct.unpack_from('<H', encoded, 0)[0]
                    low  = struct.unpack_from('<H', encoded, 2)[0]
                    _send(_make_keyboard_input(vk=0, scan=high, flags=KEYEVENTF_UNICODE))
                    _send(_make_keyboard_input(vk=0, scan=high, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
                    _send(_make_keyboard_input(vk=0, scan=low,  flags=KEYEVENTF_UNICODE))
                    _send(_make_keyboard_input(vk=0, scan=low,  flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
                else:
                    _send(_make_keyboard_input(vk=0, scan=ord(ch), flags=KEYEVENTF_UNICODE))
                    _send(_make_keyboard_input(vk=0, scan=ord(ch), flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
                if interval > 0:
                    time.sleep(interval)


# =========================================================================
#  LINUX IMPLEMENTATION  (subprocess + xdotool)
# =========================================================================
elif _PLATFORM == "linux":

    # ── Fast input backends (preferred) ─────────────────────────────────────
    # Priority: python-xlib XTest (X11, ~0.1ms, no subprocess) → evdev/uinput
    # (kernel-level, works on Wayland too) → xdotool subprocess (universal
    # fallback). Each is probed once; failures degrade gracefully.
    _XTEST = None        # python-xlib display when available
    _XTEST_KEYSYM = None  # XK module
    _UINPUT_DEV = None    # evdev/uinput device when available
    _UINPUT_MOD = None

    def _init_xtest():
        global _XTEST, _XTEST_KEYSYM
        try:
            from Xlib import display as _xd, X as _X, XK as _XK
            from Xlib.ext import xtest as _xt  # noqa: F401  (import proves availability)
            _XTEST = _xd.Display(os.environ.get('DISPLAY', ':0'))
            _XTEST_KEYSYM = _XK
            return True
        except Exception:
            _XTEST = None
            return False

    def _init_uinput():
        global _UINPUT_DEV, _UINPUT_MOD
        try:
            import evdev
            from evdev import UInput, ecodes as e
            caps = {
                e.EV_KEY: (list(range(e.KEY_RESERVED, e.KEY_MICMUTE)) +
                           [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE]),
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
            }
            _UINPUT_DEV = UInput(caps, name='portdesk-virtual-input')
            _UINPUT_MOD = e
            return True
        except Exception:
            _UINPUT_DEV = None
            return False

    # Probe order: XTest first (best for X11 desktops), then uinput (Wayland/
    # headless). Wayland sessions usually lack a usable XTest, so uinput wins there.
    _wayland_session = bool(os.environ.get('WAYLAND_DISPLAY')) or \
                       os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
    _have_xtest = (not _wayland_session) and _init_xtest()
    _have_uinput = _init_uinput() if (_wayland_session or not _have_xtest) else False

    def _xtest_button(name):
        return {'left': 1, 'right': 3, 'middle': 2}.get(name, 1)

    def _xtest_move(dx, dy):
        from Xlib import X as _X
        # XTest relative motion
        _XTEST.xtest.fake_input(_X.MotionNotify, x=int(dx), y=int(dy), detail=True)
        _XTEST.sync()

    def _xtest_click(btn=1, press_only=None):
        from Xlib import X as _X
        if press_only is None or press_only == 'down':
            _XTEST.xtest.fake_input(_X.ButtonPress, btn); _XTEST.sync()
        if press_only is None or press_only == 'up':
            _XTEST.xtest.fake_input(_X.ButtonRelease, btn); _XTEST.sync()

    def _xtest_keycode(keyname):
        # map our key name → X keysym → keycode
        ks_name = _XDO_KEY.get(keyname.lower().strip(), keyname)
        # X keysym strings differ slightly; try direct + title-case
        for cand in (ks_name, ks_name.capitalize(), keyname):
            ks = _XTEST_KEYSYM.string_to_keysym(cand)
            if ks:
                kc = _XTEST.keysym_to_keycode(ks)
                if kc:
                    return kc
        return None

    def _xtest_key(keyname, press_only=None):
        from Xlib import X as _X
        kc = _xtest_keycode(keyname)
        if not kc:
            return False
        if press_only is None or press_only == 'down':
            _XTEST.xtest.fake_input(_X.KeyPress, kc); _XTEST.sync()
        if press_only is None or press_only == 'up':
            _XTEST.xtest.fake_input(_X.KeyRelease, kc); _XTEST.sync()
        return True

    def _uinput_move(dx, dy):
        e = _UINPUT_MOD
        if dx: _UINPUT_DEV.write(e.EV_REL, e.REL_X, int(dx))
        if dy: _UINPUT_DEV.write(e.EV_REL, e.REL_Y, int(dy))
        _UINPUT_DEV.syn()

    def _uinput_btn(name, down):
        e = _UINPUT_MOD
        code = {'left': e.BTN_LEFT, 'right': e.BTN_RIGHT, 'middle': e.BTN_MIDDLE}.get(name, e.BTN_LEFT)
        _UINPUT_DEV.write(e.EV_KEY, code, 1 if down else 0); _UINPUT_DEV.syn()

    def _uinput_scroll(notches):
        e = _UINPUT_MOD
        _UINPUT_DEV.write(e.EV_REL, e.REL_WHEEL, int(notches)); _UINPUT_DEV.syn()

    def _uinput_keycode(keyname):
        e = _UINPUT_MOD
        m = {'ctrl':'KEY_LEFTCTRL','control':'KEY_LEFTCTRL','alt':'KEY_LEFTALT',
             'shift':'KEY_LEFTSHIFT','win':'KEY_LEFTMETA','cmd':'KEY_LEFTMETA',
             'enter':'KEY_ENTER','return':'KEY_ENTER','backspace':'KEY_BACKSPACE',
             'tab':'KEY_TAB','space':'KEY_SPACE','escape':'KEY_ESC','esc':'KEY_ESC',
             'delete':'KEY_DELETE','del':'KEY_DELETE','up':'KEY_UP','down':'KEY_DOWN',
             'left':'KEY_LEFT','right':'KEY_RIGHT','home':'KEY_HOME','end':'KEY_END',
             'pageup':'KEY_PAGEUP','pagedown':'KEY_PAGEDOWN','insert':'KEY_INSERT'}
        k = keyname.lower().strip()
        name = m.get(k)
        if not name:
            if len(k) == 1 and k.isalnum(): name = f'KEY_{k.upper()}'
            elif k.startswith('f') and k[1:].isdigit(): name = f'KEY_{k.upper()}'
        return getattr(e, name, None) if name else None

    def _uinput_key(keyname, down):
        e = _UINPUT_MOD
        code = _uinput_keycode(keyname)
        if code is None: return False
        _UINPUT_DEV.write(e.EV_KEY, code, 1 if down else 0); _UINPUT_DEV.syn()
        return True

    def _xdotool(*args):
        """Run xdotool command; silently ignore if not installed."""
        try:
            subprocess.run(
                ["xdotool"] + list(args),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _xdotool_output(*args):
        """Run xdotool and return stdout; return empty string on failure."""
        try:
            result = subprocess.run(
                ["xdotool"] + list(args),
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout.strip()
        except FileNotFoundError:
            return ""

    def _xdo_key_name(key):
        k = key.lower().strip()
        if k in _XDO_KEY:
            return _XDO_KEY[k]
        if len(k) == 1:
            return k
        return k

    def _detect_linux_button_map():
        """Return (left_btn, right_btn, middle_btn) as xdotool button strings.
        Reads xmodmap -pp to honour OS-level button remapping (e.g. left-handed swap).
        Falls back to (1, 3, 2) on any failure (Wayland, xmodmap absent, etc.)."""
        try:
            r = subprocess.run(
                ["xmodmap", "-pp"],
                capture_output=True, text=True, check=False, timeout=2
            )
            mapping = {}
            for line in r.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    mapping[int(parts[0])] = int(parts[1])
            if mapping:
                return str(mapping.get(1, 1)), str(mapping.get(3, 3)), str(mapping.get(2, 2))
        except Exception:
            pass
        return "1", "3", "2"

    _BTN_LEFT, _BTN_RIGHT, _BTN_MIDDLE = _detect_linux_button_map()

    # --- Public API -------------------------------------------------------

    def moveRel(dx, dy, duration=0):
        """Move mouse relative to current position. Fast path: XTest/uinput."""
        with _lock:
            dx, dy = int(dx), int(dy)
            if _have_xtest:
                try: _xtest_move(dx, dy); return
                except Exception: pass
            if _have_uinput:
                try: _uinput_move(dx, dy); return
                except Exception: pass
            if duration > 0:
                steps = max(int(duration / 0.01), 1)
                for i in range(steps):
                    _xdotool("mousemove_relative", "--", str(int(dx/steps)), str(int(dy/steps)))
                    time.sleep(duration / steps)
            else:
                _xdotool("mousemove_relative", "--", str(dx), str(dy))

    def _lin_click(btn_name, xdo_btn):
        if _have_xtest:
            try: _xtest_click(_xtest_button(btn_name)); return
            except Exception: pass
        if _have_uinput:
            try: _uinput_btn(btn_name, True); _uinput_btn(btn_name, False); return
            except Exception: pass
        _xdotool("click", xdo_btn)

    def click():
        with _lock: _lin_click('left', _BTN_LEFT)

    def rightClick():
        with _lock: _lin_click('right', _BTN_RIGHT)

    def doubleClick():
        with _lock:
            if _have_xtest:
                try:
                    _xtest_click(1); _xtest_click(1); return
                except Exception: pass
            if _have_uinput:
                try:
                    _uinput_btn('left',True);_uinput_btn('left',False)
                    _uinput_btn('left',True);_uinput_btn('left',False); return
                except Exception: pass
            _xdotool("click", "--repeat", "2", _BTN_LEFT)

    def middleClick():
        with _lock: _lin_click('middle', _BTN_MIDDLE)

    def scroll(dy):
        """Scroll mouse wheel. Positive = up, negative = down."""
        with _lock:
            n = int(dy)
            if n == 0: return
            if _have_xtest:
                try:
                    from Xlib import X as _X
                    btn = 4 if n > 0 else 5
                    for _ in range(abs(n)):
                        _XTEST.xtest.fake_input(_X.ButtonPress, btn)
                        _XTEST.xtest.fake_input(_X.ButtonRelease, btn)
                    _XTEST.sync(); return
                except Exception: pass
            if _have_uinput:
                try: _uinput_scroll(n); return
                except Exception: pass
            btn = "4" if n > 0 else "5"
            for _ in range(abs(n)):
                _xdotool("click", btn)

    def mouseDown():
        with _lock:
            if _have_xtest:
                try: _xtest_click(1, 'down'); return
                except Exception: pass
            if _have_uinput:
                try: _uinput_btn('left', True); return
                except Exception: pass
            _xdotool("mousedown", _BTN_LEFT)

    def mouseUp():
        with _lock:
            if _have_xtest:
                try: _xtest_click(1, 'up'); return
                except Exception: pass
            if _have_uinput:
                try: _uinput_btn('left', False); return
                except Exception: pass
            _xdotool("mouseup", _BTN_LEFT)

    def position():
        """Return named tuple (x, y) of current mouse position."""
        output = _xdotool_output("getmouselocation")
        if output:
            # Format: x:123 y:456 screen:0 window:789
            parts = {}
            for token in output.split():
                if ":" in token:
                    k, v = token.split(":", 1)
                    parts[k] = int(v)
            return _Point(parts.get("x", 0), parts.get("y", 0))
        return _Point(0, 0)

    def press(key):
        """Press and release a single key. Fast path: XTest/uinput."""
        with _lock:
            if _have_xtest:
                try:
                    if _xtest_key(key): return
                except Exception: pass
            if _have_uinput:
                try:
                    if _uinput_key(key, True): _uinput_key(key, False); return
                except Exception: pass
            _xdotool("key", _xdo_key_name(key))

    def hotkey(*keys):
        """Press multiple keys simultaneously (e.g., Ctrl+C)."""
        with _lock:
            if _have_xtest:
                try:
                    ok = True
                    for k in keys:
                        if not _xtest_key(k, 'down'): ok = False; break
                    for k in reversed(keys):
                        _xtest_key(k, 'up')
                    if ok: return
                except Exception: pass
            if _have_uinput:
                try:
                    ok = True
                    for k in keys:
                        if not _uinput_key(k, True): ok = False; break
                    for k in reversed(keys):
                        _uinput_key(k, False)
                    if ok: return
                except Exception: pass
            combo = "+".join(_xdo_key_name(k) for k in keys)
            _xdotool("key", combo)

    def keyDown(key):
        """Press and hold a key."""
        with _lock:
            if _have_xtest:
                try:
                    if _xtest_key(key, 'down'): return
                except Exception: pass
            if _have_uinput:
                try:
                    if _uinput_key(key, True): return
                except Exception: pass
            _xdotool("keydown", _xdo_key_name(key))

    def keyUp(key):
        """Release a held key."""
        with _lock:
            if _have_xtest:
                try:
                    if _xtest_key(key, 'up'): return
                except Exception: pass
            if _have_uinput:
                try:
                    if _uinput_key(key, False): return
                except Exception: pass
            _xdotool("keyup", _xdo_key_name(key))

    def write(text, interval=0.02):
        """Type a string character by character."""
        with _lock:
            # xdotool type handles the whole string, but we emulate
            # character-by-character for interval support
            for ch in text:
                _xdotool("type", "--clearmodifiers", ch)
                if interval > 0:
                    time.sleep(interval)


# =========================================================================
#  macOS IMPLEMENTATION  (subprocess + osascript / cliclick)
# =========================================================================
elif _PLATFORM == "darwin":

    # ── Quartz CGEvent backend (preferred — kernel-level, no subprocess) ──
    # Mirrors Windows SendInput quality. Falls back to cliclick/osascript when
    # pyobjc-framework-Quartz is unavailable or Accessibility perms are missing.
    _QZ = None
    def _init_quartz():
        global _QZ
        try:
            import Quartz as _Q
            _QZ = _Q
            return True
        except Exception:
            _QZ = None
            return False
    _have_quartz = _init_quartz()

    _MAC_VK = {
        'a':0,'s':1,'d':2,'f':3,'h':4,'g':5,'z':6,'x':7,'c':8,'v':9,'b':11,
        'q':12,'w':13,'e':14,'r':15,'y':16,'t':17,'o':31,'u':32,'i':34,'p':35,
        'l':37,'j':38,'k':40,'n':45,'m':46,
        '1':18,'2':19,'3':20,'4':21,'5':23,'6':22,'7':26,'8':28,'9':25,'0':29,
        'enter':36,'return':36,'tab':48,'space':49,'delete':51,'backspace':51,
        'escape':53,'esc':53,'left':123,'right':124,'down':125,'up':126,
        'home':115,'end':119,'pageup':116,'pagedown':121,
        'f1':122,'f2':120,'f3':99,'f4':118,'f5':96,'f6':97,'f7':98,'f8':100,
        'f9':101,'f10':109,'f11':103,'f12':111,
    }
    _MAC_MODS = {'shift':0x20000,'ctrl':0x40000,'control':0x40000,'alt':0x80000,
                 'option':0x80000,'win':0x100000,'cmd':0x100000,'command':0x100000}

    def _qz_cursor():
        return _QZ.CGEventGetLocation(_QZ.CGEventCreate(None))

    def _qz_move(dx, dy):
        loc = _qz_cursor()
        ev = _QZ.CGEventCreateMouseEvent(None, _QZ.kCGEventMouseMoved, (loc.x + dx, loc.y + dy), 0)
        _QZ.CGEventPost(_QZ.kCGHIDEventTap, ev)

    def _qz_click(button='left', clicks=1):
        loc = _qz_cursor()
        if button == 'right':
            down, up, btn = _QZ.kCGEventRightMouseDown, _QZ.kCGEventRightMouseUp, _QZ.kCGMouseButtonRight
        elif button == 'middle':
            down, up, btn = _QZ.kCGEventOtherMouseDown, _QZ.kCGEventOtherMouseUp, _QZ.kCGMouseButtonCenter
        else:
            down, up, btn = _QZ.kCGEventLeftMouseDown, _QZ.kCGEventLeftMouseUp, _QZ.kCGMouseButtonLeft
        for i in range(clicks):
            evd = _QZ.CGEventCreateMouseEvent(None, down, (loc.x, loc.y), btn)
            if clicks > 1: _QZ.CGEventSetIntegerValueField(evd, _QZ.kCGMouseEventClickState, i + 1)
            _QZ.CGEventPost(_QZ.kCGHIDEventTap, evd)
            evu = _QZ.CGEventCreateMouseEvent(None, up, (loc.x, loc.y), btn)
            if clicks > 1: _QZ.CGEventSetIntegerValueField(evu, _QZ.kCGMouseEventClickState, i + 1)
            _QZ.CGEventPost(_QZ.kCGHIDEventTap, evu)

    def _qz_button(button='left', down=True):
        loc = _qz_cursor()
        if button == 'right':
            ev_t, btn = (_QZ.kCGEventRightMouseDown if down else _QZ.kCGEventRightMouseUp), _QZ.kCGMouseButtonRight
        else:
            ev_t, btn = (_QZ.kCGEventLeftMouseDown if down else _QZ.kCGEventLeftMouseUp), _QZ.kCGMouseButtonLeft
        ev = _QZ.CGEventCreateMouseEvent(None, ev_t, (loc.x, loc.y), btn)
        _QZ.CGEventPost(_QZ.kCGHIDEventTap, ev)

    def _qz_scroll(notches):
        ev = _QZ.CGEventCreateScrollWheelEvent(None, _QZ.kCGScrollEventUnitLine, 1, int(notches))
        _QZ.CGEventPost(_QZ.kCGHIDEventTap, ev)

    def _qz_key(keyname, down=True, mods=0):
        vk = _MAC_VK.get(keyname.lower().strip())
        if vk is None:
            return False
        ev = _QZ.CGEventCreateKeyboardEvent(None, vk, down)
        if mods: _QZ.CGEventSetFlags(ev, mods)
        _QZ.CGEventPost(_QZ.kCGHIDEventTap, ev)
        return True

    def _osascript(script):
        """Run an AppleScript string via osascript."""
        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _osascript_output(script):
        """Run an AppleScript and return stdout."""
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.stdout.strip()
        except FileNotFoundError:
            return ""

    # Check if cliclick is available for mouse operations
    _HAS_CLICLICK = False
    try:
        result = subprocess.run(
            ["which", "cliclick"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            _HAS_CLICLICK = True
    except FileNotFoundError:
        pass

    def _cliclick(*args):
        try:
            subprocess.run(
                ["cliclick"] + list(args),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            pass

    def _detect_mac_button_swap():
        """Return True if the user has swapped primary/secondary mouse buttons
        via System Preferences → Mouse.  Reads the global preference key that
        macOS writes when the swap is toggled."""
        try:
            r = subprocess.run(
                ["defaults", "read", "-g", "com.apple.mouse.swapLeftRightButton"],
                capture_output=True, text=True, check=False, timeout=2
            )
            return r.stdout.strip() == "1"
        except Exception:
            pass
        return False

    _BUTTONS_SWAPPED = _detect_mac_button_swap()

    def _mac_key_code(key):
        """Resolve a pyautogui key name to a macOS key code."""
        k = key.lower().strip()
        if k in _MAC_KEYCODE:
            return _MAC_KEYCODE[k]
        return None

    def _mac_keystroke(key):
        """Get the keystroke string for AppleScript (for printable chars)."""
        k = key.lower().strip()
        if len(k) == 1:
            return k
        # Map special keys to their AppleScript keystroke names
        _special = {
            "enter": "return", "return": "return",
            "tab": "tab",
            "space": " ",
            "backspace": "delete",
            "escape": "escape", "esc": "escape",
        }
        return _special.get(k)

    # --- Public API -------------------------------------------------------

    def moveRel(dx, dy, duration=0):
        """Move mouse relative to current position. Fast path: Quartz CGEvent."""
        with _lock:
            if _have_quartz and duration <= 0:
                try: _qz_move(int(dx), int(dy)); return
                except Exception: pass
            if duration > 0:
                # Get current position first
                pos = position()
                target_x = pos.x + int(dx)
                target_y = pos.y + int(dy)
                steps = max(int(duration / 0.01), 1)
                for i in range(1, steps + 1):
                    frac = i / steps
                    cx = int(pos.x + dx * frac)
                    cy = int(pos.y + dy * frac)
                    if _HAS_CLICLICK:
                        _cliclick(f"m:{cx},{cy}")
                    else:
                        _osascript(
                            f'tell application "System Events" to set cursor to {{{cx},{cy}}}'
                        )
                    time.sleep(duration / steps)
            else:
                # For relative move without duration, use cliclick if available
                if _HAS_CLICLICK:
                    # cliclick requires explicit sign: +5 or -5 (never +-5)
                    _sx = f"+{int(dx)}" if int(dx) >= 0 else str(int(dx))
                    _sy = f"+{int(dy)}" if int(dy) >= 0 else str(int(dy))
                    _cliclick(f"m:{_sx},{_sy}")
                else:
                    pos = position()
                    _osascript(
                        f'tell application "System Events" to set cursor to {{{pos.x + int(dx)},{pos.y + int(dy)}}}'
                    )

    def click():
        with _lock:
            if _have_quartz:
                try: _qz_click('right' if _BUTTONS_SWAPPED else 'left'); return
                except Exception: pass
            if _HAS_CLICLICK:
                _cliclick("rc:." if _BUTTONS_SWAPPED else "c:.")
            else:
                _osascript('tell application "System Events" to click')

    def rightClick():
        with _lock:
            if _have_quartz:
                try: _qz_click('left' if _BUTTONS_SWAPPED else 'right'); return
                except Exception: pass
            if _HAS_CLICLICK:
                _cliclick("c:." if _BUTTONS_SWAPPED else "rc:.")
            else:
                _osascript('tell application "System Events" to right click')

    def doubleClick():
        with _lock:
            if _have_quartz:
                try: _qz_click('right' if _BUTTONS_SWAPPED else 'left', clicks=2); return
                except Exception: pass
            if _HAS_CLICLICK:
                if _BUTTONS_SWAPPED:
                    _cliclick("rc:."); time.sleep(0.05); _cliclick("rc:.")
                else:
                    _cliclick("dc:.")
            else:
                _osascript('tell application "System Events" to double click')

    def mouseDown():
        with _lock:
            if _have_quartz:
                try: _qz_button('right' if _BUTTONS_SWAPPED else 'left', down=True); return
                except Exception: pass
            if _HAS_CLICLICK and not _BUTTONS_SWAPPED:
                _cliclick("kd:.")
            else:
                _osascript('tell application "System Events" to mouse down')

    def mouseUp():
        with _lock:
            if _have_quartz:
                try: _qz_button('right' if _BUTTONS_SWAPPED else 'left', down=False); return
                except Exception: pass
            if _HAS_CLICLICK and not _BUTTONS_SWAPPED:
                _cliclick("ku:.")
            else:
                _osascript('tell application "System Events" to mouse up')

    def middleClick():
        with _lock:
            try:
                import Quartz
                pos = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
                ev_down = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventOtherMouseDown, pos, Quartz.kCGMouseButtonCenter)
                ev_up = Quartz.CGEventCreateMouseEvent(
                    None, Quartz.kCGEventOtherMouseUp, pos, Quartz.kCGMouseButtonCenter)
                Quartz.CGEventPost(Quartz.kCGSessionEventTap, ev_down)
                time.sleep(0.05)
                Quartz.CGEventPost(Quartz.kCGSessionEventTap, ev_up)
            except (ImportError, Exception):
                pass

    def scroll(dy):
        with _lock:
            amount = int(dy)
            if _have_quartz:
                try: _qz_scroll(amount); return
                except Exception: pass
            if _HAS_CLICLICK:
                _cliclick(f"scroll:{amount}")
            else:
                direction = "up" if amount > 0 else "down"
                count = abs(amount)
                for _ in range(count):
                    _osascript(
                        f'tell application "System Events" to key code {126 if direction == "up" else 125} using option down'
                    )

    def position():
        """Return named tuple (x, y) of current mouse position. Fast: Quartz."""
        if _have_quartz:
            try:
                loc = _qz_cursor()
                return _Point(int(loc.x), int(loc.y))
            except Exception:
                pass
        # Fallback: osascript
        output = _osascript_output(
            'tell application "System Events" to get {mouseX, mouseY}'
        )
        if output:
            try:
                # Try parsing as "x, y"
                parts = output.replace("{", "").replace("}", "").split(",")
                return _Point(int(parts[0].strip()), int(parts[1].strip()))
            except (ValueError, IndexError):
                pass

        # Fallback: use Python objc or return (0, 0)
        try:
            from Quartz import CGEventGetLocation  # noqa
            loc = CGEventGetLocation()
            return _Point(int(loc.x), int(loc.y))
        except ImportError:
            pass

        return _Point(0, 0)

    def press(key):
        """Press and release a single key. Fast path: Quartz CGEvent."""
        with _lock:
            if _have_quartz:
                try:
                    if _qz_key(key, True): _qz_key(key, False); return
                except Exception: pass
            kc = _mac_key_code(key)
            ks = _mac_keystroke(key)
            if kc is not None:
                _osascript(f'tell application "System Events" to key code {kc}')
            elif ks is not None:
                _osascript(f'tell application "System Events" to keystroke "{ks}"')

    def hotkey(*keys):
        """Press multiple keys simultaneously (e.g., Ctrl+C)."""
        with _lock:
            if _have_quartz:
                try:
                    # Build modifier flag mask + press non-mod keys with it.
                    mask = 0; nonmods = []
                    for k in keys:
                        mk = _MAC_MODS.get(k.lower().strip())
                        if mk: mask |= mk
                        else: nonmods.append(k)
                    ok = True
                    for k in nonmods:
                        if not _qz_key(k, True, mask): ok = False; break
                        _qz_key(k, False, mask)
                    if ok and nonmods: return
                except Exception: pass
            modifiers = []
            non_mod_keys = []

            _modifier_names = {
                "ctrl": "control", "control": "control",
                "alt": "option", "option": "option",
                "shift": "shift",
                "win": "command", "winleft": "command", "command": "command", "cmd": "command",
            }

            for key in keys:
                k = key.lower().strip()
                if k in _modifier_names:
                    modifiers.append(_modifier_names[k])
                else:
                    non_mod_keys.append(key)

            using_clause = ""
            if modifiers:
                using_clause = " using {" + ", ".join(f"{m} down" for m in modifiers) + "}"

            for key in non_mod_keys:
                kc = _mac_key_code(key)
                ks = _mac_keystroke(key)
                if kc is not None:
                    _osascript(f'tell application "System Events" to key code {kc}{using_clause}')
                elif ks is not None:
                    _osascript(f'tell application "System Events" to keystroke "{ks}"{using_clause}')

    def keyDown(key):
        """Press and hold a key."""
        with _lock:
            if _have_quartz:
                try:
                    if _qz_key(key, True): return
                except Exception: pass
            kc = _mac_key_code(key)
            if kc is not None:
                _osascript(f'tell application "System Events" to key down {kc}')

    def keyUp(key):
        """Release a held key."""
        with _lock:
            if _have_quartz:
                try:
                    if _qz_key(key, False): return
                except Exception: pass
            kc = _mac_key_code(key)
            if kc is not None:
                _osascript(f'tell application "System Events" to key up {kc}')

    def write(text, interval=0.02):
        """Type a string character by character."""
        with _lock:
            for ch in text:
                if ch == '\n':
                    _osascript('tell application "System Events" to key code 36')  # Return
                elif ch == '\t':
                    _osascript('tell application "System Events" to key code 48')  # Tab
                else:
                    escaped = ch.replace("\\", "\\\\").replace('"', '\\"')
                    _osascript(f'tell application "System Events" to keystroke "{escaped}"')
                if interval > 0:
                    time.sleep(interval)


# =========================================================================
#  FALLBACK — Unsupported platform
# =========================================================================
else:

    def _unsupported(*args, **kwargs):
        pass

    moveRel = _unsupported
    click = _unsupported
    rightClick = _unsupported
    doubleClick = _unsupported
    middleClick = _unsupported
    scroll = _unsupported
    mouseDown = _unsupported
    mouseUp = _unsupported

    def position():
        return _Point(0, 0)

    press = _unsupported
    hotkey = _unsupported
    keyDown = _unsupported
    keyUp  = _unsupported
    write  = _unsupported