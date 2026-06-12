"""
pd_capture.py — screen capture backend chain for PortDesk (extracted module).

Holds all platform capture backends (dxcam, BitBlt, mss, ffmpeg-x11, xlib,
scrot, grim, Quartz, screencapture) + backend selection logic.

Heavy deps (numpy/cv2/dxcam/mss) and the runtime FLAG_BACKEND live in the main
server module, so they are injected once via init() to keep this module loosely
coupled without changing behavior.
"""
from __future__ import annotations
import os
import sys
import re
import time
import platform
import subprocess

# ── Injected dependencies (set by init from the server) ─────────────────────
np = None
cv2 = None
_dxcam = None
_mss = None
DXCAM_AVAILABLE = False
CV2_AVAILABLE = False
MSS_AVAILABLE = False
_vprint = lambda *a, **k: None
_get_flag_backend = lambda: None

def init(*, numpy_mod=None, cv2_mod=None, dxcam_mod=None, mss_mod=None,
         dxcam_available=False, cv2_available=False, mss_available=False,
         vprint=None, get_flag_backend=None):
    """Inject server-level deps once at startup. Must be called before use."""
    global np, cv2, _dxcam, _mss, DXCAM_AVAILABLE, CV2_AVAILABLE, MSS_AVAILABLE
    global _vprint, _get_flag_backend
    np = numpy_mod; cv2 = cv2_mod; _dxcam = dxcam_mod; _mss = mss_mod
    DXCAM_AVAILABLE = dxcam_available
    CV2_AVAILABLE = cv2_available
    MSS_AVAILABLE = mss_available
    if vprint: _vprint = vprint
    if get_flag_backend: _get_flag_backend = get_flag_backend

# ══ Capture Backend Chain ════════════════════════════════════════════════════
# Each backend exposes: .name, .grab(mon_idx) → (np.ndarray BGR, dict mon_info), .close()
# mon_info = {'left': int, 'top': int, 'width': int, 'height': int}

class _CaptureBase:
    name = 'unknown'
    def grab(self, mon_idx=0): raise NotImplementedError
    def close(self): pass

class _MSSCaptureBackend(_CaptureBase):
    name = 'mss'
    def __init__(self):
        self._sct = _mss.mss()
    def grab(self, mon_idx=0):
        mons = self._sct.monitors
        m = mons[min(max(1, mon_idx + 1), len(mons) - 1)]
        img = self._sct.grab(m)
        arr = np.frombuffer(img.raw, dtype=np.uint8).reshape((img.height, img.width, 4))[:, :, :3]
        return arr, {'left': m['left'], 'top': m['top'], 'width': m['width'], 'height': m['height']}
    def close(self):
        try: self._sct.close()
        except: pass

class _DXCamCaptureBackend(_CaptureBase):
    name = 'dxcam'
    def __init__(self, mon_idx=0):
        self._mon_idx = mon_idx
        self._cam = _dxcam.create(output_idx=mon_idx, output_color='BGR')
        self._cam.start(target_fps=60, video_mode=True)
        import mss as _mss_tmp
        with _mss_tmp.mss() as s:
            m = s.monitors[min(max(1, mon_idx + 1), len(s.monitors) - 1)]
            self._mon = {'left': m['left'], 'top': m['top'], 'width': m['width'], 'height': m['height']}
    def grab(self, mon_idx=0):
        if self._cam is None:
            return None, self._mon
        frame = self._cam.grab()
        return (frame, self._mon) if frame is not None else (None, self._mon)
    def close(self):
        # Stop capture, then release via a SINGLE path. Calling both
        # camera.release() AND dxcam.clean_up() double-frees the same COM
        # interface → "access violation writing 0x0" in comtypes. We use
        # clean_up() only (it stops + releases every camera + clears the
        # singleton factory) so the next create() rebuilds cleanly.
        cam = self._cam
        self._cam = None
        if cam is not None:
            try: cam.stop()
            except Exception: pass
        try:
            if hasattr(_dxcam, 'clean_up'):
                _dxcam.clean_up()
        except Exception:
            pass

class _BitBltCaptureBackend(_CaptureBase):
    name = 'BitBlt'
    def __init__(self):
        import win32gui as _wg, win32ui as _wu, win32con as _wc
        import win32api as _wa
        self._wg, self._wu, self._wc, self._wa = _wg, _wu, _wc, _wa
    def _mon_rect(self, mon_idx):
        import ctypes, ctypes.wintypes
        mons = []
        PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong, ctypes.c_ulong,
                                   ctypes.POINTER(ctypes.wintypes.RECT), ctypes.c_double)
        def cb(hm, hdc, r, d): mons.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom)); return True
        ctypes.windll.user32.EnumDisplayMonitors(None, None, PROC(cb), 0)
        if mon_idx < len(mons): l, t, r, b = mons[mon_idx]
        else: l, t, r, b = 0, 0, self._wa.GetSystemMetrics(0), self._wa.GetSystemMetrics(1)
        return l, t, r - l, b - t
    def grab(self, mon_idx=0):
        try:
            x, y, w, h = self._mon_rect(mon_idx)
            hwnd = self._wg.GetDesktopWindow()
            wdc  = self._wg.GetWindowDC(hwnd)
            dc   = self._wu.CreateDCFromHandle(wdc)
            mdc  = dc.CreateCompatibleDC()
            bmp  = self._wu.CreateBitmap()
            bmp.CreateCompatibleBitmap(dc, w, h)
            mdc.SelectObject(bmp)
            mdc.BitBlt((0, 0), (w, h), dc, (x, y), self._wc.SRCCOPY)
            bits = bmp.GetBitmapBits(True)
            arr  = np.frombuffer(bits, dtype=np.uint8).reshape((h, w, 4))[:, :, :3].copy()
            mdc.DeleteDC(); dc.DeleteDC()
            self._wg.DeleteObject(bmp.GetHandle())
            self._wg.ReleaseDC(hwnd, wdc)
            return arr, {'left': x, 'top': y, 'width': w, 'height': h}
        except Exception: return None, {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}

class _FFmpegX11CaptureBackend(_CaptureBase):
    name = 'ffmpeg_x11'
    def __init__(self, mon_idx=0):
        import shutil
        self._proc = None
        disp = os.environ.get('DISPLAY', ':0')
        try:
            xr = subprocess.run(['xrandr', '--query'], capture_output=True, timeout=2, text=True)
            mons = re.findall(r'(\d+)x(\d+)\+(\d+)\+(\d+)', xr.stdout)
            if mon_idx < len(mons): w, h, x, y = [int(v) for v in mons[mon_idx]]
            else: w, h, x, y = 1920, 1080, 0, 0
        except: w, h, x, y = 1920, 1080, 0, 0
        self.w, self.h, self.x, self.y = w, h, x, y
        self._mon = {'left': x, 'top': y, 'width': w, 'height': h}
        self._fsize = w * h * 3
        cmd = ['ffmpeg', '-f', 'x11grab', '-framerate', '30',
               '-video_size', f'{w}x{h}', '-i', f'{disp}+{x},{y}',
               '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-']
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, bufsize=self._fsize * 2)
    def grab(self, mon_idx=0):
        if not self._proc: return None, self._mon
        try:
            raw = self._proc.stdout.read(self._fsize)
            if len(raw) < self._fsize: return None, self._mon
            arr = np.frombuffer(raw, dtype=np.uint8).reshape((self.h, self.w, 3))
            return arr, self._mon
        except: return None, self._mon
    def close(self):
        if self._proc:
            try: self._proc.terminate(); self._proc.wait(timeout=1)
            except: pass

class _XlibCaptureBackend(_CaptureBase):
    name = 'python-xlib'
    def __init__(self, mon_idx=0):
        from Xlib import display as _xd, X as _X
        self._X = _X
        self._d = _xd.Display(os.environ.get('DISPLAY', ':0'))
        self._root = self._d.screen().root
        self._mon = self._get_mon(mon_idx)
    def _get_mon(self, mon_idx):
        try:
            from Xlib.ext import randr
            res = randr.get_screen_resources(self._root)
            mons = []
            for crtc in res.crtcs:
                ci = randr.get_crtc_info(self._root, crtc, res.config_timestamp)
                if ci.width > 0: mons.append({'left': ci.x, 'top': ci.y, 'width': ci.width, 'height': ci.height})
            if mon_idx < len(mons): return mons[mon_idx]
        except: pass
        g = self._root.get_geometry()
        return {'left': 0, 'top': 0, 'width': g.width, 'height': g.height}
    def grab(self, mon_idx=0):
        m = self._mon
        try:
            raw = self._root.get_image(m['left'], m['top'], m['width'], m['height'], self._X.ZPixmap, 0xffffffff)
            arr = np.frombuffer(raw.data, dtype=np.uint8).reshape((m['height'], m['width'], 4))
            return arr[:, :, 2::-1].copy(), m
        except: return None, m
    def close(self):
        try: self._d.close()
        except: pass

class _ScrotCaptureBackend(_CaptureBase):
    name = 'scrot'
    def __init__(self):
        import tempfile
        self._tmp = tempfile.mktemp(suffix='.png')
    def grab(self, mon_idx=0):
        try:
            subprocess.run(['scrot', '-o', self._tmp], timeout=2, capture_output=True)
            img = cv2.imread(self._tmp)
            if img is None: return None, {}
            h, w = img.shape[:2]
            return img, {'left': 0, 'top': 0, 'width': w, 'height': h}
        except: return None, {}
    def close(self):
        try: os.unlink(self._tmp)
        except: pass

class _GrimCaptureBackend(_CaptureBase):
    name = 'grim'
    def grab(self, mon_idx=0):
        try:
            r = subprocess.run(['grim', '-t', 'ppm', '-'], capture_output=True, timeout=2)
            if r.returncode or not r.stdout: return None, {}
            \n', 3)
            if lines[0] != blines = r.stdout.split(b''P6': return None, {}
            w, h = map(int, lines[1].split())
            arr = np.frombuffer(lines[3][:w*h*3], dtype=np.uint8).reshape((h, w, 3))[:, :, ::-1]
            return arr, {'left': 0, 'top': 0, 'width': w, 'height': h}
        except: return None, {}

class _QuartzCaptureBackend(_CaptureBase):
    name = 'Quartz'
    def __init__(self):
        import Quartz as _Q; self._Q = _Q
    def grab(self, mon_idx=0):
        try:
            Q = self._Q
            ids = Q.CGGetActiveDisplayList(32, None, None)[1]
            disp = ids[min(mon_idx, len(ids)-1)] if ids else Q.CGMainDisplayID()
            img = Q.CGWindowListCreateImage(Q.CGRectInfinite, Q.kCGWindowListOptionOnScreenOnly,
                                             Q.kCGNullWindowID, Q.kCGWindowImageDefault)
            if img is None: return None, {}
            w, h, bpr = Q.CGImageGetWidth(img), Q.CGImageGetHeight(img), Q.CGImageGetBytesPerRow(img)
            data = Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(img))
            arr = np.frombuffer(data, dtype=np.uint8).reshape((h, bpr//4, 4))[:h, :w, :3]
            return arr[:, :, ::-1], {'left': 0, 'top': 0, 'width': w, 'height': h}
        except: return None, {}

class _ScreencaptureCLIBackend(_CaptureBase):
    name = 'screencapture'
    def __init__(self):
        import tempfile; self._tmp = tempfile.mktemp(suffix='.png')
    def grab(self, mon_idx=0):
        try:
            subprocess.run(['screencapture', '-x', '-t', 'png', self._tmp], timeout=3, capture_output=True)
            img = cv2.imread(self._tmp)
            if img is None: return None, {}
            h, w = img.shape[:2]
            return img, {'left': 0, 'top': 0, 'width': w, 'height': h}
        except: return None, {}
    def close(self):
        try: os.unlink(self._tmp)
        except: pass

def _build_capture_chain(mon_idx=0):
    """Return ordered list of (backend_factory, name) tuples per OS/version."""
    import shutil as _sh
    _sys = platform.system()
    chain = []

    if _get_flag_backend() == 'mss':
        return [(_MSSCaptureBackend, 'mss')]
    if _get_flag_backend() == 'dxcam':
        return [(_DXCamCaptureBackend, 'dxcam')] if DXCAM_AVAILABLE else [(_MSSCaptureBackend, 'mss')]

    if _sys == 'Windows':
        _win_modern = sys.getwindowsversion().major >= 8 if hasattr(sys, 'getwindowsversion') else False
        if _win_modern and DXCAM_AVAILABLE:
            chain.append((lambda mi=mon_idx: _DXCamCaptureBackend(mi), 'dxcam'))
        try:
            import win32gui; chain.append((_BitBltCaptureBackend, 'BitBlt'))
        except ImportError: pass
        chain.append((_MSSCaptureBackend, 'mss'))

    elif _sys == 'Linux':
        _wayland = (os.environ.get('WAYLAND_DISPLAY') or
                    os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland')
        if _wayland:
            if _sh.which('grim'):
                chain.append((_GrimCaptureBackend, 'grim'))
            chain.append((_MSSCaptureBackend, 'mss'))  # fallback if grim missing
        else:
            # X11 order: mss first (in-process SHM grab, fast + very stable),
            # then python-xlib (also in-process). ffmpeg_x11 spawns a persistent
            # subprocess that can stall silently, so it's demoted to a fallback
            # rather than the primary. scrot (spawns a process per frame) is last.
            chain.append((_MSSCaptureBackend, 'mss'))
            try:
                from Xlib import display as _xd
                chain.append((lambda mi=mon_idx: _XlibCaptureBackend(mi), 'python-xlib'))
            except ImportError: pass
            if _sh.which('ffmpeg') and os.environ.get('DISPLAY'):
                chain.append((lambda mi=mon_idx: _FFmpegX11CaptureBackend(mi), 'ffmpeg_x11'))
            if _sh.which('scrot'):
                chain.append((_ScrotCaptureBackend, 'scrot'))

    elif _sys == 'Darwin':
        try:
            import Quartz; chain.append((_QuartzCaptureBackend, 'Quartz'))
        except ImportError: pass
        if _sh.which('screencapture'):
            chain.append((_ScreencaptureCLIBackend, 'screencapture'))
        chain.append((_MSSCaptureBackend, 'mss'))

    else:
        chain.append((_MSSCaptureBackend, 'mss'))

    return chain

def _create_capture_backend(mon_idx=0):
    """Try each backend in priority order, return first working one."""
    for factory, bname in _build_capture_chain(mon_idx):
        try:
            cap = factory()
            # dxcam (and some duplication APIs) return None on the first grab(s)
            # right after start() because the capture thread hasn't produced a
            # frame yet. Retry briefly before declaring the backend dead — this
            # is THE fix that stops the wrong fallback to slow BitBlt.
            arr = None
            _retries = 20 if bname == 'dxcam' else 1
            for _attempt in range(_retries):
                arr, _ = cap.grab(mon_idx)
                if arr is not None and getattr(arr, 'size', 0) > 0:
                    break
                if _retries > 1:
                    time.sleep(0.03)
            if arr is not None and getattr(arr, 'size', 0) > 0:
                _vprint(f"✅ capture backend: {bname}", flush=True)
                return cap
            _vprint(f"⚠ backend '{bname}' produced no frame — skipping", flush=True)
            cap.close()
        except Exception as e:
            _vprint(f"⚠ backend '{bname}' failed: {e}", flush=True)
    return None

