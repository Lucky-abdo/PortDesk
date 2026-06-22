# ── Custom HTTP/WebSocket server — replaces fastapi + starlette + uvicorn ────

# ══════════════════════════════════════════════════════════════════════════════
# FLEXIBLE MODULE BOOTSTRAP — runs BEFORE any project imports.
#
# Problem:  SERVER.py sits in the project root while the pd_* / portdesk_*
#           modules live in a sub-directory (e.g. "modules/").  Python can't
#           find them unless the sub-directory is on sys.path.
# Solution: Auto-discover the modules directory and inject it into sys.path.
#           Also register *this* file as the canonical module name so that
#           `from portdesk_server import ...` works inside pd_routes.py
#           regardless of what the main file is actually called.
#
# Configurability:
#   • MODULE_DIR_NAMES  — directory names to search (edit if you rename the
#                         folder from "modules" to something else).
#   • SERVER_MODULE_NAME — the name pd_routes.py uses to import back from
#                         this file.  Change it if you rename the import alias.
#   • MODULE_MARKERS    — filenames used to verify a candidate directory is
#                         really the modules folder (so we don't add a random
#                         "modules" dir by accident).  Add/remove as needed.
# ══════════════════════════════════════════════════════════════════════════════
import sys as _sys, os as _os

# ── User-tunable constants (change these if you reorganize) ─────────────────
MODULE_DIR_NAMES   = ('modules', 'src', 'lib')          # directories to search
SERVER_MODULE_NAME = 'portdesk_server'                   # import name for pd_routes
MODULE_MARKERS     = (                                   # files that prove "this is it"
    'portdesk_http.py', 'pd_config.py', 'pd_security.py',
    'pd_state.py', 'pd_crypto.py', 'pd_routes.py',
)

def _bootstrap_module_path():
    """Find and add the modules directory to sys.path.  Returns the path or None."""
    # 1) Directory of the running main script
    main_file = None
    if hasattr(_sys, 'argv') and _sys.argv:
        main_file = _sys.argv[0]
    elif '__main__' in _sys.modules:
        main_file = getattr(_sys.modules['__main__'], '__file__', None)
    start_dir = _os.path.dirname(_os.path.abspath(main_file)) if main_file else _os.getcwd()

    # 2) Walk upward from start_dir looking for a candidate directory
    search_root = start_dir
    for _ in range(5):                                  # max 5 levels up
        for dirname in MODULE_DIR_NAMES:
            candidate = _os.path.join(search_root, dirname)
            if _os.path.isdir(candidate):
                # Verify it contains at least one marker file
                marker_hits = sum(
                    1 for m in MODULE_MARKERS
                    if _os.path.isfile(_os.path.join(candidate, m))
                )
                if marker_hits >= 2:                    # ≥2 markers → confident match
                    abs_candidate = _os.path.abspath(candidate)
                    if abs_candidate not in _sys.path:
                        _sys.path.insert(0, abs_candidate)
                    return abs_candidate
        parent = _os.path.dirname(search_root)
        if parent == search_root:
            break
        search_root = parent

    # 3) Fallback: check if modules are already importable (e.g. same dir)
    return None

_modules_dir = _bootstrap_module_path()

# ── Register *this* main module under the canonical name that pd_routes expects.
#    pd_routes does  `from portdesk_server import (app, cfg, ...)`  — this makes
#    it work whether the file is called SERVER.py, portdesk_server.py, or anything
#    else.  The registration happens *before* pd_routes is imported, so all globals
#    defined up to that point are visible.
#    NOTE: we only set it here as a placeholder; the final update happens right
#    before `

import sys

# Import pd_routes at the end of this file so all globals are set.
# Refresh the module registration so pd_routes can see ALL globals defined
# in this file (not just the ones that existed at bootstrap time).
if SERVER_MODULE_NAME in sys.modules:
    sys.modules[SERVER_MODULE_NAME] = sys.modules.get('__main__', sys.modules[SERVER_MODULE_NAME])

# ── Clean up bootstrap namespace (don't pollute the server module) ──────────
del _bootstrap_module_path, _modules_dir
# Keep MODULE_DIR_NAMES, SERVER_MODULE_NAME, MODULE_MARKERS accessible for
# other modules that might want to call _bootstrap_module_path() themselves.
# If you prefer a truly clean namespace, delete them too and the rest of the
# code will still work because sys.path is already patched.

# ══════════════════════════════════════════════════════════════════════════════
# Original imports — now they work because sys.path includes the modules dir
# ══════════════════════════════════════════════════════════════════════════════
from portdesk_http import (
    Server, Request, Response, JSONResponse, FileResponse, StreamingResponse,
    WebSocket, WebSocketDisconnect, _UploadFile, make_middleware, set_max_body_size
)
from collections import defaultdict

import sys, io, asyncio, json, os, time, ctypes, threading, logging, platform, struct, re
import queue as _queue
import string as _string
import base64, subprocess, hashlib, hmac as _hmac_mod
import concurrent.futures as _futures
import zlib as _zlib
from portdesk_trace import tracer, trace, trace_subprocess_run, trace_subprocess_popen

# ── PIN hashing — extracted to pd_crypto module (refactor) ──────────────────
# Backward-compat aliases keep the rest of this file unchanged.
import pd_crypto as _pd_crypto
_PIN_ROUNDS = _pd_crypto._PIN_ROUNDS
_pin_hash   = _pd_crypto.pin_hash
_pin_verify = _pd_crypto.pin_verify
# bcrypt fully removed — all hashes use PBKDF2-SHA256 (no external dependency)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)

# ── Built-in input control — replaces pyautogui dependency ────────────────────
# Uses native platform APIs (ctypes SendInput on Windows, xdotool on Linux,
# osascript/cliclick on macOS). Zero external dependencies.
import portdesk_input as pyautogui
import socket as _socket

try:
    import numpy as np
    import cv2
    CV2_AVAILABLE = True
    # Let OpenCV use up to half the cores for JPEG encode (capped at 4) so the
    # differential-stream encoder can sustain 30fps on full-screen changes,
    # while leaving headroom for capture + the event loop. (Was hard-pinned to
    # 2, which throttled encoding on multi-core machines.)
    import os as _os_cv
    cv2.setNumThreads(max(2, min(4, (_os_cv.cpu_count() or 2) // 2)))
except ImportError:
    np = None; cv2 = None; CV2_AVAILABLE = False

try:
    import mss as _mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    import dxcam as _dxcam
    DXCAM_AVAILABLE = True
except ImportError:
    _dxcam = None
    DXCAM_AVAILABLE = False

try:
    import uinput
    UINPUT_AVAILABLE = True
except ImportError:
    uinput = None
    UINPUT_AVAILABLE = False

try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
    from aiortc.contrib.media import MediaStreamTrack
    import av
    WEBRTC_AVAILABLE = True
except ImportError:
    WEBRTC_AVAILABLE = False

SUBPROCESS_AVAILABLE = True
# ── Immutable config constants — extracted to pd_config module (refactor) ─────
# Paths + tunables that are written once and read-only afterwards. FLAG_*,
# SECURITY_FILE stay in this file because they mutate at
# runtime (moving them would risk a silent live-update break). pd_config also
# performs the DATA_DIR makedirs+chmod bootstrap on import.
import pd_config as _pd_config
BASE_DIR = _pd_config.BASE_DIR
DATA_DIR = _pd_config.DATA_DIR
SECURITY_FILE = os.path.join(DATA_DIR, "portdesk_security.json")


def _ensure_self_signed_cert(cert_path: str, key_path: str) -> bool:
    """Generate a self-signed cert+key in DATA_DIR on first run if missing, so
    the server can default to HTTPS without the user running a separate tool.
    Returns True if a usable cert/key pair exists afterwards. Requires the
    'cryptography' package; if absent, returns False (caller falls back to HTTP).
    A user who prefers plain HTTP can simply delete key.pem / cert.pem.
    """
    if os.path.isfile(cert_path) and os.path.isfile(key_path):
        return True
    try:
        import socket as _sk, ipaddress as _ip, datetime as _dt
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except Exception:
        return False
    try:
        local_ip = "127.0.0.1"
        try:
            _s = _sk.socket(_sk.AF_INET, _sk.SOCK_DGRAM)
            try: _s.connect(("8.8.8.8", 80)); local_ip = _s.getsockname()[0]
            finally: _s.close()
        except OSError:
            pass
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PortDesk"),
        ])
        _now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
        san = [x509.IPAddress(_ip.ip_address('127.0.0.1')), x509.DNSName('localhost')]
        try:
            _lan = _ip.ip_address(local_ip)
            if not _lan.is_loopback:
                san.insert(0, x509.IPAddress(_lan))
        except ValueError:
            pass
        cert = (x509.CertificateBuilder()
                .subject_name(subject).issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(_now)
                .not_valid_after(_now + _dt.timedelta(days=90))
                .add_extension(x509.SubjectAlternativeName(san), critical=False)
                .sign(key, hashes.SHA256()))
        with open(cert_path, "wb") as fh:
            fh.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as fh:
            fh.write(key.private_bytes(serialization.Encoding.PEM,
                     serialization.PrivateFormat.TraditionalOpenSSL,
                     serialization.NoEncryption()))
        try: os.chmod(key_path, 0o600)
        except OSError: pass
        print(f"  \U0001f510 Generated self-signed certificate (HTTPS) at {cert_path}", flush=True)
        print(f"     To use plain HTTP instead, delete key.pem and cert.pem.", flush=True)
        return True
    except Exception as _e:
        print(f"  \u26a0 Could not auto-generate certificate: {_e} \u2014 falling back to HTTP", flush=True)
        return False


def _check_cert_renewal() -> None:
    """Regenerate cert if <14 days remaining. 90-day validity aligns with modern standards."""
    for path in (os.path.join(DATA_DIR, 'cert.pem'), os.path.join(BASE_DIR, 'cert.pem')):
        if not os.path.isfile(path):
            continue
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            import datetime
            with open(path, 'rb') as f:
                cert_obj = x509.load_pem_x509_certificate(f.read())
            days_left = (cert_obj.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc)).days
            if days_left < 14:
                print(f"  \U0001f504 Certificate expires in {days_left} days — auto-renewing", flush=True)
                key_path = path.replace('cert.pem', 'key.pem')
                _ensure_self_signed_cert(path, key_path)
        except Exception as e:
            _vprint(f"Cert renewal check failed: {e}", flush=True)


def _ensure_fonts_subsets() -> None:
    """Download Tajawal + JetBrains Mono fonts and generate subset .woff2 files.
    Runs once on first startup. Requires fonttools (pyftsubset)."""
    fonts_dir = os.path.join(BASE_DIR, 'extras', 'fonts')
    os.makedirs(fonts_dir, exist_ok=True)

    subset_files = [
        'tajawal-regular-subset.woff2', 'tajawal-bold-subset.woff2',
        'tajawal-extrabold-subset.woff2',
        'jetbrains-mono-subset.woff2', 'jetbrains-mono-bold-subset.woff2'
    ]
    if all(os.path.exists(os.path.join(fonts_dir, f)) for f in subset_files):
        return

    try:
        import urllib.request
        import subprocess as _sp
        import tempfile as _tf

        # Font sources (SIL OFL licensed - redistribution permitted with attribution)
        # Using jsdelivr CDN for reliable Google Fonts access
        font_sources = {
            'tajawal-regular.ttf': 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/tajawal/Tajawal-Regular.ttf',
            'tajawal-bold.ttf': 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/tajawal/Tajawal-Bold.ttf',
            'tajawal-extrabold.ttf': 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/tajawal/Tajawal-ExtraBold.ttf',
            'jetbrains-mono-regular.ttf': 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/jetbrainsmono/JetBrainsMono-Regular.ttf',
            'jetbrains-mono-bold.ttf': 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/jetbrainsmono/JetBrainsMono-Bold.ttf',
        }

        _vprint("  📥 Downloading fonts for subsetting...", flush=True)
        with _tf.TemporaryDirectory() as tmpdir:
            for local_name, url in font_sources.items():
                local_path = os.path.join(tmpdir, local_name)
                try:
                    urllib.request.urlretrieve(url, local_path)
                    _vprint(f"     ✓ {local_name}", flush=True)
                except Exception as e:
                    _vprint(f"     ⚠ Failed to download {local_name}: {e}", flush=True)
                    return

            # Subset definitions: (input_font, output_font, unicode_ranges)
            subsets = [
                ('tajawal-regular.ttf', 'tajawal-regular-subset.woff2',
                 'U+0600-06FF,U+0000-007F,U+00A0-00FF'),
                ('tajawal-bold.ttf', 'tajawal-bold-subset.woff2',
                 'U+0600-06FF,U+0000-007F,U+00A0-00FF'),
                ('tajawal-extrabold.ttf', 'tajawal-extrabold-subset.woff2',
                 'U+0600-06FF,U+0000-007F,U+00A0-00FF'),
                ('jetbrains-mono-regular.ttf', 'jetbrains-mono-subset.woff2',
                 'U+0000-007F,U+00A0-00FF'),
                ('jetbrains-mono-bold.ttf', 'jetbrains-mono-bold-subset.woff2',
                 'U+0000-007F,U+00A0-00FF'),
            ]

            _vprint("  ✂️  Generating font subsets (pyftsubset)...", flush=True)
            for inp, out, ranges in subsets:
                inp_path = os.path.join(tmpdir, inp)
                out_path = os.path.join(fonts_dir, out)
                if not os.path.exists(inp_path):
                    continue
                try:
                    _sp.run([
                        'pyftsubset', inp_path,
                        '--unicodes', ranges,
                        '--flavor=woff2',
                        '--output-file', out_path,
                        '--layout-features=*',
                        '--desubroutinize'
                    ], check=True, capture_output=True, timeout=60)
                    _vprint(f"     ✓ {out}", flush=True)
                except _sp.CalledProcessError as e:
                    _vprint(f"     ⚠ pyftsubset failed for {out}: {e.stderr.decode() if e.stderr else e}", flush=True)
                    return
                except Exception as e:
                    _vprint(f"     ⚠ pyftsubset error for {out}: {e}", flush=True)
                    return

        _vprint("  ✅ Font subsets ready in extras/fonts/", flush=True)
    except ImportError:
        _vprint("  ⚠ fonttools not installed — skipping font subsetting", flush=True)
    except Exception as e:
        _vprint(f"  ⚠ Font subsetting failed: {e}", flush=True)


def _ensure_wf_recorder() -> None:
    """Auto-install wf-recorder on Linux if running as root.
    wf-recorder provides DMA-BUF zero-copy capture (3-5x faster than grim).
    If not root, prints a recommendation hint."""
    import shutil as _sh
    if _sh.which('wf-recorder'):
        return  # already installed

    is_root = os.geteuid() == 0
    if not is_root:
        _vprint("  💡 Recommended: install wf-recorder for better Wayland performance (run as root to auto-install)", flush=True)
        return

    _vprint("  📦 Installing wf-recorder (DMA-BUF capture)...", flush=True)
    try:
        import subprocess as _sp
        # Try apt first (Debian/Ubuntu)
        result = _sp.run(['apt-get', 'update'], capture_output=True, timeout=60)
        if result.returncode == 0:
            result = _sp.run(['apt-get', 'install', '-y', 'wf-recorder'], capture_output=True, timeout=120)
            if result.returncode == 0:
                _vprint("  ✅ wf-recorder installed", flush=True)
                return
        # Try dnf (Fedora)
        result = _sp.run(['dnf', 'install', '-y', 'wf-recorder'], capture_output=True, timeout=120)
        if result.returncode == 0:
            _vprint("  ✅ wf-recorder installed", flush=True)
            return
        # Try pacman (Arch)
        result = _sp.run(['pacman', '-S', '--noconfirm', 'wf-recorder'], capture_output=True, timeout=120)
        if result.returncode == 0:
            _vprint("  ✅ wf-recorder installed", flush=True)
            return
        _vprint("  ⚠ Could not auto-install wf-recorder (unsupported package manager)", flush=True)
    except Exception as e:
        _vprint(f"  ⚠ wf-recorder auto-install failed: {e}", flush=True)


# Dynamic pool manager — replaces static executors
from modules.pd_pool_manager import initialize_pools, get_io_executor, get_input_executor, get_jpeg_encode_executor, get_webrtc_encode_executor

# Initialize on first use (lazy)
_EXECUTOR = None
_INPUT_EXECUTOR = None

def _get_executor():
    global _EXECUTOR
    if _EXECUTOR is None:
        initialize_pools()
        _EXECUTOR = get_io_executor()
    return _EXECUTOR

def _get_input_executor():
    global _INPUT_EXECUTOR
    if _INPUT_EXECUTOR is None:
        initialize_pools()
        _INPUT_EXECUTOR = get_input_executor()
    return _INPUT_EXECUTOR

class _cfg:
    """Single source of truth for all runtime flags.
    Replaces 13 scattered mutable globals — eliminates snapshot/aliasing risk (Pattern 67)."""
    watch_only   = False
    no_explorer  = False
    no_mouse     = False
    no_keyboard  = False
    no_webrtc    = False
    no_h264      = True    # default: JPEG differential. --h264 to opt in.
    grey         = False
    scale        = 1.0
    upload_limit = None
    backend      = None
    verbose      = False
    debug        = False
    no_upload    = False
    no_download  = False

cfg = _cfg()

# Backward-compat module-level scalars — ONLY used by terminal flag toggle
# (setattr/getattr on __main__) and argparse wiring at startup.
# All route/handler code now uses cfg.* directly (single source of truth).
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)
# Aliases removed (M3)

# ── Runtime flag setter — updates cfg (single source of truth) ──
def _set_flag(name, value):
    """Set a runtime flag. Updates the cfg object (single source of truth)."""
    _map = {
        'watch_only':   'watch_only',
        'no_explorer':  'no_explorer',
        'no_mouse':     'no_mouse',
        'no_keyboard':  'no_keyboard',
        'no_webrtc':    'no_webrtc',
        'no_h264':      'no_h264',
        'grey':         'grey',
        'verbose':      'verbose',
        'no_upload':    'no_upload',
        'no_download':  'no_download',
    }
    if name not in _map:
        return False
    attr = _map[name]
    setattr(cfg, attr, value)
    return True

def _vprint(*a, **kw):
    if cfg.verbose:
        import sys as _sys
        _sys.stdout.write('\r' + ' '*60 + '\r')
        print(*a, **kw)
        try: _update_stream_status()
        except NameError: pass
        from portdesk_trace import tracer
        msg = ' '.join(str(x) for x in a)
        tracer.debug(msg)

# portdesk_input has FAILSAFE=False and PAUSE=0 built-in (no configuration needed)

_pyautogui_lock = threading.Lock()
_sec_lock       = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
# LoopRef — thread-safe encapsulation of the running event loop
# Eliminates the _loop global and provides a safe thread→async API.
# Root cause fixed: threads no longer hold a raw loop reference that may be
# stale, closed, or from the wrong asyncio.run() call.
# ══════════════════════════════════════════════════════════════════════════════
class _LoopRef:
    __slots__ = ('_loop',)

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None

    def set(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def ready(self) -> bool:
        return (self._loop is not None
                and not self._loop.is_closed()
                and self._loop.is_running())

    def run_coroutine(self, coro) -> '_futures.Future | None':
        if not self.ready: return None
        try: return asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError: return None

    def call_soon(self, fn, *args) -> None:
        if not self.ready: return
        try: self._loop.call_soon_threadsafe(fn, *args)
        except RuntimeError: pass

_loop_ref = _LoopRef()
# Backward-compat alias — existing code that reads `_loop` still works;
# code that SETS _loop must use _loop_ref.set(loop) instead.
_loop: asyncio.AbstractEventLoop | None = None

# ── Group 3: Token Bucket ─────────────────────────────────────────────────────
class _TokenBucket:
    def __init__(self, rate, capacity):
        self.rate     = rate
        self.capacity = capacity
        self._tokens  = {}
        self._last    = {}
        self._lock    = threading.Lock()

    def consume(self, key, tokens=1):
        now = time.time()
        with self._lock:
            if key not in self._tokens:
                self._tokens[key] = self.capacity
                self._last[key]   = now
            elapsed = now - self._last[key]
            self._tokens[key] = min(self.capacity, self._tokens[key] + elapsed * self.rate)
            self._last[key]   = now
            if self._tokens[key] >= tokens:
                self._tokens[key] -= tokens
                return True
        return False

_ws_buckets = {
    'move':         _TokenBucket(rate=150, capacity=200),
    'scroll':       _TokenBucket(rate=60,  capacity=80),
    'selector_move':_TokenBucket(rate=150, capacity=200),
    'selector_start':_TokenBucket(rate=10, capacity=15),
    'selector_end': _TokenBucket(rate=10,  capacity=15),
    'click':        _TokenBucket(rate=20,  capacity=30),
    'key':          _TokenBucket(rate=30,  capacity=40),
    'key_down':     _TokenBucket(rate=80,  capacity=100),
    'key_up':       _TokenBucket(rate=80,  capacity=100),
    'type':         _TokenBucket(rate=20,  capacity=30),
    'shortcut':     _TokenBucket(rate=15,  capacity=20),
    'stream_config':_TokenBucket(rate=2,   capacity=5),
    'set_monitor':  _TokenBucket(rate=2,   capacity=5),
    'screen_start': _TokenBucket(rate=0.5, capacity=2),
    'screen_stop':  _TokenBucket(rate=2,   capacity=4),
    'mic_start':    _TokenBucket(rate=0.5, capacity=2),
    'mic_stop':     _TokenBucket(rate=2,   capacity=4),
    'mic_chunk':    _TokenBucket(rate=100, capacity=150),
    'audio_start':  _TokenBucket(rate=0.5, capacity=2),
    'audio_stop':   _TokenBucket(rate=2,   capacity=4),
    'default':      _TokenBucket(rate=30,  capacity=50),
}

# STUN removed — LAN-only WebRTC (no NAT traversal)

_webrtc_pcs: set = set()

_webrtc_dc_clients: list = []
_webrtc_dc_lock           = threading.Lock()


class _DataChannelClient:
    _is_dc_client = True

    def __init__(self, channel, host: str):
        self.channel = channel
        self.client  = type('_C', (), {'host': host})()

    async def send_json(self, data: dict):
        try:
            if self.channel.readyState == 'open':
                self.channel.send(json.dumps(data))
        except Exception:
            pass

    async def send_bytes(self, data: bytes):
        try:
            if self.channel.readyState == 'open':
                self.channel.send(data)
        except Exception:
            pass


class ConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in list(self.active):          # iterate a copy — active may mutate during await
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_bytes(self, data: bytes):
        dead = []
        for ws in list(self.active):          # iterate a copy — active may mutate during await
            if getattr(ws, '_is_dc_client', False):
                continue
            try:
                await ws.send_bytes(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_sync(self, data: dict):
        if _loop and not _loop.is_closed() and _loop.is_running():
            _loop_ref.run_coroutine(self.broadcast(data))

    def broadcast_bytes_sync(self, data: bytes):
        if _loop and not _loop.is_closed() and _loop.is_running():
            _loop_ref.run_coroutine(self.broadcast_bytes(data))

    async def broadcast_ws_only(self, data: dict):
        dead = []
        for ws in list(self.active):          # iterate a copy — active may mutate during await
            if getattr(ws, '_is_dc_client', False):
                continue
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_ws_only_sync(self, data: dict):
        if _loop and not _loop.is_closed() and _loop.is_running():
            _loop_ref.run_coroutine(self.broadcast_ws_only(data))

manager = ConnectionManager()

_SEC_BACKUP_COUNT = _pd_config._SEC_BACKUP_COUNT

def _security_backup_path(n):
    return f"{SECURITY_FILE}.bak{n}"

def _load_security_from(path):
    with open(path) as f:
        d = json.load(f)
    if "blacklist" not in d: d["blacklist"] = []
    if "lockout"   not in d: d["lockout"]   = {}
    if "pins"      not in d: d["pins"]       = {}
    d.pop("pin_hash", None)
    return d

def _load_security():
    for src in [SECURITY_FILE] + [_security_backup_path(n) for n in range(1, _SEC_BACKUP_COUNT + 1)]:
        try:
            d = _load_security_from(src)
            if src != SECURITY_FILE:
                print(f"  ⚠ Security file recovered from {os.path.basename(src)}", flush=True)
            return d
        except Exception:
            continue
    return {"whitelist": [], "blacklist": [], "pins": {}, "lockout": {}}

def _save_security():
    for n in range(_SEC_BACKUP_COUNT, 1, -1):
        src = _security_backup_path(n - 1)
        dst = _security_backup_path(n)
        try:
            if os.path.exists(src): os.replace(src, dst)
        except Exception: pass
    try:
        if os.path.exists(SECURITY_FILE):
            os.replace(SECURITY_FILE, _security_backup_path(1))
    except Exception: pass
    tmp = SECURITY_FILE + '.tmp'
    with open(tmp, "w") as f:
        json.dump(security, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SECURITY_FILE)
    try: os.chmod(SECURITY_FILE, 0o600)
    except Exception: pass

def _restore_security_backup(n=1):
    path = _security_backup_path(n)
    if not os.path.exists(path):
        return False, f"backup .bak{n} not found"
    try:
        d = _load_security_from(path)
        with _sec_lock:
            security.clear()
            security.update(d)
            _save_security()
        _log_event('security_restore', detail=f"restored from .bak{n}", severity='WARNING')
        return True, path
    except Exception as e:
        return False, str(e)

security = _load_security()
if "blacklist" not in security: security["blacklist"] = []
if "pins"      not in security: security["pins"]      = {}
if "lockout"   not in security: security["lockout"]   = {}

# ── HMAC Challenge-Response secret for WebSocket ──────────────────────────
if "hmac_secret" not in security or not security["hmac_secret"]:
    import secrets as _secrets
    security["hmac_secret"] = _secrets.token_hex(32)
    _save_security()

_HMAC_SECRET = security["hmac_secret"].encode()

# ══════════════════════════════════════════════════════════════════════════════
# Security CORE — extracted to pd_security module (refactor).
# Only the self-contained primitives are moved (nonce guard, rate limiting,
# attack detection, HMAC verify, allow/blacklist checks). The cross-cutting
# orchestration (_trigger_lockdown / _approve_ip / _prompt_add_ip /
# _require_active_pin) and persistence (_load/_save/_restore_security) stay here.
# configure() shares the SAME `security` dict + `_sec_lock` references so writes
# from the watcher/approve/save paths and reads in the module see one object.
# Backward-compat aliases keep the rest of this file unchanged.
# ══════════════════════════════════════════════════════════════════════════════
import pd_security as _pd_security
_pd_security.configure(security, _sec_lock)

_hmac_verify            = _pd_security._hmac_verify
_is_rate_limited        = _pd_security._is_rate_limited
_is_blacklisted         = _pd_security._is_blacklisted
_is_allowed             = _pd_security._is_allowed
_check_and_consume_nonce = _pd_security._check_and_consume_nonce
_req_counts             = _pd_security._req_counts          # same dict object
_used_pin_nonces        = _pd_security._used_pin_nonces      # same dict object
_used_pin_nonces_lock   = _pd_security._used_pin_nonces_lock # same lock object

_reject_counts = defaultdict(int)   # stays: owned by approve/prompt orchestration

# ── Restore persisted lockout state ──────────────────────────────────────────
_pin_fails         = defaultdict(int)
_pin_lockout       = {}
_pin_lockout_count = defaultdict(int)
PIN_MAX_TRIES      = _pd_config.PIN_MAX_TRIES
PIN_LOCKOUT_STEPS  = _pd_config.PIN_LOCKOUT_STEPS

_now = time.time()
for _ip, _ld in security.get("lockout", {}).items():
    if isinstance(_ld, dict) and _ld.get("until", 0) > _now:
        _pin_lockout[_ip]       = _ld["until"]
        _pin_lockout_count[_ip] = _ld.get("count", 1)

import ipaddress as _ipaddress

def _is_private_host(host: str) -> bool:
    h = host.split(':')[0].lower()
    if h in ('localhost', '127.0.0.1', '::1'): return True
    try:
        ip = _ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False

def _require_active_pin(request):
    """Pattern 27 guard: ensure the requesting IP is the active client AND has
    passed PIN verification. Returns a JSONResponse to short-circuit on
    failure, or None when the caller may proceed. Local loopback is exempt."""
    ip = request.client.host
    if ip in ('127.0.0.1', '::1', 'localhost'):
        return None
    if not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    with _active_client_lock:
        _aip = _active_client_ip
    if _aip is not None and _aip != ip:
        return JSONResponse({'error': 'session occupied'}, status_code=423)
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)
    return None

_pending_ips = []
_pending_ips_lock = threading.Lock()

_lockdown         = False
_lockdown_lock    = threading.Lock()

# ══════════════════════════════════════════════════════════════════════════════
# SessionManager + StreamStateManager — extracted to pd_state module (refactor).
# Behaviour is byte-for-byte identical; only the file location changed. The
# backward-compat aliases below keep the rest of this file unchanged.
# ══════════════════════════════════════════════════════════════════════════════
import pd_state as _pd_state
_SessionManager     = _pd_state._SessionManager
_StreamStateManager = _pd_state._StreamStateManager

_session = _SessionManager()

# Backward-compat aliases — reads still work; WRITES go through _session methods.
_active_client_ws   = None           # read-alias → _session.ws
_active_client_ip   = None           # read-alias → _session.ip
_active_client_lock = _session._lock # same lock object
_ws_pin_verified    = _session._verified  # same dict object
_ws_pin_lock        = _session._lock      # same lock object

# ── Multi-IP attack detection — moved to pd_security (refactor) ───────────────
ATTACK_THRESHOLD       = _pd_security.ATTACK_THRESHOLD
ATTACK_WINDOW          = _pd_security.ATTACK_WINDOW
_unknown_attempts      = _pd_security._unknown_attempts       # same list object
_unknown_attempts_lock = _pd_security._unknown_attempts_lock  # same lock object
_record_unknown_attempt = _pd_security._record_unknown_attempt

def _trigger_lockdown(reason=''):
    global _lockdown
    with _lockdown_lock:
        if _lockdown: return
        _lockdown = True
    _stream.stop_all()
    _log_event('lockdown', detail=reason, severity='CRITICAL')
    print(f"\n{'█'*52}", flush=True)
    print(f"  🚨 LOCKDOWN ACTIVATED — {reason}", flush=True)
    print(f"  All clients kicked. New connections blocked.", flush=True)
    print(f"  Type  lockdown off  to resume.", flush=True)
    print(f"{'█'*52}\n", flush=True)
    manager.broadcast_sync({'type': 'lockdown', 'reason': reason})
    if _loop and not _loop.is_closed() and _loop.is_running():
        _loop_ref.run_coroutine(_kick_all_clients())

async def _kick_all_clients():
    for ws in list(manager.active):
        try: await ws.close(1008)
        except: pass
    manager.active.clear()

def _approve_ip(ip, action='allow'):
    with _pending_ips_lock:
        if ip in _pending_ips:
            _pending_ips.remove(ip)
    if action == 'allow':
        with _sec_lock:
            if ip not in security["whitelist"]:
                security["whitelist"].append(ip)
            _reject_counts[ip] = 0
            security.get("pins", {}).pop(ip, None)
            security.get("lockout", {}).pop(ip, None)
            _pin_lockout.pop(ip, None)
            _pin_fails[ip] = 0
            _pin_lockout_count[ip] = 0
            _save_security()
        print(f"  ✅ Approved {ip}", flush=True)
        manager.broadcast_sync({'type': 'ip_approved', 'ip': ip})
    else:
        _reject_counts[ip] += 1
        if _reject_counts[ip] >= 3:
            with _sec_lock:
                if ip not in security["blacklist"]:
                    security["blacklist"].append(ip)
                    _save_security()
            print(f"  ⛔ {ip} blacklisted after 3 rejections", flush=True)
        else:
            print(f"  ✗ Rejected {ip} — {3 - _reject_counts[ip]} attempt(s) remaining", flush=True)

def _stdin_reader():
    print("  ⌨  Terminal ready — type  help  to see all commands", flush=True)
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
        except (EOFError, OSError):
            break
        cmd = line.lower()
        if cmd == 'y':
            with _pending_ips_lock:
                if not _pending_ips:
                    print("  ❓ No pending requests", flush=True); continue
                ip = _pending_ips[-1]
            _approve_ip(ip, 'allow')
        elif cmd == 'n':
            with _pending_ips_lock:
                if not _pending_ips:
                    print("  ❓ No pending requests", flush=True); continue
                ip = _pending_ips[-1]
            _approve_ip(ip, 'deny')
        elif cmd.startswith('unblock '):
            ip = line[8:].strip()
            if not ip:
                print("  ❓ Usage: unblock <ip>", flush=True); continue
            with _sec_lock:
                changed = False
                if ip in security.get('blacklist', []):
                    security['blacklist'].remove(ip)
                    changed = True
                if ip in _reject_counts:
                    _reject_counts[ip] = 0
                if changed:
                    _save_security()
                    print(f"  ✅ {ip} removed from blacklist", flush=True)
                else:
                    print(f"  ❓ {ip} not in blacklist", flush=True)
        elif cmd == 'kick all':
            _log_event('kick_all', ip='system')
            if _loop and not _loop.is_closed() and _loop.is_running():
                _loop_ref.run_coroutine(_kick_all_clients())
            print("  ✅ All clients kicked", flush=True)
        elif cmd == 'lockdown off':
            global _lockdown
            with _lockdown_lock:
                _lockdown = False
            _log_event('lockdown_off', ip='system')
            print("  ✅ Lockdown lifted", flush=True)
        elif cmd.startswith('restore security'):
            parts = cmd.split()
            n = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
            ok, info = _restore_security_backup(n)
            if ok:
                print(f"  ✅ Security restored from .bak{n}: {info}", flush=True)
            else:
                print(f"  ❌ Restore failed: {info}", flush=True)
        elif cmd.startswith('flag ') or cmd.startswith('--'):
            # Runtime flag toggle from terminal.
            # Two supported syntaxes:
            #   flag <name> <on|off>     →  flag verbose on
            #   --<name>                 →  --verbose          (sets ON, mirrors startup arg)
            #   --<name> <on|off>        →  --verbose off  /  --no-mouse off
            import __main__ as _m
            _BOOL_FLAGS = {
                'verbose':      'cfg.verbose',
                'watch-only':   'cfg.watch_only',
                'watch_only':   'cfg.watch_only',
                'no-mouse':     'cfg.no_mouse',
                'no_mouse':     'cfg.no_mouse',
                'no-keyboard':  'cfg.no_keyboard',
                'no_keyboard':  'cfg.no_keyboard',
                'no-explorer':  'cfg.no_explorer',
                'no_explorer':  'cfg.no_explorer',
                'no-upload':    'cfg.no_upload',
                'no_upload':    'cfg.no_upload',
                'no-download':  'cfg.no_download',
                'no_download':  'cfg.no_download',
                'grey':         'cfg.grey',
            }
            parts = cmd.split()  # already lowercased

            # Normalise both syntaxes into (fname, fval)
            if parts[0].startswith('--'):
                # --verbose  /  --verbose off  /  --no-mouse on
                fname = parts[0].lstrip('-')
                if len(parts) >= 2 and parts[1] in ('on', 'off', '1', '0', 'true', 'false'):
                    fval = parts[1]
                else:
                    fval = 'on'   # bare --verbose → turn ON
            else:
                # flag verbose on
                if len(parts) < 3:
                    print("  ❓ Usage:  flag <name> <on|off>", flush=True)
                    print("  ❓ Or:     --<name>  /  --<name> <on|off>", flush=True)
                    print("  ❓ Names: verbose, watch-only, no-mouse, no-keyboard,", flush=True)
                    print("            no-explorer, no-upload, no-download, grey", flush=True)
                    continue  # back to top of while loop
                fname = parts[1]
                fval  = parts[2]

            if fname not in _BOOL_FLAGS:
                print(f"  ❓ Unknown flag '{fname}'", flush=True)
                print( "  ❓ Names: verbose, watch-only, no-mouse, no-keyboard,", flush=True)
                print( "            no-explorer, no-upload, no-download, grey", flush=True)
            elif fval not in ('on', 'off', '1', '0', 'true', 'false'):
                print("  ❓ Value must be  on  or  off", flush=True)
            else:
                bool_val = fval in ('on', '1', 'true')
                var_name = _BOOL_FLAGS[fname]
                # Extract the actual flag name from 'cfg.flag_name' format
                flag_name = var_name.replace('cfg.', '')
                _set_flag(flag_name, bool_val)
                print(f"  ✅ {var_name} = {bool_val}", flush=True)
                _log_event('flags_update', detail=f'terminal: {var_name}={bool_val}')
        elif cmd == 'flags':
            # Show current runtime flag values
            print("\n  ┌─ Runtime Flags ────────────────────────────────────────┐", flush=True)
            for _fn, _attr in [
                ('verbose',     'verbose'),
                ('watch-only',  'watch_only'),
                ('no-mouse',    'no_mouse'),
                ('no-keyboard', 'no_keyboard'),
                ('no-explorer', 'no_explorer'),
                ('no-upload',   'no_upload'),
                ('no-download', 'no_download'),
                ('grey',        'grey'),
                ('scale',       'scale'),
                ('backend',     'backend'),
            ]:
                _val = getattr(cfg, _attr, '?')
                _status = '✅ on' if _val is True else ('⛔ off' if _val is False else str(_val))
                print(f"  │  {_fn:<14}  {_status}", flush=True)
            print("  └────────────────────────────────────────────────────────┘\n", flush=True)
        elif cmd == 'help':
            print(
                "\n  ┌──────────────────────────────────────────────────────────┐\n"
                "  │  PortDesk Terminal Commands                              │\n"
                "  ├──────────────────────────────────────────────────────────┤\n"
                "  │  y                       Approve last pending IP         │\n"
                "  │  n                       Reject last pending IP          │\n"
                "  │  unblock <ip>            Remove IP from blacklist        │\n"
                "  │  kick all                Disconnect all active clients   │\n"
                "  │  lockdown off            Lift active lockdown            │\n"
                "  │  restore security [1-3]  Restore security backup file    │\n"
                "  │  flags                   Show all runtime flag values    │\n"
                "  │  flag <name> <on|off>    Toggle a runtime flag           │\n"
                "  │  --<name>                Same — set flag ON (startup     │\n"
                "  │  --<name> <on|off>         arg style also works)         │\n"
                "  │    names: verbose, watch-only, no-mouse, no-keyboard,    │\n"
                "  │           no-explorer, no-upload, no-download, grey      │\n"
                "  │  help                    Show this message               │\n"
                "  └──────────────────────────────────────────────────────────┘\n",
                flush=True
            )
        else:
            print("  ❓ Unknown command — type  help  to see all commands", flush=True)

_security_file_mtime = 0

def _security_file_watcher():
    global _security_file_mtime, security, _HMAC_SECRET
    _cleanup_counter = 0
    while True:
        time.sleep(2)
        _cleanup_counter += 1
        try:
            mtime = os.path.getmtime(SECURITY_FILE)
            if mtime != _security_file_mtime:
                _security_file_mtime = mtime
                new_sec = _load_security()
                with _sec_lock:
                    security.clear()
                    security.update(new_sec)
                # Reload HMAC secret if changed
                _new_hmac = new_sec.get('hmac_secret', '')
                if _new_hmac:
                    _HMAC_SECRET = _new_hmac.encode()
                now = time.time()
                for ip, ld in new_sec.get("lockout", {}).items():
                    if isinstance(ld, dict) and ld.get("until", 0) > now:
                        _pin_lockout[ip]       = ld["until"]
                        _pin_lockout_count[ip] = ld.get("count", 1)
                    else:
                        _pin_lockout.pop(ip, None)
                _vprint("🔄 Security file reloaded", flush=True)
        except Exception:
            pass
        if _cleanup_counter >= 30:
            _cleanup_counter = 0
            _pd_security.cleanup_req_counts()

def _prompt_add_ip(ip):
    if _record_unknown_attempt(ip):
        _trigger_lockdown(f'Multi-IP attack detected ({ATTACK_THRESHOLD}+ unknown IPs in {ATTACK_WINDOW}s)')
        return
    with _pending_ips_lock:
        if ip in _pending_ips: return
        _pending_ips.append(ip)
    count = _reject_counts[ip] + 1
    print(f"\n{'═'*50}\n  🔔 New connection request from: {ip}  (attempt {count}/3)", flush=True)
    print(f"  Type  y  to approve,  n  to reject", flush=True)
    print('═'*50, flush=True)
    manager.broadcast_sync({'type': 'ip_request', 'ip': ip, 'attempt': count})

class SecurityMiddleware:
    """Security middleware — no longer inherits BaseHTTPMiddleware.
    Uses plain dispatch() method compatible with custom server middleware chain."""
    OPEN_PATHS = {'/security/whitelist/request', '/security/whitelist/remove_self'}
    SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS'}
    CSRF_PATHS = {
        '/explorer/delete', '/explorer/upload', '/explorer/rename',
        '/explorer/mkdir',  '/explorer/mkfile', '/explorer/copy',
        '/explorer/move',   '/explorer/shortcut',
        '/tasks/kill',      '/macros/run',       '/macros/delete',
        '/macros/save',
        '/log/clear',       '/auth/set_pin',     '/auth/clear_pin',
        '/scheduled/delete','/scheduled/save',
        '/flags/update',    '/screen/start',     '/screen/stop',
    }
    # H10: Only trust X-Forwarded-For from known reverse proxies
    TRUSTED_PROXIES = {'127.0.0.1', '::1'}

    async def dispatch(self, request: Request, call_next):
        ip = request.client.host
        # H10: Only accept X-Forwarded-For from trusted proxies
        xff = request.headers.get('x-forwarded-for', '')
        if xff and ip in self.TRUSTED_PROXIES:
            ip = xff.split(',')[0].strip()
        origin = request.headers.get('origin', '')
        host   = request.headers.get('host', '')

        def _err(msg, code):
            r = JSONResponse({"error": msg}, status_code=code)
            if origin:
                r.headers['Access-Control-Allow-Origin']      = origin
                r.headers['Access-Control-Allow-Credentials'] = 'true'
                r.headers['Vary']                             = 'Origin'
            return r

        with _lockdown_lock:
            if _lockdown and ip not in ('127.0.0.1', '::1', 'localhost'):
                return _err("server in lockdown", 503)

        if _is_blacklisted(ip):
            return _err("blacklisted", 403)

        is_local = ip in ('127.0.0.1', '::1', 'localhost')

        # Rate-limit ONLY unknown (non-whitelisted) IPs.
        # Whitelisted clients fire 6+ requests on every page load — throttling them
        # blocks the WS connection before it even opens ("connection lost" bug).
        # Unknown clients get limited here to prevent flooding whitelist/auth endpoints
        # even though they have no PIN yet.
        if not is_local and not _is_allowed(ip):
            if _is_rate_limited(ip):
                return _err("rate limited", 429)

        if request.url.path in self.OPEN_PATHS:
            # Even open paths must respect blacklist and origin checks
            if _is_blacklisted(ip):
                return _err("blacklisted", 403)
            # Require valid origin for open-path POST requests to prevent CSRF
            if request.method not in self.SAFE_METHODS:
                if origin and host and origin not in (f'http://{host}', f'https://{host}'):
                    return _err("invalid origin", 403)
            response = await call_next(request)
            response.headers['server'] = 'portdesk'
            return response

        if not is_local and not _is_allowed(ip):
            _prompt_add_ip(ip)
            return _err("not whitelisted", 403)

        if not is_local:
            with _active_client_lock:
                _aip = _active_client_ip
            if _aip is not None and _aip != ip:
                return _err("session occupied", 423)

        if request.method not in self.SAFE_METHODS and request.url.path in self.CSRF_PATHS:
            allowed_origins = (
                f'http://{host}', f'https://{host}',
                'http://localhost:5000', 'https://localhost:5000',
            )
            if not origin or origin not in allowed_origins:
                return _err("invalid origin", 403)

        response = await call_next(request)
        response.headers['server'] = 'portdesk'
        return response


app = Server()

# ── Lifespan: start background threads (replaces FastAPI lifespan) ───────────
def _start_lifespan():
    global _clip_running, _sched_running, _stats_running
    _clip_running  = True
    _sched_running = True
    _stats_running = True

    import sys as _sys
    _pv = _sys.version_info
    if (_pv.major, _pv.minor) not in ((3, 11), (3, 12)):
        print(f"\n  ⚠️  Python {_pv.major}.{_pv.minor} detected — recommended: 3.11 or 3.12 for best stability.", flush=True)

    from portdesk_trace import tracer
    tracer.enabled = cfg.verbose

    _check_cert_renewal()

    _ensure_fonts_subsets()

    if platform.system() == 'Linux':
        import shutil as _sh
        _sc = _sh.which('sysctl')
        if _sc:
            for _kv in ['net.ipv4.tcp_syncookies=1',
                        'net.ipv4.icmp_echo_ignore_broadcasts=1',
                        'net.ipv4.conf.all.rp_filter=1']:
                try: trace_subprocess_run([_sc, '-w', _kv], capture_output=True, timeout=3)
                except Exception: pass
        _ensure_wf_recorder()
    threading.Thread(target=_clipboard_watcher, daemon=True).start()
    threading.Thread(target=_scheduler_worker, daemon=True).start()
    threading.Thread(target=_stats_pusher, daemon=True).start()
    threading.Thread(target=_stdin_reader, daemon=True).start()
    threading.Thread(target=_security_file_watcher, daemon=True).start()
    threading.Thread(target=_log_writer_thread, daemon=True).start()
    for warn in _check_linux_compatibility():
        print(f"⚠️ Linux: {warn}")
    if platform.system() != 'Windows':
        if _init_virtual_keyboard():
            print("✅ Virtual keyboard initialized.")
        else:
            print("⚠️ Virtual keyboard not available; using fallbacks.")
    threading.Thread(target=_detect_ffmpeg_encoder, daemon=True).start()
    if WEBRTC_AVAILABLE:
        async def _webrtc_pc_cleanup():
            while True:
                await asyncio.sleep(30)
                dead = {pc for pc in list(_webrtc_pcs)
                        if pc.connectionState in ('failed', 'closed', 'disconnected')}
                for pc in dead:
                    try: await pc.close()
                    except Exception: pass
                    _webrtc_pcs.discard(pc)
        _loop_ref.run_coroutine(_webrtc_pc_cleanup())
    _flag_detail = (
        f"watch_only={cfg.watch_only} no_explorer={cfg.no_explorer} "
        f"no_mouse={cfg.no_mouse} no_keyboard={cfg.no_keyboard} "
        f"no_webrtc={cfg.no_webrtc} grey={cfg.grey} "
        f"scale={cfg.scale} backend={cfg.backend or 'auto'} "
        f"upload_limit={cfg.upload_limit or 'unlimited'} "
        f"no_upload={cfg.no_upload} no_download={cfg.no_download}"
    )
    _log_event('startup', detail=_flag_detail)

def _stop_lifespan():
    global _stats_running, _clip_running, _sched_running, _dxcam_camera
    _stats_running = False
    _clip_running  = False
    _sched_running = False
    with _dxcam_camera_lock:
        if _dxcam_camera is not None:
            try: _dxcam_camera.stop()
            except: pass
            _dxcam_camera = None

# ── Exception handler ────────────────────────────────────────────────────────
async def _global_exception_handler(request, exc):
    import traceback as _tb
    print(f"\u26a0\ufe0f  Unhandled exception on {request.url.path}: {exc!r}", flush=True)
    if cfg.verbose:
        _tb.print_exc()
    return JSONResponse({'error': 'internal server error'}, status_code=500)

app._exception_handler = _global_exception_handler

# ── Security middleware (replaces BaseHTTPMiddleware) ────────────────────────
app.add_middleware(make_middleware(SecurityMiddleware))

# ── Security headers middleware ─────────────────────────────────────────────
class _SecurityHeadersMiddleware:
    _CSP = (
        "default-src 'self'; "
        # 'unsafe-inline' is required because the client uses inline event
        # handlers and inline <script>. DOMPurify is bundled locally at /extras/purify.min.js.
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' wss: ws:; "
        "media-src 'self' blob:; "
        "frame-ancestors 'none';"
    )
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers['X-Frame-Options']          = 'DENY'
        response.headers['X-Content-Type-Options']   = 'nosniff'
        response.headers['Referrer-Policy']          = 'no-referrer'
        response.headers['Content-Security-Policy']  = self._CSP
        if request.url.scheme == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response

app.add_middleware(make_middleware(_SecurityHeadersMiddleware))

# ── Dynamic CORS middleware ──────────────────────────────────────────────────
class _DynamicCORSMiddleware:
    @staticmethod
    def _is_safe_origin(origin: str, host: str) -> bool:
        if origin not in (f'http://{host}', f'https://{host}'):
            return False
        # Strip port from host for IP validation
        bare = host.split(':')[0]
        try:
            import ipaddress
            addr = ipaddress.ip_address(bare)
            return addr.is_private or addr.is_loopback or addr.is_link_local
        except ValueError:
            # hostname (not an IP) — only allow localhost
            return bare.lower() == 'localhost'

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        origin = request.headers.get('origin', '')
        host   = request.headers.get('host', '')
        if origin and host and self._is_safe_origin(origin, host):
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Access-Control-Allow-Methods'] = 'GET,POST,OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            response.headers['Vary'] = 'Origin'
        return response

app.add_middleware(make_middleware(_DynamicCORSMiddleware))

# ── CORS preflight handler ──────────────────────────────────────────────────
async def cors_preflight(path, request: Request):
    origin = request.headers.get('origin', '')
    host   = request.headers.get('host', '')
    if origin and host and _DynamicCORSMiddleware._is_safe_origin(origin, host):
        return JSONResponse(
            status_code=204,
            headers={
                'Access-Control-Allow-Origin': origin,
                'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Max-Age': '86400',
                'Vary': 'Origin',
            }
        )
    return JSONResponse(status_code=403)

app._options_handler = cors_preflight

# ── System stats — extracted to pd_stats module (refactor) ──────────────────
import pd_stats as _pd_stats
_CoreTempData    = _pd_stats._CoreTempData
_get_coretemp    = _pd_stats._get_coretemp
get_system_stats = _pd_stats.get_system_stats

KEY_MAP = {
    'win':'winleft','windows':'winleft','super':'winleft',
    'cmd':'command','command':'command',
    'ctrl':'ctrl','control':'ctrl','alt':'alt','shift':'shift',
    'printscreen':'printscreen','prtsc':'printscreen',
    'playpause':'playpause','nexttrack':'nexttrack','prevtrack':'prevtrack',
    'volumemute':'volumemute','volumeup':'volumeup','volumedown':'volumedown',
    'esc':'escape','escape':'escape','del':'delete','ins':'insert',
    'backspace':'backspace','enter':'enter','return':'enter',
    'tab':'tab','space':'space',
    'up':'up','down':'down','left':'left','right':'right',
    **{f'f{i}': f'f{i}' for i in range(1, 13)},
}
def map_key(k: str) -> str: return KEY_MAP.get(k.lower(), k.lower())

# ── Clipboard — extracted to pd_clipboard module (refactor) ─────────────────
import pd_clipboard as _pd_clipboard
_clipboard_copy  = _pd_clipboard._clipboard_copy
_clipboard_paste = _pd_clipboard._clipboard_paste

def _is_simple_typable(text):
    """True if text is short ASCII printable — safe & fast to send via direct
    keystrokes (pyautogui.write) instead of the slow clipboard-paste path."""
    if len(text) > 40:
        return False
    try:
        text.encode('ascii')
    except UnicodeEncodeError:
        return False
    return all(32 <= ord(c) <= 126 for c in text)

def type_text(text):
    if not text: return
    with _pyautogui_lock:
        # ── Fast path: plain ASCII (typical Latin typing). Direct keystrokes are
        # far faster than the clipboard dance, which on Windows spawns powershell
        # Get-Clipboard (~200-400ms) PER call — the cause of laggy typing.
        if _is_simple_typable(text):
            try:
                pyautogui.write(text, interval=0)
                return
            except Exception as e:
                _vprint(f"type_text write fast-path: {e}", flush=True)
                # fall through to clipboard path
        # ── Unicode / long text path: use clipboard + paste (preserves Arabic,
        # emoji, etc.). Back up & restore the previous clipboard once.
        _old_clip = None
        try:
            try: _old_clip = _clipboard_paste()
            except Exception: _old_clip = None
            _clipboard_copy(text)
            time.sleep(0.02)
            if platform.system() == 'Darwin': pyautogui.hotkey('command', 'v')
            else:                              pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.02)
        except Exception:
            try: pyautogui.write(text, interval=0.02)
            except Exception as e: _vprint(f"❌ type_text: {e}", flush=True)
        finally:
            if _old_clip is not None:
                try:
                    time.sleep(0.03)
                    _clipboard_copy(_old_clip)
                except Exception: pass

_virtual_kb_device = None

def _init_virtual_keyboard():
    global _virtual_kb_device
    if not UINPUT_AVAILABLE or platform.system() != 'Linux': return False
    try:
        events = [
            uinput.KEY_A, uinput.KEY_B, uinput.KEY_C, uinput.KEY_D, uinput.KEY_E,
            uinput.KEY_F, uinput.KEY_G, uinput.KEY_H, uinput.KEY_I, uinput.KEY_J,
            uinput.KEY_K, uinput.KEY_L, uinput.KEY_M, uinput.KEY_N, uinput.KEY_O,
            uinput.KEY_P, uinput.KEY_Q, uinput.KEY_R, uinput.KEY_S, uinput.KEY_T,
            uinput.KEY_U, uinput.KEY_V, uinput.KEY_W, uinput.KEY_X, uinput.KEY_Y,
            uinput.KEY_Z, uinput.KEY_0, uinput.KEY_1, uinput.KEY_2, uinput.KEY_3,
            uinput.KEY_4, uinput.KEY_5, uinput.KEY_6, uinput.KEY_7, uinput.KEY_8,
            uinput.KEY_9, uinput.KEY_SPACE, uinput.KEY_ENTER, uinput.KEY_BACKSPACE,
            uinput.KEY_TAB, uinput.KEY_ESC, uinput.KEY_LEFTSHIFT, uinput.KEY_LEFTCTRL,
            uinput.KEY_LEFTALT, uinput.KEY_LEFTMETA, uinput.KEY_F1, uinput.KEY_F2,
            uinput.KEY_F3, uinput.KEY_F4, uinput.KEY_F5, uinput.KEY_F6, uinput.KEY_F7,
            uinput.KEY_F8, uinput.KEY_F9, uinput.KEY_F10, uinput.KEY_F11, uinput.KEY_F12,
            uinput.KEY_LEFT, uinput.KEY_RIGHT, uinput.KEY_UP, uinput.KEY_DOWN,
            uinput.KEY_DELETE, uinput.KEY_HOME, uinput.KEY_END, uinput.KEY_PAGEUP,
            uinput.KEY_PAGEDOWN, uinput.KEY_CAPSLOCK, uinput.KEY_NUMLOCK,
        ]
        _virtual_kb_device = uinput.Device(events)
        return True
    except Exception as e:
        _vprint(f"Virtual keyboard init failed: {e}", flush=True)
        return False

def _send_virtual_key(key_code, press=True):
    if _virtual_kb_device:
        try:
            _virtual_kb_device.emit(key_code, 1 if press else 0)
        except Exception as e:
            _vprint(f"Virtual key send failed: {e}", flush=True)

def _send_virtual_text(text):
    for char in text:
        if char.isalpha():
            key = getattr(uinput, f'KEY_{char.upper()}', None)
            if key: _send_virtual_key(key, True); time.sleep(0.01); _send_virtual_key(key, False)
        elif char.isdigit():
            key = getattr(uinput, f'KEY_{char}', None)
            if key: _send_virtual_key(key, True); time.sleep(0.01); _send_virtual_key(key, False)
        elif char == ' ':  _send_virtual_key(uinput.KEY_SPACE, True);     time.sleep(0.01); _send_virtual_key(uinput.KEY_SPACE, False)
        elif char == '\n': _send_virtual_key(uinput.KEY_ENTER, True);     time.sleep(0.01); _send_virtual_key(uinput.KEY_ENTER, False)
        elif char == '\t': _send_virtual_key(uinput.KEY_TAB, True);       time.sleep(0.01); _send_virtual_key(uinput.KEY_TAB, False)
        elif char == '\b': _send_virtual_key(uinput.KEY_BACKSPACE, True); time.sleep(0.01); _send_virtual_key(uinput.KEY_BACKSPACE, False)
        time.sleep(0.005)

def _send_xdotool_key(key):
    if SUBPROCESS_AVAILABLE and platform.system() == 'Linux':
        try: trace_subprocess_run(['xdotool', 'key', key], check=True)
        except Exception as e: print(f"xdotool key failed: {e}", flush=True)

def _send_xdotool_text(text):
    if SUBPROCESS_AVAILABLE and platform.system() == 'Linux':
        try: trace_subprocess_run(['xdotool', 'type', '--clearmodifiers', text], check=True)
        except Exception as e: print(f"xdotool text failed: {e}", flush=True)

# ══ Capture Backend Chain — extracted to pd_capture module (refactor) ════════
# Deps (numpy/cv2/dxcam/mss + runtime cfg.backend + _vprint) injected via init().
import pd_capture as _pd_capture
_pd_capture.init(
    numpy_mod=np if CV2_AVAILABLE else None,
    cv2_mod=cv2 if CV2_AVAILABLE else None,
    dxcam_mod=_dxcam if DXCAM_AVAILABLE else None,
    mss_mod=_mss if MSS_AVAILABLE else None,
    dxcam_available=DXCAM_AVAILABLE,
    cv2_available=CV2_AVAILABLE,
    mss_available=MSS_AVAILABLE,
    vprint=_vprint,
    get_flag_backend=lambda: cfg.backend,
)
# Backward-compat aliases (rest of this file references these names unchanged)
_CaptureBase             = _pd_capture._CaptureBase
_MSSCaptureBackend       = _pd_capture._MSSCaptureBackend
_DXCamCaptureBackend     = _pd_capture._DXCamCaptureBackend
_BitBltCaptureBackend    = _pd_capture._BitBltCaptureBackend
_FFmpegX11CaptureBackend = _pd_capture._FFmpegX11CaptureBackend
_XlibCaptureBackend      = _pd_capture._XlibCaptureBackend
_ScrotCaptureBackend     = _pd_capture._ScrotCaptureBackend
_GrimCaptureBackend      = _pd_capture._GrimCaptureBackend
_QuartzCaptureBackend    = _pd_capture._QuartzCaptureBackend
_ScreencaptureCLIBackend = _pd_capture._ScreencaptureCLIBackend
_build_capture_chain     = _pd_capture._build_capture_chain
_create_capture_backend  = _pd_capture._create_capture_backend

# ═════════════════════════════════════════════════════════════════════════════
# StreamStateManager — extracted to pd_state module (refactor). Imported above
# with _SessionManager. Instance kept local so all backward-compat aliases work.
# ══════════════════════════════════════════════════════════════════════════════
_stream = _StreamStateManager()

def _update_stream_status():
    """Print/update a live stream status line in the terminal."""
    transport = _stream.transport or 'WS'
    mode = _stream.mode or 'idle'
    status = f'{transport} · {mode}'
    # Use carriage return to overwrite the same line
    sys.stdout.write(f'\r  📡 Stream: {status}          \r')
    sys.stdout.flush()

_dxcam_camera      = None
_dxcam_camera_lock = threading.Lock()

stream_config = {
    'height': 720, 'quality': 65, 'fps': 30,
    'monitor': 1, 'cursor_color_bgr': (255, 255, 255),
    'scale': 1.0, 'grey': False, 'codec': 'auto'
}
_stream_config_lock = threading.Lock()

_ffmpeg_encoder    = None
_ffmpeg_encoder_ok = False

def _detect_ffmpeg_encoder():
    global _ffmpeg_encoder, _ffmpeg_encoder_ok
    import shutil
    if not shutil.which('ffmpeg'):
        _vprint("⚠ FFmpeg not found in PATH — hardware encoding unavailable", flush=True)
        return None
    if not CV2_AVAILABLE:
        return None

    try:
        res = trace_subprocess_run(['ffmpeg', '-encoders', '-hide_banner'],
                                   capture_output=True, text=True, timeout=5)
        enc_list = res.stdout + res.stderr
    except Exception:
        enc_list = ''

    sys_name = platform.system()
    if sys_name == 'Windows':
        candidates = [
            ('h264_nvenc', ['-preset', 'p1', '-tune', 'll', '-bf', '0']),
            ('h264_amf',   ['-quality', 'speed', '-bf', '0']),
            ('h264_qsv',   ['-preset', 'veryfast', '-bf', '0']),
            ('libx264',    ['-preset', 'ultrafast', '-tune', 'zerolatency', '-bf', '0']),
        ]
    elif sys_name == 'Linux':
        candidates = [
            ('h264_nvenc', ['-preset', 'p1', '-tune', 'll', '-bf', '0']),
            ('h264_vaapi', []),
            ('libx264',    ['-preset', 'ultrafast', '-tune', 'zerolatency', '-bf', '0']),
        ]
    elif sys_name == 'Darwin':
        candidates = [
            ('h264_videotoolbox', ['-realtime', '1', '-bf', '0']),
            ('libx264',           ['-preset', 'ultrafast', '-tune', 'zerolatency', '-bf', '0']),
        ]
    else:
        candidates = [('libx264', ['-preset', 'ultrafast', '-tune', 'zerolatency', '-bf', '0'])]

    dummy_frame = np.zeros((64, 64, 3), dtype=np.uint8).tobytes()

    for enc, enc_flags in candidates:
        if enc != 'libx264' and enc not in enc_list:
            _vprint(f"  ↳ {enc}: not listed in ffmpeg encoders, skip", flush=True)
            continue
        try:
            if enc == 'h264_vaapi':
                cmd = [
                    'ffmpeg', '-y', '-loglevel', 'error',
                    '-vaapi_device', '/dev/dri/renderD128',
                    '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', '64x64', '-r', '1',
                    '-i', 'pipe:0',
                    '-vf', 'format=nv12,hwupload',
                    '-vcodec', 'h264_vaapi',
                    '-frames:v', '1', '-f', 'null', '-'
                ]
            else:
                cmd = [
                    'ffmpeg', '-y', '-loglevel', 'error',
                    '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', '64x64', '-r', '1',
                    '-i', 'pipe:0',
                    '-vcodec', enc,
                ] + enc_flags + ['-frames:v', '1', '-f', 'null', '-']

            proc = trace_subprocess_popen(cmd, stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            _, _ = proc.communicate(input=dummy_frame, timeout=5)
            if proc.returncode == 0:
                _ffmpeg_encoder    = enc
                _ffmpeg_encoder_ok = True
                hw = enc != 'libx264'
                _vprint(f"✅ FFmpeg encoder: {enc} ({'hardware' if hw else 'software fallback'})", flush=True)
                return enc
            else:
                _vprint(f"  ↳ {enc}: returned error", flush=True)
        except FileNotFoundError:
            _vprint("⚠ FFmpeg not found", flush=True); return None
        except subprocess.TimeoutExpired:
            try: proc.kill()
            except: pass
            _vprint(f"  ↳ {enc}: timeout", flush=True)
        except Exception as e:
            _vprint(f"  ↳ {enc}: {e}", flush=True); continue

    _vprint("⚠ FFmpeg: no encoder detected", flush=True)
    return None


class _FfmpegH264Streamer:
    MSG_H264 = 0x03

    def __init__(self, encoder, width, height, fps):
        self.encoder  = encoder
        self.width    = width
        self.height   = height
        self.fps      = max(1, fps)
        self.proc     = None
        self._running = False
        self._reader  = None

    def _build_cmd(self):
        enc = self.encoder
        fps = self.fps
        gop = fps * 2

        base = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-f', 'rawvideo', '-pix_fmt', 'bgr24',
            '-s', f'{self.width}x{self.height}',
            '-r', str(fps),
            '-i', 'pipe:0',
        ]

        if enc == 'h264_nvenc':
            enc_args = ['-vcodec', 'h264_nvenc', '-preset', 'p1',
                        '-tune', 'll', '-zerolatency', '1', '-bf', '0', '-g', str(gop)]
        elif enc == 'h264_amf':
            enc_args = ['-vcodec', 'h264_amf', '-quality', 'speed',
                        '-rc', 'cbr', '-bf', '0', '-g', str(gop)]
        elif enc == 'h264_qsv':
            enc_args = ['-vcodec', 'h264_qsv', '-preset', 'veryfast',
                        '-bf', '0', '-g', str(gop)]
        elif enc == 'h264_videotoolbox':
            enc_args = ['-vcodec', 'h264_videotoolbox', '-realtime', '1',
                        '-bf', '0', '-g', str(gop)]
        elif enc == 'h264_vaapi':
            base = [
                'ffmpeg', '-y', '-loglevel', 'error',
                '-vaapi_device', '/dev/dri/renderD128',
                '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                '-s', f'{self.width}x{self.height}',
                '-r', str(fps), '-i', 'pipe:0',
            ]
            enc_args = ['-vf', 'format=nv12,hwupload',
                        '-vcodec', 'h264_vaapi', '-bf', '0', '-g', str(gop)]
        else:
            enc_args = ['-vcodec', 'libx264', '-preset', 'ultrafast',
                        '-tune', 'zerolatency', '-bf', '0', '-g', str(gop)]

        out = ['-f', 'mp4',
               '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
               'pipe:1']
        return base + enc_args + out

    def start(self):
        self._running = True
        try:
            self.proc = trace_subprocess_popen(
                self._build_cmd(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0
            )
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
            return True
        except Exception as e:
            _vprint(f"❌ H264Streamer start: {e}", flush=True)
            self._running = False
            return False

    def _read_loop(self):
        CHUNK = 16384
        while self._running:
            try:
                data = self.proc.stdout.read(CHUNK)
                if not data:
                    break
                msg = struct.pack('>BI', self.MSG_H264, len(data)) + data
                _loop_ref.run_coroutine(manager.broadcast_bytes(msg))
            except Exception as e:
                if self._running:
                    _vprint(f"❌ H264Streamer read: {e}", flush=True)
                break

    def send_frame(self, frame_bgr):
        if self.proc is None or self.proc.poll() is not None:
            return False
        try:
            self.proc.stdin.write(frame_bgr.tobytes())
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def close(self):
        self._running = False
        if self.proc:
            try: self.proc.stdin.close()
            except: pass
            try: self.proc.terminate()
            except: pass
            self.proc = None

_mouse_pos      = (0, 0)
_mouse_pos_lock = threading.Lock()
_scroll_accum   = 0.0   # fractional scroll accumulator (smooth continuous scroll)

def _mouse_tracker():
    global _mouse_pos
    while True:
        try:
            p = pyautogui.position()
            with _mouse_pos_lock: _mouse_pos = (p.x, p.y)
        except Exception: pass
        time.sleep(0.016)

threading.Thread(target=_mouse_tracker, daemon=True).start()

def _draw_cursor(arr, mx, my, mon_left, mon_top, src_w, src_h, cursor_color):
    nw, nh = arr.shape[1], arr.shape[0]
    sx = int((mx - mon_left) * nw / src_w)
    sy = int((my - mon_top)  * nh / src_h)
    if 0 <= sx < nw and 0 <= sy < nh:
        pts = np.array([[sx, sy], [sx+12, sy+12], [sx, sy+16]], np.int32)
        cv2.fillPoly(arr, [pts], cursor_color)
        cv2.polylines(arr, [pts], True, (0, 0, 0), 1)

@trace
def screen_worker():
    _stream.screen_error = ''

    if not CV2_AVAILABLE:
        _stream.screen_error = 'cv2 not available'
        return

    with _stream_config_lock: cfg0 = stream_config.copy()
    codec = cfg0.get('codec', 'auto')
    use_h264 = (codec in ('h264', 'auto')) and _ffmpeg_encoder_ok and _ffmpeg_encoder is not None

    if use_h264:
        target_h = cfg0['height']
        fps      = max(1, cfg0['fps'])
        mon_idx0 = max(0, cfg0.get('monitor', 1) - 1)
        _probe_cap = _create_capture_backend(mon_idx0)
        if _probe_cap:
            _, _pm = _probe_cap.grab(mon_idx0)
            src_w0, src_h0 = _pm.get('width', 1920), _pm.get('height', 1080)
            _probe_cap.close()
        else:
            src_w0, src_h0 = 1920, 1080
        target_w = int(src_w0 * target_h / src_h0)
        target_w = target_w if target_w % 2 == 0 else target_w + 1
        target_h = target_h if target_h % 2 == 0 else target_h + 1

        h264 = _FfmpegH264Streamer(_ffmpeg_encoder, target_w, target_h, fps)
        if not h264.start():
            use_h264 = False
            _stream.mode = 'JPEG (H264 failed — fallback)'
            _update_stream_status()
            _vprint("⚠ H264 streamer failed to start — falling back to JPEG", flush=True)
        else:
            _stream.mode = f'H264 via {_ffmpeg_encoder} ({target_w}x{target_h} @ {fps}fps)'
            _update_stream_status()
            _vprint(f"✅ screen: H264 via {_ffmpeg_encoder} ({target_w}x{target_h} @ {fps}fps)", flush=True)
            _loop_ref.run_coroutine(
                manager.broadcast({'type': 'stream_mode', 'mode': 'h264',
                                   'encoder': _ffmpeg_encoder,
                                   'width': target_w, 'height': target_h})
            )

    if not use_h264:
        tj = None; use_turbo = False
        try:
            from turbojpeg import TurboJPEG, TJPF_BGR, TJSAMP_444 as _TJSAMP_444
            tj, use_turbo = TurboJPEG(), True
            _TJPF_BGR = TJPF_BGR
            _stream.mode = 'JPEG (TurboJPEG)'
            _update_stream_status()
            _vprint("✅ screen: TurboJPEG active", flush=True)
        except Exception as _te:
            _stream.mode = 'JPEG (cv2 fallback)'
            _update_stream_status()
            _vprint(f"⚠ screen: TurboJPEG not available ({_te}), falling back to cv2", flush=True)
        _loop_ref.run_coroutine(manager.broadcast({'type': 'stream_mode', 'mode': 'jpeg'}))

    _pipe      = asyncio.Queue(maxsize=1)
    _SENTINEL  = object()
    fps_frames = 0
    fps_t      = time.perf_counter()
    # ── Adaptive frame pacing state ──────────────────────────────────────────
    _pace = {'enc_ema': 0.0,      # avg seconds to encode+send one frame
             'eff_fps': float(max(1, stream_config.get('fps', 30)))}  # current sustainable fps
    _pace_lock = threading.Lock()

    def _msg_full(fw, fh, jpeg):
        return struct.pack('>BHH', 0x01, fw, fh) + jpeg

    def _msg_patch(fw, fh, px, py, pw, ph, jpeg):
        return struct.pack('>BHHHHHHI', 0x02, fw, fh, px, py, pw, ph, len(jpeg)) + jpeg

    BLOCK       = 64
    DIFF_THR    = 12
    FORCE_EVERY = 90
    PATCH_LIMIT = 0.45
    _prev_arr   = None
    _frame_ctr  = 0

    def _encode_jpeg(a, q):
        if use_turbo:
            return bytes(tj.encode(a, quality=q, jpeg_subsample=_TJSAMP_444, pixel_format=_TJPF_BGR))
        _, enc = cv2.imencode('.jpg', a, [cv2.IMWRITE_JPEG_QUALITY, q])
        return enc.tobytes()

    DIFF_DS = 4  # downscale factor for change detection (cheap, ~16x less work)

    def _dirty_bbox(arr, prev, block):
        H, W  = arr.shape[:2]
        # Detect the changed region on a downscaled thumbnail — the heavy
        # absdiff/gray/reshape then runs on 1/16 the pixels. The resulting bbox
        # is mapped back to full-res and snapped outward to BLOCK boundaries, so
        # the encoded patch is visually identical to the full-res detection.
        ds  = DIFF_DS
        sH, sW = max(1, H // ds), max(1, W // ds)
        a_s = cv2.resize(arr,  (sW, sH), interpolation=cv2.INTER_AREA)
        p_s = cv2.resize(prev, (sW, sH), interpolation=cv2.INTER_AREA)
        diff = cv2.absdiff(a_s, p_s)
        gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY) if diff.ndim == 3 else diff
        if int(gray.max()) <= DIFF_THR:
            return None
        ys, xs = np.where(gray > DIFF_THR)
        # map thumbnail coords back to full-res, snap to BLOCK grid
        y0 = max(0, (int(ys.min()) * ds) // block * block)
        x0 = max(0, (int(xs.min()) * ds) // block * block)
        y1 = min(H - 1, (((int(ys.max()) + 1) * ds + block - 1) // block) * block - 1)
        x1 = min(W - 1, (((int(xs.max()) + 1) * ds + block - 1) // block) * block - 1)
        return x0, y0, x1, y1

    @trace
    async def _encode_emit():
        nonlocal fps_frames, fps_t, _prev_arr, _frame_ctr
        loop = asyncio.get_running_loop()
        sent_frames = 0   # frames actually encoded+sent this second
        seen_frames = 0   # frames pulled from capture this second (target rate)
        while _stream.screen_streaming:
            try:
                item = await asyncio.wait_for(_pipe.get(), timeout=0.1)
            except asyncio.TimeoutError:
                item = None
            now = time.perf_counter()
            if item is not None and item is not _SENTINEL:
                _enc_t0 = time.perf_counter()
                arr, cfg_snap = item
                seen_frames += 1
                q  = cfg_snap.get('quality', 65)
                H, W = arr.shape[:2]
                _frame_ctr += 1
                force = (_frame_ctr % FORCE_EVERY == 1) or (_prev_arr is None) or (_prev_arr.shape != arr.shape)

                if force:
                    jpeg = await loop.run_in_executor(_EXECUTOR, _encode_jpeg, arr, q)
                    if jpeg:
                        await manager.broadcast_bytes(_msg_full(W, H, jpeg))
                    _prev_arr = arr; sent_frames += 1; fps_frames += 1
                else:
                    bbox = await loop.run_in_executor(_EXECUTOR, _dirty_bbox, arr, _prev_arr, BLOCK)
                    if bbox is None:
                        # No visual change — skip encode/send entirely (CPU saver).
                        _prev_arr = arr
                    else:
                        x0, y0, x1, y1 = bbox
                        pw_p, ph_p = x1 - x0 + 1, y1 - y0 + 1
                        if (pw_p * ph_p) / (W * H) > PATCH_LIMIT:
                            jpeg = await loop.run_in_executor(_EXECUTOR, _encode_jpeg, arr, q)
                            if jpeg:
                                await manager.broadcast_bytes(_msg_full(W, H, jpeg))
                        else:
                            patch = arr[y0:y1+1, x0:x1+1]
                            jpeg  = await loop.run_in_executor(_EXECUTOR, _encode_jpeg, patch, q)
                            if jpeg:
                                await manager.broadcast_bytes(_msg_patch(W, H, x0, y0, pw_p, ph_p, jpeg))
                        _prev_arr = arr; sent_frames += 1; fps_frames += 1
            elif item is _SENTINEL:
                break

            # Once per second: report fps + an honest state so the client can show
            # a status indicator instead of a scary "1 fps" while the screen is
            # simply static. target = the fps the user selected.
            if now - fps_t >= 1.0:
                with _stream_config_lock: _tgt = max(1, stream_config.get('fps', 30))
                real = round(sent_frames / (now - fps_t), 1)
                if sent_frames == 0:
                    state = 'idle'           # nothing changed on screen — stream is healthy
                elif real >= _tgt * 0.6 or seen_frames >= _tgt * 0.8:
                    state = 'active'         # keeping up with the target
                elif sent_frames <= 2:
                    state = 'active'         # only a tiny change happened — not a slowdown
                else:
                    state = 'slow'           # many changes but can't keep up — real warning
                await manager.broadcast({'type': 'fps_update', 'fps': real,
                                         'state': state, 'target': _tgt})
                fps_frames = 0; fps_t = now; sent_frames = 0; seen_frames = 0

            # Update adaptive pacing: how long did this iteration's encode cost?
            if item is not None and item is not _SENTINEL:
                _enc_dt = time.perf_counter() - _enc_t0
                with _pace_lock:
                    # Slow EMA so one spike doesn't whipsaw the rate.
                    _pace['enc_ema'] = (_enc_dt if _pace['enc_ema'] == 0.0
                                        else _pace['enc_ema'] * 0.9 + _enc_dt * 0.1)
                    with _stream_config_lock: _tgt2 = max(1, stream_config.get('fps', 30))
                    # Sustainable fps from measured cost, with ~15% headroom for
                    # capture+resize in the other thread. Never exceed the user's
                    # chosen fps; never below 5.
                    if _pace['enc_ema'] > 0:
                        sustainable = 0.85 / _pace['enc_ema']
                    else:
                        sustainable = _tgt2
                    target_eff = max(5.0, min(float(_tgt2), sustainable))
                    # Gentle glide toward target, but clamp the per-update change
                    # so eff_fps never lurches (prevents visible dips).
                    _delta = target_eff - _pace['eff_fps']
                    _delta = max(-2.0, min(2.0, _delta))   # at most ±2 fps/update
                    _pace['eff_fps'] += _delta * 0.5

    emit_future = _loop_ref.run_coroutine(_encode_emit())

    def _put_frame(arr, cfg):
        if not _pipe.empty():
            try: _pipe.get_nowait()
            except: pass
        try: _pipe.put_nowait((arr, cfg))
        except: pass

    # ── Unified capture backend loop (now uses capture process) ───────────────
    from modules.pd_pool_manager import send_capture_config, get_capture_frame, stop_capture
    import numpy as np
    
    with _stream_config_lock: cfg_local = stream_config.copy()
    
    # Send initial config to capture process
    send_capture_config({
        'height': cfg_local['height'],
        'fps': cfg_local['fps'],
        'monitor': cfg_local.get('monitor', 1),
        'scale': cfg_local.get('scale', 1.0),
        'grey': cfg_local.get('grey', False),
        'cursor_color_bgr': cfg_local.get('cursor_color_bgr', (255, 255, 255)),
        'codec': cfg_local.get('codec', 'auto'),
    })
    
    _stream.mode = 'Capture Process + ' + (_stream.mode.split(' + ')[-1] if _stream.mode else 'JPEG')
    _update_stream_status()
    _vprint("✅ screen: capture process active", flush=True)

    _consec_err     = 0
    _prev_raw_small = None
    _prev_mouse     = (-1, -1)
    _RAW_DS         = 8

    try:
        while _stream.screen_streaming:
            try:
                t0 = time.perf_counter()
                with _stream_config_lock: cfg_local = stream_config.copy()
                fps          = max(1, cfg_local['fps'])
                with _pace_lock: _eff = _pace['eff_fps']
                frame_budget = 1.0 / max(1.0, min(float(fps), _eff))
                target_h     = cfg_local['height']

                # Get frame from capture process
                metadata = get_capture_frame(timeout=0.1)
                if metadata is None:
                    elapsed = time.perf_counter() - t0
                    if frame_budget - elapsed > 0.001: time.sleep(frame_budget - elapsed)
                    continue

                # Attach to shared memory and get frame data
                from multiprocessing import shared_memory
                shm = shared_memory.SharedMemory(name=metadata.shm_name)
                arr = np.frombuffer(shm.buf[:metadata.shm_size], dtype=np.uint8).reshape((metadata.height, metadata.width, 3))
                shm.close()

                # Idle check on the frame we received
                with _mouse_pos_lock: mx, my = _mouse_pos
                try:
                    _rh, _rw = arr.shape[:2]
                    _small = cv2.resize(arr, (max(1, _rw // _RAW_DS), max(1, _rh // _RAW_DS)),
                                        interpolation=cv2.INTER_NEAREST)
                except Exception:
                    _small = None
                _mouse_moved = (mx, my) != _prev_mouse
                if (not use_h264) and _small is not None and _prev_raw_small is not None \
                        and not _mouse_moved \
                        and _small.shape == _prev_raw_small.shape \
                        and int(cv2.absdiff(_small, _prev_raw_small).max()) <= 8:
                    elapsed = time.perf_counter() - t0
                    if frame_budget - elapsed > 0.001: time.sleep(frame_budget - elapsed)
                    continue
                _prev_raw_small = _small
                _prev_mouse     = (mx, my)
                _consec_err = 0

                # Frame already resized/processed by capture process, just draw cursor
                src_h, src_w = arr.shape[:2]
                _draw_cursor(arr, mx, my,
                             0, 0,  # capture process already handles monitor offset
                             src_w, src_h,
                             cfg_local.get('cursor_color_bgr', (255, 255, 255)))

                if use_h264:
                    if not h264.send_frame(arr):
                        _vprint("⚠ H264 send_frame failed", flush=True); break
                    fps_frames += 1
                    _now_fps = time.perf_counter()
                    if _now_fps - fps_t >= 1.0:
                        _loop_ref.run_coroutine(manager.broadcast({'type': 'fps_update', 'fps': round(fps_frames / (_now_fps - fps_t), 1)}))
                        fps_frames = 0; fps_t = _now_fps
                else:
                    _loop_ref.call_soon(_put_frame, arr, cfg_local)

                elapsed = time.perf_counter() - t0
                if frame_budget - elapsed > 0.001: time.sleep(frame_budget - elapsed)

            except Exception as e:
                _stream.screen_error = str(e)
                _consec_err += 1
                _backoff = min(2.0, 0.1 * (2 ** min(_consec_err - 1, 5)))
                if _consec_err <= 3 or _consec_err % 20 == 0:
                    _vprint(f"\u274c frame error #{_consec_err}: {e} (backoff {_backoff:.1f}s)", flush=True)
                if _consec_err >= 50:
                    _stream.screen_error = f"capture failed {_consec_err}x — stopping: {e}"
                    print(f"\u26a0\ufe0f  Screen capture failed {_consec_err} times consecutively — stopping stream.", flush=True)
                    _stream.screen_streaming = False
                    break
                time.sleep(_backoff)
    finally:
        stop_capture()
        _prev_arr = None
    try: _loop_ref.call_soon(_pipe.put_nowait, _SENTINEL)
    except: pass
    try: emit_future.result(timeout=2)
    except: pass
    if use_h264:
        try: h264.close()
        except: pass
    if _stream.screen_error:
        manager.broadcast_sync({'type': 'screen_error', 'msg': _stream.screen_error})

if WEBRTC_AVAILABLE:
    from fractions import Fraction as _Fraction

    class ScreenCaptureTrack(MediaStreamTrack):
        kind = "video"

        def __init__(self):
            super().__init__()
            self._pts       = 0
            self._time_base = _Fraction(1, 90000)
            self._fps       = 30
            self._last_arr  = None

        def stop(self):
            super().stop()

        async def recv(self):
            loop = asyncio.get_running_loop()
            with _stream_config_lock:
                self._fps = max(1, stream_config.get('fps', 30))
            
            # Get frame from capture process
            from modules.pd_pool_manager import get_capture_frame
            metadata = get_capture_frame(timeout=0.1)
            
            if metadata is not None:
                from multiprocessing import shared_memory
                import numpy as np
                shm = shared_memory.SharedMemory(name=metadata.shm_name)
                frame_arr = np.frombuffer(shm.buf[:metadata.shm_size], dtype=np.uint8).reshape((metadata.height, metadata.width, 3))
                shm.close()
                self._last_arr = frame_arr
            elif self._last_arr is not None:
                frame_arr = self._last_arr
            else:
                frame_arr = None

            if frame_arr is not None:
                frame = av.VideoFrame.from_ndarray(frame_arr, format='bgr24')
            else:
                frame = av.VideoFrame(width=640, height=480, format='rgb24')

            frame.pts       = self._pts
            frame.time_base = self._time_base
            self._pts      += int(90000 / self._fps)
            await asyncio.sleep(1.0 / self._fps)
            return frame

_mic_queue  = _queue.Queue(maxsize=40)

def _mic_worker():
    stream = None
    try:
        import sounddevice as sd
        device_idx = None
        for i, dev in enumerate(sd.query_devices()):
            name = dev['name'].lower()
            if 'cable' in name and dev['max_output_channels'] > 0:
                device_idx = i; break
        if device_idx is None:
            # Fallback: use the system default output device.
            try:
                _def = sd.default.device
                device_idx = _def[1] if isinstance(_def, (list, tuple)) else _def
            except Exception:
                device_idx = None
            if device_idx is None or (isinstance(device_idx, int) and device_idx < 0):
                _vprint("❌ mic: no output device found", flush=True)
                _stream.mic_active = False
                return
            _vprint("ℹ️  mic: VB-Audio cable not found — using default output device", flush=True)
        stream = sd.RawOutputStream(samplerate=44100, channels=1, dtype='int16',
                                    blocksize=2048, latency='low', device=device_idx)
        stream.start()
        while _stream.mic_active:
            try:
                pcm = _mic_queue.get(timeout=0.5)
                if pcm is None: break
                stream.write(pcm)
            except _queue.Empty: continue
            except Exception as e: _vprint(f"mic_worker write: {e}", flush=True)
    except Exception as e: _vprint(f"mic_worker: {e}", flush=True)
    finally:
        if stream is not None:
            try: stream.stop()
            except: pass
            try: stream.close()
            except: pass

_AUDIO_CHUNK    = 1024   # ~46ms @22050Hz (was 4096 = 185ms latency)
_AUDIO_RATE     = 22050

def _audio_worker():
    try:
        import sounddevice as sd
        device_idx = None
        for i, dev in enumerate(sd.query_devices()):
            name = dev['name'].lower()
            if 'cable' in name and dev['max_input_channels'] > 0:
                device_idx = i; break
        if device_idx is None:
            # Fallback: use the system default input device.
            try:
                _def = sd.default.device
                device_idx = _def[0] if isinstance(_def, (list, tuple)) else _def
            except Exception:
                device_idx = None
            if device_idx is None or (isinstance(device_idx, int) and device_idx < 0):
                _vprint("❌ audio: no input device found", flush=True)
                _stream.audio_streaming = False
                return
            _vprint("ℹ️  audio: VB-Audio cable not found — using default input device", flush=True)
        with sd.InputStream(samplerate=_AUDIO_RATE, channels=1, dtype='int16',
                            blocksize=_AUDIO_CHUNK, device=device_idx) as stream:
            while _stream.audio_streaming:
                data, _ = stream.read(_AUDIO_CHUNK)
                # Binary audio frame: 0x04 + raw PCM16 (no base64 → 33% less
                # bandwidth, no encode CPU). Falls back gracefully — client
                # handles both binary 0x04 and legacy JSON audio_chunk.
                manager.broadcast_bytes_sync(b'\x04' + data.tobytes())
    except Exception as e:
        _vprint(f"❌ audio_worker: {e}", flush=True); _stream.audio_streaming = False

_last_clip    = ""
_clip_lock    = threading.Lock()
_clip_running = False

def _clipboard_watcher():
    global _last_clip, _clip_running
    # Uses built-in _clipboard_paste — no pyperclip dependency needed
    while _clip_running:
        try:
            current = _clipboard_paste()
            with _clip_lock:
                if current and current != _last_clip:
                    _last_clip = current
                    manager.broadcast_ws_only_sync({'type': 'clipboard_update', 'text': current})
        except: pass
        time.sleep(2)

_stats_running = False

def _stats_pusher():
    while _stats_running:
        time.sleep(5)
        try:
            stats = get_system_stats()
            manager.broadcast_sync({'type': 'stats_push', **stats})
        except: pass

LOG_FILE  = _pd_config.LOG_FILE
_log_lock = threading.Lock()
_last_log_hash: str | None = None

_SEVERITY_MAP = {
    'connect':       'INFO',
    'disconnect':    'INFO',
    'pin_success':   'INFO',
    'pin_fail':      'WARNING',
    'pin_set':       'INFO',
    'pin_cleared':   'INFO',
    'task_kill':     'WARNING',
    'mic_start':     'INFO',
    'mic_stop':      'INFO',
    'audio_start':   'INFO',
    'audio_stop':    'INFO',
    'sched_run':     'INFO',
    'ip_rejected':   'WARNING',
    'ip_blacklisted':'WARNING',
    'lockdown':      'CRITICAL',
    'lockdown_off':  'INFO',
    'intrusion':     'CRITICAL',
    'kick_all':      'WARNING',
    'startup':        'INFO',
    'security_restore':'WARNING',
}

def _sanitize_log(s: str) -> str:
    """Strip newlines and control characters from log fields to prevent log injection."""
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(s)).strip()

# Background single-writer log queue — keeps file I/O off the event loop
# (Pattern 5). _log_event() only does the fast in-memory hash-chain under
# the lock, then hands the serialized line to a daemon writer thread.
_log_write_queue: '_queue.Queue' = _queue.Queue(maxsize=10000)

_MAX_LOG_SIZE = _pd_config._MAX_LOG_SIZE   # 10 MB before rotating to .1

def _rotate_log_if_needed():
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > _MAX_LOG_SIZE:
            bak = LOG_FILE + '.1'
            try:
                if os.path.exists(bak): os.remove(bak)
            except OSError: pass
            os.replace(LOG_FILE, bak)
    except Exception as e:
        print(f"\u26a0\ufe0f Log rotation error: {e}", flush=True)

def _log_writer_thread():
    while True:
        item = _log_write_queue.get()
        if item is None:
            break
        try:
            if item == '__CLEAR__':
                with _log_lock:
                    with open(LOG_FILE, 'w', encoding='utf-8'):
                        pass
                continue
            _rotate_log_if_needed()
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(item + '\n')
        except Exception as e:
            print(f"\u26a0\ufe0f Log writer error: {e}", flush=True)

def _log_event(event_type, detail='', ip='system', severity=None):
    global _last_log_hash
    with _log_lock:
        if _last_log_hash is None:
            _last_log_hash = '0' * 64
            try:
                if os.path.exists(LOG_FILE):
                    with open(LOG_FILE, 'rb') as _f:
                        _f.seek(0, 2)
                        _fsize = _f.tell()
                        _f.seek(max(0, _fsize - 4096))
                        _tail = _f.read()
                    for _line in reversed(_tail.splitlines()):
                        if _line.strip():
                            try:
                                _last_log_hash = json.loads(_line).get('hash', '0' * 64)
                            except Exception:
                                pass
                            break
            except Exception:
                pass
        last_hash = _last_log_hash

        sev = severity or _SEVERITY_MAP.get(event_type, 'INFO')
        entry = {
            't':        time.strftime('%Y-%m-%d %H:%M:%S'),
            'type':     _sanitize_log(event_type),
            'severity': _sanitize_log(sev),
            'ip':       _sanitize_log(ip),
            'detail':   _sanitize_log(detail),
            'prev':     last_hash,
        }
        chain_str     = json.dumps(entry, sort_keys=True, separators=(',', ':'))
        entry['hash'] = hashlib.sha256(chain_str.encode()).hexdigest()
        _last_log_hash = entry['hash']
        line = json.dumps(entry)

    # File write is offloaded — never blocks the caller / event loop.
    # Fallback: if queue is full, write directly to disk (blocking but ensures durability).
    try:
        _log_write_queue.put_nowait(line)
    except _queue.Full:
        try:
            _rotate_log_if_needed()
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass

def _press_win_shortcut(keys):
    system = platform.system()
    try:
        if system == 'Windows':
            VK = {
                'winleft':0x5B,'winright':0x5C,
                'a':0x41,'b':0x42,'c':0x43,'d':0x44,'e':0x45,'f':0x46,'g':0x47,
                'h':0x48,'i':0x49,'j':0x4A,'k':0x4B,'l':0x4C,'m':0x4D,'n':0x4E,
                'o':0x4F,'p':0x50,'q':0x51,'r':0x52,'s':0x53,'t':0x54,'u':0x55,
                'v':0x56,'w':0x57,'x':0x58,'y':0x59,'z':0x5A,
                'tab':0x09,'space':0x20,'enter':0x0D,'escape':0x1B,
                'f1':0x70,'f2':0x71,'f3':0x72,'f4':0x73,'f5':0x74,
                'ctrl':0x11,'alt':0x12,'shift':0x10,
            }
            u32 = ctypes.windll.user32
            vks = [VK.get(k) for k in keys if VK.get(k)]
            if not vks: return False
            for vk in vks: u32.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
            for vk in reversed(vks): u32.keybd_event(vk, 0, 0x0002, 0)
            return True
        elif system == 'Darwin':
            mac_keys = ['command' if k in ('winleft','winright','command','cmd') else k for k in keys]
            with _pyautogui_lock: pyautogui.hotkey(*mac_keys)
            return True
        else:
            with _pyautogui_lock: pyautogui.hotkey(*keys)
            return True
    except Exception as e:
        _vprint(f"win shortcut error: {e}", flush=True); return False

SCHED_FILE    = _pd_config.SCHED_FILE
_sched_lock   = threading.Lock()
_sched_running = False

def _load_scheduled():
    try:
        with open(SCHED_FILE) as f: return json.load(f)
    except: return []

def _save_scheduled(tasks):
    # Atomic write: tmp + os.replace so a crash mid-write can't corrupt the file.
    tmp = SCHED_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(tasks, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, SCHED_FILE)

scheduled_tasks = _load_scheduled()

MACROS_FILE = _pd_config.MACROS_FILE
_macro_lock = threading.Lock()

def _load_macros():
    try:
        with open(MACROS_FILE) as f: return json.load(f)
    except: return {}

def _save_macros(m):
    # Atomic write: tmp + os.replace (prevents corruption on crash mid-write).
    tmp = MACROS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(m, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, MACROS_FILE)

macros = _load_macros()

# Per-task locks to prevent overlapping runs
_sched_task_locks = {}  # task_id -> threading.Event

def _scheduler_worker():
    global _sched_running
    while _sched_running:
        now = time.strftime('%H:%M')
        with _sched_lock:
            for task in scheduled_tasks:
                if not task.get('enabled', True): continue
                if task.get('time') == now and task.get('last_run') != now:
                    task_id = task.get('id')
                    lock = _sched_task_locks.get(task_id)
                    if lock is None:
                        lock = threading.Event()
                        _sched_task_locks[task_id] = lock
                    if lock.is_set():
                        continue  # Previous run still executing
                    task['last_run'] = now
                    _save_scheduled(scheduled_tasks)
                    macro_name = task.get('macro')
                    with _macro_lock: steps = macros.get(macro_name, [])
                    if steps:
                        def _run(lock=lock, s=steps):
                            try:
                                if cfg.watch_only: return
                                deadline = time.time() + MACRO_TIMEOUT
                                for step in s:
                                    if time.time() > deadline: break
                                    t = step.get('type','')
                                    try:
                                        if t == 'type':
                                            if cfg.no_keyboard: pass
                                            else: type_text(step.get('text',''))
                                        else:
                                            with _pyautogui_lock:
                                                if   t=='key' and not cfg.no_keyboard:      pyautogui.press(map_key(step['key']))
                                                elif t=='shortcut' and not cfg.no_keyboard: pyautogui.hotkey(*[map_key(k) for k in step['keys']])
                                                elif t=='click' and not cfg.no_mouse:
                                                    bt = step.get('btn','left')
                                                    if   bt=='left':   pyautogui.click()
                                                    elif bt=='right':  pyautogui.rightClick()
                                                    elif bt=='double': pyautogui.doubleClick()
                                                elif t=='scroll' and not cfg.no_mouse: pyautogui.scroll(int(step.get('dy',0)))
                                        delay = step.get('delay', 0.1)
                                        if delay > 0: time.sleep(min(delay, max(0, deadline - time.time())))
                                    except Exception as e: _vprint(f"sched step: {e}", flush=True)
                            finally:
                                lock.clear()
                        lock.set()
                        threading.Thread(target=_run, daemon=True).start()
                        _log_event('sched_run', macro_name)
        time.sleep(10)

# lockout vars already declared above — no need for separate flag

def _check_linux_compatibility():
    if platform.system() != 'Linux': return []
    errors = []
    if 'DISPLAY' not in os.environ:
        if 'WAYLAND_DISPLAY' in os.environ:
            errors.append('Wayland detected without DISPLAY; run xwayland or use X11 session if pyautogui not working.')
        else:
            errors.append('DISPLAY variable not set; headless mode. Use xvfb-run to start the app.')
    import shutil
    if not shutil.which('xclip') and not shutil.which('xsel'):
        errors.append('xclip/xsel not installed; clipboard sync may not work.')
    if not shutil.which('xdotool'):
        errors.append('xdotool not installed; virtual keyboard may be slower or unavailable on Linux.')
    return errors

# ── Compact binary input protocol (client→server) ───────────────────────────
# 7-byte events instead of ~50-byte JSON. Used for high-frequency input only;
# complex events (type/shortcut/stream_config) stay JSON. Decoded to the same
# dict shape that _dispatch already understands, so one code path handles both.
import struct as _struct
_BIN_KEYS = [
    'ctrl','alt','shift','win','enter','backspace','tab','space','escape','delete',
    'up','down','left','right','home','end','pageup','pagedown','insert',
    'f1','f2','f3','f4','f5','f6','f7','f8','f9','f10','f11','f12',
    'a','b','c','d','e','f','g','h','i','j','k','l','m','n','o','p','q','r','s','t',
    'u','v','w','x','y','z','0','1','2','3','4','5','6','7','8','9',
]
_BIN_CLICK = {0:'left',1:'right',2:'double',3:'middle'}

@trace
def _decode_binary_input(buf: bytes):
    """Decode one compact binary input frame → dict (or None if invalid)."""
    if not buf:
        return None
    op = buf[0]
    try:
        if op == 0x10 and len(buf) >= 5:
            dx, dy = _struct.unpack_from('<hh', buf, 1)
            return {'_ev': 'move', 'dx': dx, 'dy': dy}
        if op == 0x11 and len(buf) >= 2:
            return {'_ev': 'click', 'button': _BIN_CLICK.get(buf[1], 'left')}
        if op == 0x12 and len(buf) >= 4:
            dy = _struct.unpack_from('<h', buf, 1)[0]
            smooth = buf[3]
            return {'_ev': 'scroll', 'dy': dy, 'smooth': bool(smooth)}
        if op in (0x13, 0x14) and len(buf) >= 3:
            idx = _struct.unpack_from('<H', buf, 1)[0]
            if 0 <= idx < len(_BIN_KEYS):
                return {'_ev': 'key_down' if op == 0x13 else 'key_up', 'key': _BIN_KEYS[idx]}
            return None
        if op == 0x15 and len(buf) >= 6:
            kind = buf[1]; dx, dy = _struct.unpack_from('<hh', buf, 2)
            ev = {0: 'selector_start', 1: 'selector_move', 2: 'selector_end'}.get(kind)
            if ev: return {'_ev': ev, 'dx': dx, 'dy': dy}
            return None
    except _struct.error:
        return None
    return None

@trace
async def _dispatch(data: dict, ws):
    # mic/audio thread management via _stream properties
    t  = data.get('_ev', data.get('type', ''))
    ip = ws.client.host

    # ── Replay attack protection ───────────────────────────────────────────────
    # Window widened to 10s and the client clock-syncs to server_ts (sent with
    # hmac_ok), so phone/PC clock skew no longer silently drops every input
    # event. A stale/replayed message outside the window is dropped (logged in
    # verbose to aid debugging instead of failing silently).
    _ts = data.get('_ts')
    if _ts is not None:
        try:
            if abs(time.time() - float(_ts) / 1000.0) > 10:
                _vprint(f"\u26a0\ufe0f  dropped stale event '{t}' (clock skew?) ts={_ts}", flush=True)
                return
        except (TypeError, ValueError):
            return

    # ── Group 3: per-event-type rate limiting ─────────────────────────────
    bucket = _ws_buckets.get(t, _ws_buckets['default'])
    if not bucket.consume(ip):
        return

    _mouse_events = {'move','click','scroll','selector_start','selector_move','selector_end'}
    _keyboard_events = {'key','type','key_down','key_up','shortcut'}
    if t in _mouse_events and (cfg.no_mouse or cfg.watch_only): return
    if t in _keyboard_events and (cfg.no_keyboard or cfg.watch_only): return

    if t == 'move':
        dx, dy = int(data.get('dx',0)), int(data.get('dy',0))
        def _do_move():
            if _pyautogui_lock.acquire(blocking=False):
                try: pyautogui.moveRel(dx, dy, duration=0)
                finally: _pyautogui_lock.release()
        asyncio.get_running_loop().run_in_executor(_INPUT_EXECUTOR, _do_move)

    elif t == 'click':
        ct = data.get('button', 'left')
        loop = asyncio.get_running_loop()
        def _do_click():
            with _pyautogui_lock:
                if   ct=='left':   pyautogui.click()
                elif ct=='right':  pyautogui.rightClick()
                elif ct=='double': pyautogui.doubleClick()
                elif ct=='middle': pyautogui.middleClick()
        await loop.run_in_executor(_INPUT_EXECUTOR, _do_click)

    elif t == 'scroll':
        raw = data.get('dy', 0)
        smooth = bool(data.get('smooth'))
        loop = asyncio.get_running_loop()
        if smooth:
            global _scroll_accum
            try: _scroll_accum += float(raw)
            except (TypeError, ValueError): _scroll_accum = 0.0
            step = int(_scroll_accum)        # only whole notches
            if step != 0:
                _scroll_accum -= step
                def _do_scroll(_s=step):
                    with _pyautogui_lock: pyautogui.scroll(_s)
                await loop.run_in_executor(_INPUT_EXECUTOR, _do_scroll)
        else:
            dy = int(raw)
            if dy != 0:
                def _do_scroll():
                    with _pyautogui_lock: pyautogui.scroll(dy)
                await loop.run_in_executor(_INPUT_EXECUTOR, _do_scroll)

    elif t == 'selector_start':
        loop = asyncio.get_running_loop()
        def _do_sel_start():
            with _pyautogui_lock: pyautogui.mouseDown()
        await loop.run_in_executor(_INPUT_EXECUTOR, _do_sel_start)
    elif t == 'selector_move':
        dx, dy = int(data.get('dx',0)), int(data.get('dy',0))
        def _do_sel_move():
            if _pyautogui_lock.acquire(blocking=False):
                try: pyautogui.moveRel(dx, dy, duration=0)
                finally: _pyautogui_lock.release()
        asyncio.get_running_loop().run_in_executor(_INPUT_EXECUTOR, _do_sel_move)
    elif t == 'selector_end':
        loop = asyncio.get_running_loop()
        def _do_sel_end():
            with _pyautogui_lock: pyautogui.mouseUp()
        await loop.run_in_executor(_INPUT_EXECUTOR, _do_sel_end)

    elif t == 'shortcut':
        keys   = [map_key(k) for k in data.get('keys',[])]
        system = platform.system()
        loop   = asyncio.get_running_loop()
        if system == 'Linux':
            keys = ['super' if k in ('winleft','winright','command','cmd') else k for k in keys]
        has_win = any(k in ('winleft','winright') for k in keys)
        has_cmd = any(k in ('command','cmd','super') for k in keys)
        try:
            if system == 'Windows' and has_win:
                ok = await loop.run_in_executor(_INPUT_EXECUTOR, _press_win_shortcut, keys)
                if not ok:
                    def _do_shortcut_win_fb(_k=keys):
                        with _pyautogui_lock: pyautogui.hotkey(*_k)
                    await loop.run_in_executor(_INPUT_EXECUTOR, _do_shortcut_win_fb)
            elif system == 'Darwin' and (has_win or has_cmd):
                mac_keys = ['command' if k in ('winleft','winright','command','cmd') else k for k in keys]
                def _do_shortcut_mac(_k=mac_keys):
                    with _pyautogui_lock: pyautogui.hotkey(*_k)
                await loop.run_in_executor(_INPUT_EXECUTOR, _do_shortcut_mac)
            else:
                def _do_shortcut_else(_k=keys):
                    with _pyautogui_lock: pyautogui.hotkey(*_k)
                await loop.run_in_executor(_INPUT_EXECUTOR, _do_shortcut_else)
        except Exception as e: _vprint(f"shortcut error: {e}", flush=True)

    elif t == 'key':
        key    = map_key(data.get('key', ''))
        system = platform.system()
        loop   = asyncio.get_running_loop()
        if system == 'Linux':
            if _virtual_kb_device:
                key_code = getattr(uinput, f'KEY_{key.upper()}', None)
                if key_code:
                    def _vkey_press(_kc=key_code):
                        _send_virtual_key(_kc, True); time.sleep(0.01); _send_virtual_key(_kc, False)
                    await loop.run_in_executor(_INPUT_EXECUTOR, _vkey_press)
            elif SUBPROCESS_AVAILABLE: await loop.run_in_executor(_INPUT_EXECUTOR, _send_xdotool_key, key)
            else:
                def _do_key_linux_fb(_k=key):
                    try:
                        with _pyautogui_lock: pyautogui.press(_k)
                    except Exception as e: _vprint(f"key: {e}", flush=True)
                await loop.run_in_executor(_INPUT_EXECUTOR, _do_key_linux_fb)
        else:
            def _do_key(_k=key):
                try:
                    with _pyautogui_lock: pyautogui.press(_k)
                except Exception as e: _vprint(f"key: {e}", flush=True)
            await loop.run_in_executor(_INPUT_EXECUTOR, _do_key)

    elif t == 'type':
        text   = data.get('text', '')
        system = platform.system()
        loop   = asyncio.get_running_loop()
        # Check if text contains non-ASCII characters
        needs_unicode = any(ord(c) > 127 for c in text)
        if system == 'Linux':
            if needs_unicode:
                # Non-ASCII text (Arabic, etc.) - use clipboard/xdotool which support Unicode
                if SUBPROCESS_AVAILABLE: await loop.run_in_executor(_INPUT_EXECUTOR, _send_xdotool_text, text)
                else: await loop.run_in_executor(_INPUT_EXECUTOR, type_text, text)
            else:
                # ASCII text - can use virtual keyboard for lower latency
                if _virtual_kb_device: await loop.run_in_executor(_INPUT_EXECUTOR, _send_virtual_text, text)
                elif SUBPROCESS_AVAILABLE: await loop.run_in_executor(_INPUT_EXECUTOR, _send_xdotool_text, text)
                else: await loop.run_in_executor(_INPUT_EXECUTOR, type_text, text)
        else: await loop.run_in_executor(_INPUT_EXECUTOR, type_text, text)

    elif t == 'key_down':
        _key = map_key(data.get('key',''))
        loop = asyncio.get_running_loop()
        def _do_kd():
            try:
                with _pyautogui_lock: pyautogui.keyDown(_key)
            except Exception as e: _vprint(f"key_down: {e}", flush=True)
        await loop.run_in_executor(_INPUT_EXECUTOR, _do_kd)

    elif t == 'key_up':
        _key = map_key(data.get('key',''))
        loop = asyncio.get_running_loop()
        def _do_ku():
            try:
                with _pyautogui_lock: pyautogui.keyUp(_key)
            except Exception as e: _vprint(f"key_up: {e}", flush=True)
        await loop.run_in_executor(_INPUT_EXECUTOR, _do_ku)

    elif t == 'stream_config':
        with _stream_config_lock:
            if 'height'       in data: stream_config['height']          = max(120, min(2160, int(data['height'])))
            if 'quality'      in data: stream_config['quality']         = max(10, min(100, int(data['quality'])))
            if 'fps'          in data: stream_config['fps']             = max(1, min(60, int(data['fps'])))
            if 'monitor'      in data: stream_config['monitor']         = max(1, int(data['monitor']))
            if 'scale'        in data: stream_config['scale']           = max(0.1, min(2.0, float(data['scale'])))
            if 'grey'         in data: stream_config['grey']            = bool(data['grey'])
            if 'codec'        in data: stream_config['codec']           = data['codec'] if data['codec'] in ('auto', 'jpeg', 'h264', 'vp8') else 'auto'
            if 'cursor_color' in data:
                hex_c = data['cursor_color'].lstrip('#')
                r, g, b = int(hex_c[0:2],16), int(hex_c[2:4],16), int(hex_c[4:6],16)
                stream_config['cursor_color_bgr'] = (b, g, r)

    elif t == 'set_monitor':
        with _stream_config_lock:
            new_mon = max(1, int(data.get('index', 1)))
            changed = stream_config.get('monitor') != new_mon
            stream_config['monitor'] = new_mon
        if changed and _stream.screen_streaming:
            await asyncio.get_running_loop().run_in_executor(
                _EXECUTOR, _stream.restart_screen, 'WS', screen_worker)

    elif t == 'screen_start':
        if not _stream.start_screen('WS', screen_worker):
            pass  # already streaming

    elif t == 'screen_stop':
        _stream.stop_screen()
        _update_stream_status()

    elif t == 'mic_start':
        if _stream.mic_thread and _stream.mic_thread.is_alive():
            _stream.mic_active = False
            try: _mic_queue.put_nowait(None)
            except: pass
            await asyncio.get_running_loop().run_in_executor(_EXECUTOR, _stream.mic_thread.join, 1.0)
        while not _mic_queue.empty():
            try: _mic_queue.get_nowait()
            except: break
        _stream.start_mic(_mic_worker)
        _log_event('mic_start', ip=ip)

    elif t == 'mic_stop':
        _stream.stop_mic()
        try: _mic_queue.put_nowait(None)
        except: pass
        _log_event('mic_stop', ip=ip)

    elif t == 'mic_chunk':
        if not _stream.mic_active: return
        if not (_stream.mic_thread and _stream.mic_thread.is_alive()):
            _stream.mic_active = False
            return
        raw = data.get('data')
        if not raw: return
        try:
            pcm = base64.b64decode(raw)
            if len(pcm) > 65536: return  # sanity cap
            try: _mic_queue.put_nowait(pcm)
            except _queue.Full: pass
        except Exception as e: _vprint(f"mic_chunk: {e}", flush=True)

    elif t == 'audio_start':
        if _stream.audio_thread and _stream.audio_thread.is_alive(): return
        try:
            import sounddevice as _sd
            devs = _sd.query_devices()
            has_input = any(d.get('max_input_channels', 0) > 0 for d in devs)
            if not has_input:
                try: await ws.send_json({'type': 'error', 'msg': 'no_audio_input'})
                except: pass
                _log_event('audio_start_fail', ip=ip, detail='no input device')
                return
        except ImportError:
            try: await ws.send_json({'type': 'error', 'msg': 'sounddevice_unavailable'})
            except: pass
            return
        _stream.start_audio(_audio_worker)
        _log_event('audio_start', ip=ip)

    elif t == 'audio_stop':
        _stream.stop_audio()
        _log_event('audio_stop', ip=ip)

    # ── Auto-Sleep / Wake (client→server notification) ─────────────────────
    elif t == 'client_sleep':
        # Client entered sleep mode → stop streaming to save resources
        if _session.ws is ws:
            _stream.stop_screen()
            _stream.stop_audio()
            _stream.stop_mic()
            _session.put_to_sleep(ws)
            _log_event('client_sleep', ip=ip, detail=f'duration_setting={data.get("timeout", "default")}s')
            _vprint(f"💤 Client {ip} entered sleep mode — streaming paused", flush=True)

    elif t == 'client_wake':
        # Client woke up → restart streaming
        if _session.ws is ws:
            _session.wake_up(ws)
            _log_event('client_wake', ip=ip, detail=f'slept_for={int(_session.sleep_duration)}s')
            _vprint(f"☀️  Client {ip} woke up — streaming resumed", flush=True)


# ── Routes extracted to pd_routes module (refactor) ──

def _auto_install_deps():
    """First-run convenience for non-technical users: detect missing optional
    libraries that materially improve the experience and try to pip-install them
    once. Best-effort and fully silent on failure — the app already degrades
    gracefully without any of these (custom fallbacks everywhere). Skipped if
    PORTDESK_NO_AUTOINSTALL is set, or if running frozen (PyInstaller).
    Recommended packages per platform are chosen to give kernel-level input,
    HTTPS, and fast screen capture."""
    import os as _os, sys as _sys, platform as _pf, importlib.util as _ilu
    if _os.environ.get('PORTDESK_NO_AUTOINSTALL'):
        return
    if getattr(_sys, 'frozen', False):   # bundled exe — deps are baked in
        return
    # (import_name, pip_name, why)
    wanted = [
        ('cryptography', 'cryptography', 'auto-HTTPS certificate'),
        ('numpy',        'numpy',        'screen capture/encode'),
        ('cv2',          'opencv-python','screen encode (JPEG/diff)'),
    ]
    _sysname = _pf.system()
    if _sysname == 'Windows':
        wanted += [('mss', 'mss', 'screen capture'),
                   ('dxcam', 'dxcam', 'fast GPU screen capture'),
                   ('turbojpeg', 'PyTurboJPEG', 'hardware-accelerated JPEG encoding')]
    elif _sysname == 'Linux':
        wanted += [('mss', 'mss', 'screen capture'),
                   ('Xlib', 'python-xlib', 'fast X11 input (XTest)'),
                   ('evdev', 'evdev', 'kernel input (Wayland/headless)'),
                   ('uvloop', 'uvloop', '2x faster event loop'),
                   ('turbojpeg', 'PyTurboJPEG', 'hardware-accelerated JPEG encoding')]
    elif _sysname == 'Darwin':
        wanted += [('mss', 'mss', 'screen capture'),
                   ('Quartz', 'pyobjc-framework-Quartz', 'native input/capture'),
                   ('uvloop', 'uvloop', '2x faster event loop'),
                   ('turbojpeg', 'PyTurboJPEG', 'hardware-accelerated JPEG encoding')]
    missing = [(imp, pip, why) for (imp, pip, why) in wanted if _ilu.find_spec(imp) is None]
    if not missing:
        return
    print('  \U0001f4e6 First run: installing recommended packages for the best experience…', flush=True)
    print('     (set PORTDESK_NO_AUTOINSTALL=1 to skip; the app works without them too)', flush=True)
    for imp, pip, why in missing:
        try:
            print(f'     \u2192 {pip}  ({why}) …', flush=True)
            _subprocess_install(pip)
        except Exception as _e:
            print(f'       \u26a0 could not install {pip}: {_e} — continuing with fallback', flush=True)

def _subprocess_install(pip_name):
    import sys as _sys, subprocess as _sp
    _sp.run([_sys.executable, '-m', 'pip', 'install', '--quiet', pip_name],
            check=True, timeout=180)


# ── Register all 51 routes (extracted to pd_routes module) ──
# Refresh the module registration so pd_routes can see ALL globals defined
# in this file (not just the ones that existed at bootstrap time).
# Register unconditionally so pd_routes can find us.
sys.modules[SERVER_MODULE_NAME] = sys.modules.get('__main__', sys.modules.get(SERVER_MODULE_NAME, sys.modules[__name__]))

import pd_routes
from pd_routes import _get_cert_fingerprint


if __name__ == '__main__':
    import argparse
    # Windows: use selector event loop to avoid proactor SSL race (bpo-40471)
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    _auto_install_deps()

    def _parse_size(s):
        s = s.strip().upper()
        if s.endswith('GB'): return int(float(s[:-2]) * 1024**3)
        if s.endswith('MB'): return int(float(s[:-2]) * 1024**2)
        if s.endswith('KB'): return int(float(s[:-2]) * 1024)
        return int(s)

    parser = argparse.ArgumentParser(prog='portdesk-server', description='PortDesk Server')
    parser.add_argument('-p', '--port',         type=int,   default=5000,      metavar='PORT')
    parser.add_argument('-H', '--host',         type=str,   default='0.0.0.0', metavar='HOST')
    parser.add_argument('-s', '--ssl',          action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('-c', '--cert',         type=str,   default=None,      metavar='PATH')
    parser.add_argument('-k', '--key',          type=str,   default=None,      metavar='PATH')
    parser.add_argument('--whitelist',          type=str,   default=None,      metavar='PATH')
    parser.add_argument('--watch-only',         action='store_true')
    parser.add_argument('--no-explorer',        action='store_true')
    parser.add_argument('--upload-limit',       type=str,   default=None,      metavar='SIZE')
    parser.add_argument('--backend',            type=str,   default=None,      choices=['dxcam','mss'])
    parser.add_argument('--no-webrtc',          action='store_true')
    parser.add_argument('--no-https',           action='store_true', help='Disable auto-HTTPS and run plain HTTP')
    parser.add_argument('--h264',               action='store_true', help='Use H264/MSE streaming instead of the default JPEG differential')
    parser.add_argument('--scale',              type=float, default=1.0,       metavar='FACTOR')
    parser.add_argument('--grey',               action='store_true')
    parser.add_argument('--tray',               action='store_true')
    parser.add_argument('--no-mouse',           action='store_true')
    parser.add_argument('--no-keyboard',        action='store_true')
    parser.add_argument('--verbose',            action='store_true', help='Enable full tracing: function entry/exit, subprocess calls, timestamps')
    parser.add_argument('--no-upload',          action='store_true')
    parser.add_argument('--no-download',        action='store_true')
    args = parser.parse_args()

    cfg.watch_only   = args.watch_only
    cfg.no_explorer  = args.no_explorer
    cfg.no_mouse     = args.no_mouse
    cfg.no_keyboard  = args.no_keyboard
    cfg.no_upload    = args.no_upload
    cfg.no_download  = args.no_download
    cfg.no_webrtc    = args.no_webrtc
    cfg.no_h264      = not args.h264
    cfg.grey         = args.grey
    cfg.scale        = args.scale
    cfg.backend      = args.backend
    cfg.verbose      = args.verbose
    if args.upload_limit:
        cfg.upload_limit = _parse_size(args.upload_limit)
        set_max_body_size(cfg.upload_limit)

    if args.whitelist:
        SECURITY_FILE = args.whitelist

    _port = args.port
    _host = args.host

    # ── Startup config validation ──────────────────────────────────────────
    _config_errors = []
    if not (1 <= _port <= 65535):
        _config_errors.append(f'Invalid port: {_port} (must be 1–65535)')
    try:
        import ipaddress as _ipa
        _ipa.ip_address(_host) if _host != '0.0.0.0' else None
    except ValueError:
        _config_errors.append(f'Invalid host: {_host}')
    if _config_errors:
        for err in _config_errors:
            print(f'❌ Config error: {err}', flush=True)
        sys.exit(1)
    # Warn if running on privileged port without admin
    if _port < 1024:
        try:
            import ctypes as _ct
            if platform.system() == 'Windows' and not _ct.windll.shell32.IsUserAnAdmin():
                print(f'⚠ Port {_port} < 1024 may require admin privileges on Windows', flush=True)
        except Exception:
            pass

    _cert_data = args.cert or os.path.join(DATA_DIR, 'cert.pem')
    _key_data  = args.key  or os.path.join(DATA_DIR, 'key.pem')
    if not args.cert and not os.path.isfile(_cert_data):
        _cert_data = os.path.join(BASE_DIR, 'cert.pem')
    if not args.key and not os.path.isfile(_key_data):
        _key_data = os.path.join(BASE_DIR, 'key.pem')
    cert_file = _cert_data
    key_file  = _key_data
    # Default to HTTPS: if no cert/key exist anywhere, auto-generate a
    # self-signed pair in DATA_DIR on first run (user can delete them to use
    # plain HTTP). --no-https opts out explicitly.
    if not getattr(args, 'no_https', False) and not (os.path.isfile(cert_file) and os.path.isfile(key_file)):
        _dc = os.path.join(DATA_DIR, 'cert.pem'); _dk = os.path.join(DATA_DIR, 'key.pem')
        if _ensure_self_signed_cert(_dc, _dk):
            cert_file, key_file = _dc, _dk
    use_https = (not getattr(args, 'no_https', False)) and (os.path.isfile(cert_file) and os.path.isfile(key_file))

    _log_level = 'debug' if args.verbose else 'warning'

    if args.tray:
        try:
            import pystray, PIL.Image as _PIm
            _icon_img = _PIm.new('RGB', (16,16), color=(30, 30, 200))
            def _quit_tray(icon, item): icon.stop(); os._exit(0)
            _tray_icon = pystray.Icon('PortDesk', _icon_img, 'PortDesk',
                                      menu=pystray.Menu(pystray.MenuItem('Quit', _quit_tray)))
            threading.Thread(target=_tray_icon.run, daemon=True).start()
        except ImportError:
            print('⚠ --tray requires pystray and Pillow: pip install pystray Pillow', flush=True)

    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); local_ip = s.getsockname()[0]; s.close()
    except: local_ip = '0.0.0.0'

    proto = 'https' if use_https else 'http'
    import sys as _sys_check
    _is_admin = False
    try:
        if platform.system() == 'Windows':
            import ctypes; _is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        else:
            _is_admin = (os.geteuid() == 0)
    except Exception: pass

    print(f"\n{'═'*52}", flush=True)
    print(f"  🎮  PortDesk v1.0 ", flush=True)
    print(f"{'─'*52}", flush=True)
    print(f"  ✍  Developed by  :  Lucky_abdo", flush=True)
    print(f"  🔗  GitHub        :  github.com/Lucky-abdo/PortDesk", flush=True)
    print(f"{'─'*52}", flush=True)
    if not _is_admin:
        print(f"  ⚠️  Run as Administrator/root for full functionality", flush=True)
        print(f"{'─'*52}", flush=True)
    print(f"  ℹ  WebRTC screen streaming: {'✅ available' if WEBRTC_AVAILABLE and not args.no_webrtc else '⚠️ disabled' if args.no_webrtc else '⚠️ aiortc not installed — WS fallback only'}", flush=True)
    print(f"  ℹ  HTTP Server            : ✅ custom asyncio (zero external deps)", flush=True)
    _sys_name = platform.system()
    _is_wayland = os.environ.get('WAYLAND_DISPLAY') or os.environ.get('XDG_SESSION_TYPE','').lower() == 'wayland'
    import shutil as _shutil
    _has_ffmpeg   = bool(_shutil.which('ffmpeg'))
    _has_scrot    = bool(_shutil.which('scrot'))
    _has_sc       = bool(_shutil.which('screencapture'))
    try:
        import xlib as _xlib_check; _has_xlib = True
    except ImportError:
        try: import Xlib as _xlib_check; _has_xlib = True
        except ImportError: _has_xlib = False
    try:
        import AVFoundation as _avf; _has_avf = True
    except ImportError:
        _has_avf = False
    try:
        import win32api as _w32; _has_win32 = True
    except ImportError:
        _has_win32 = False

    if args.backend == 'mss':
        _be = 'forced: mss (cross-platform)'
    elif args.backend == 'dxcam':
        _be = 'forced: dxcam (DirectX/Windows)'
    elif _sys_name == 'Windows':
        import sys as _sv
        _win_modern = _sv.getwindowsversion().major >= 8
        if _win_modern:
            _be = 'dxcam → BitBlt (win32api) → mss' if DXCAM_AVAILABLE and _has_win32 else \
                  'dxcam → mss' if DXCAM_AVAILABLE else \
                  'BitBlt (win32api) → mss' if _has_win32 else 'mss'
        else:
            _be = 'BitBlt (win32api) → mss' if _has_win32 else 'mss  [legacy Windows]'
    elif _sys_name == 'Linux':
        if _is_wayland:
            _be = 'pipewire → ffmpeg (pipewire) → mss' if _has_ffmpeg else 'mss  ⚠️ Wayland: limited support'
        else:
            _be = 'ffmpeg x11grab → python-xlib → mss' if _has_ffmpeg and _has_xlib else \
                  'ffmpeg x11grab → mss' if _has_ffmpeg else \
                  'python-xlib → scrot → mss' if _has_xlib and _has_scrot else \
                  'python-xlib → mss' if _has_xlib else \
                  'scrot → mss' if _has_scrot else 'mss  [legacy Linux]'
    elif _sys_name == 'Darwin':
        import sys as _sv
        _mac_modern = _sv.version_info >= (3, 8) and platform.mac_ver()[0] >= '10.15'
        if _mac_modern:
            _be = 'AVFoundation → Quartz → screencapture CLI' if _has_avf and _has_sc else \
                  'Quartz → screencapture CLI' if _has_sc else 'Quartz → mss'
        else:
            _be = 'Quartz → screencapture CLI' if _has_sc else 'mss  [legacy macOS]'
    else:
        _be = 'mss (generic fallback)'
    print(f"  ℹ  Screen capture backend : {_be}", flush=True)
    if args.watch_only:   print(f"  ℹ  Mode                   : 👁 Watch-only", flush=True)
    if args.no_explorer:  print(f"  ℹ  File explorer          : ⛔ disabled", flush=True)
    if args.upload_limit: print(f"  ℹ  Upload limit           : {args.upload_limit}", flush=True)
    if args.scale != 1.0: print(f"  ℹ  Stream scale           : {args.scale}x", flush=True)
    if args.grey:         print(f"  ℹ  Stream color           : greyscale", flush=True)
    print(f"{'═'*52}", flush=True)
    print(f"  [USB]  adb reverse tcp:{_port} tcp:{_port} → {proto}://localhost:{_port}", flush=True)
    print(f"  [WiFi] {proto}://{local_ip}:{_port}", flush=True)
    if use_https:
        print(f"  🔒 HTTPS enabled", flush=True)
        _fp = _get_cert_fingerprint()
        if _fp:
            print(f"{'─'*52}", flush=True)
            print(f"  🔐 Cert Fingerprint (SHA-256):", flush=True)
            print(f"  {_fp}", flush=True)
            print(f"  ↑ Verify this matches on your mobile client (TOFU)", flush=True)
    else:
        print(f"{'─'*52}", flush=True)
        print(f"  ⚠⚠⚠  HTTP MODE — TRAFFIC IS UNENCRYPTED  ⚠⚠⚠", flush=True)
        print(f"  Screen stream, keyboard input and clipboard", flush=True)
        print(f"  are visible to anyone on your network.", flush=True)
        print(f"  Run gen_cert.py to enable HTTPS.", flush=True)
    print(f"{'═'*52}", flush=True)
    print(f"  🚀  LAUNCH FLAGS  (use when starting the server)", flush=True)
    print(f"{'─'*52}", flush=True)
    print(f"  ⚠  These must be passed at startup — cannot change later", flush=True)
    print(f"  --port N          Listen on port N (default 8000)", flush=True)
    print(f"  --host X          Bind to host X  (default 0.0.0.0)", flush=True)
    print(f"  --backend mss     Force MSS screen capture (slower, cross-platform)", flush=True)
    print(f"  --backend dxcam   Force DXCam capture (Windows only, fastest)", flush=True)
    print(f"  --no-webrtc       Disable WebRTC — use WebSocket streaming only", flush=True)
    print(f"  --upload-limit N  Max upload size in MB", flush=True)
    print(f"  --scale N         Scale stream (e.g. 0.75 = 75% resolution)", flush=True)
    print(f"{'─'*52}", flush=True)
    print(f"  🔄  RUNTIME FLAGS  (toggle live from Settings inside the app)", flush=True)
    print(f"{'─'*52}", flush=True)
    print(f"  ⚠  Also usable at launch as startup defaults", flush=True)
    print(f"  --watch-only      View screen only — no mouse/keyboard control", flush=True)
    print(f"  --no-explorer     Disable file explorer completely", flush=True)
    print(f"  --no-mouse        Disable remote mouse control", flush=True)
    print(f"  --no-keyboard     Disable remote keyboard control", flush=True)
    print(f"  --no-upload       Disable file upload from client to PC", flush=True)
    print(f"  --no-download     Disable file download from PC to client", flush=True)
    print(f"  --grey            Stream in greyscale (saves bandwidth)", flush=True)
    print(f"  --verbose         Show detailed server activity in terminal", flush=True)
    print(f"{'═'*52}\n", flush=True)
    print(f"  \u2705 BUILD: PortDesk-FIXED v11 (auto-deps, HTTP-Range, log-verify+rotate, atomic-saves)", flush=True)
    print(f"     If you do NOT see this line, you are running an OLD file!", flush=True)
    print(f"  📡 Stream status will appear here when client connects...", flush=True)

    # ── Start background threads (lifespan replacement) ────────────────────
    # NOTE: _start_lifespan() is now called from inside on_startup so that
    # _loop is set to the actual running event loop before threads start.
    def _on_startup(loop):
        global _loop
        _loop = loop
        _loop_ref.set(loop)
        _start_lifespan()

    # ── Run custom HTTP/WebSocket server ───────────────────────────────────
    _MAX_CRASH_RESTARTS = 5
    _CRASH_WINDOW       = 60
    _crash_times        = []

    while True:
        try:
            app.run(
                host=_host, port=_port,
                ssl_cert=cert_file if use_https else None,
                ssl_key=key_file if use_https else None,
                log_level=_log_level,
                on_startup=_on_startup,
            )
            break  # Normal exit
        except KeyboardInterrupt:
            print("\n🛑 PortDesk stopped by user.", flush=True)
            _stop_lifespan()
            break
        except SystemExit:
            _stop_lifespan()
            break
        except Exception as _crash_err:
            now = time.time()
            _crash_times = [t for t in _crash_times if now - t < _CRASH_WINDOW]
            _crash_times.append(now)
            if len(_crash_times) > _MAX_CRASH_RESTARTS:
                print(f"\n💥 PortDesk crashed {_MAX_CRASH_RESTARTS+1} times in {_CRASH_WINDOW}s — giving up.", flush=True)
                print(f"   Last error: {_crash_err}", flush=True)
                raise
            print(f"\n💥 PortDesk crashed: {_crash_err}", flush=True)
            print(f"🔄 Restarting in 3 seconds... (attempt {len(_crash_times)}/{_MAX_CRASH_RESTARTS})", flush=True)
            time.sleep(3)
            _stream.stop_all()
            _update_stream_status()

