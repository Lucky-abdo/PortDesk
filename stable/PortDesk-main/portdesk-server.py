from flask import Flask, send_file, request, jsonify
from flask_socketio import SocketIO
from collections import defaultdict
from functools import wraps
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pyautogui
import socket as _socket
import json, os, time, ctypes, threading, logging, platform
import queue as _queue
import string as _string
try:
    import numpy as np
    import cv2
    CV2_AVAILABLE = True
    cv2.setNumThreads(2)
except ImportError:
    np = None; cv2 = None; CV2_AVAILABLE = False
import base64
import subprocess

try:
    import mss as _mss
    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

# Virtual keyboard imports
try:
    import uinput  # type: ignore
    UINPUT_AVAILABLE = True
except ImportError:
    uinput = None
    UINPUT_AVAILABLE = False

SUBPROCESS_AVAILABLE = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SECURITY_FILE = os.path.join(BASE_DIR, "portdesk_security.json")

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", logger=False, engineio_logger=False)
pyautogui.FAILSAFE = False
pyautogui.PAUSE    = 0

_pyautogui_lock = threading.Lock()
_sec_lock       = threading.Lock()

# ── Security ───────────────────────────────────────────────────────────────────
def _load_security():
    try:
        with open(SECURITY_FILE) as f: return json.load(f)
    except: return {"whitelist": [], "blacklist": []}

def _save_security():
    tmp = SECURITY_FILE + '.tmp'
    with open(tmp, "w") as f: json.dump(security, f, indent=2)
    os.replace(tmp, SECURITY_FILE)

security    = _load_security()
if "blacklist" not in security: security["blacklist"] = []
_req_counts    = defaultdict(list)
_reject_counts = defaultdict(int)   # IP → times rejected (resets if removed from blacklist)

def _is_rate_limited(ip):
    now, window, limit = time.time(), 10, 50
    with _sec_lock:
        _req_counts[ip] = [t for t in _req_counts[ip] if now - t < window]
        if len(_req_counts[ip]) >= limit: return True
        _req_counts[ip].append(now)
    return False

def _is_allowed(ip):
    if ip in ('127.0.0.1', '::1', 'localhost'): return True
    if ip in security.get("blacklist", []): return False
    wl = security.get("whitelist", [])
    return ip in wl

_pending_ips = set()

def _check_linux_compatibility():
    if platform.system() != 'Linux':
        return []

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

# ── Virtual Keyboard ───────────────────────────────────────────────────────────
_virtual_kb_device = None

def _init_virtual_keyboard():
    global _virtual_kb_device
    if not UINPUT_AVAILABLE or platform.system() != 'Linux':
        return False
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
        print(f"Virtual keyboard init failed: {e}")
        return False

def _send_virtual_key(key_code, press=True):
    if _virtual_kb_device:
        try:
            if press:
                _virtual_kb_device.emit(key_code, 1)
            else:
                _virtual_kb_device.emit(key_code, 0)
        except Exception as e:
            print(f"Virtual key send failed: {e}")

def _send_virtual_text(text):
    for char in text:
        if char.isalpha():
            key = getattr(uinput, f'KEY_{char.upper()}', None)
            if key:
                _send_virtual_key(key, True)
                time.sleep(0.01)
                _send_virtual_key(key, False)
        elif char.isdigit():
            key = getattr(uinput, f'KEY_{char}', None)
            if key:
                _send_virtual_key(key, True)
                time.sleep(0.01)
                _send_virtual_key(key, False)
        elif char == ' ':
            _send_virtual_key(uinput.KEY_SPACE, True)
            time.sleep(0.01)
            _send_virtual_key(uinput.KEY_SPACE, False)
        elif char == '\n':
            _send_virtual_key(uinput.KEY_ENTER, True)
            time.sleep(0.01)
            _send_virtual_key(uinput.KEY_ENTER, False)
        elif char == '\t':
            _send_virtual_key(uinput.KEY_TAB, True)
            time.sleep(0.01)
            _send_virtual_key(uinput.KEY_TAB, False)
        elif char == '\b':
            _send_virtual_key(uinput.KEY_BACKSPACE, True)
            time.sleep(0.01)
            _send_virtual_key(uinput.KEY_BACKSPACE, False)
        time.sleep(0.005)  # faster typing while safe


def _send_xdotool_key(key):
    if SUBPROCESS_AVAILABLE and platform.system() == 'Linux':
        try:
            subprocess.run(['xdotool', 'key', key], check=True)
        except Exception as e:
            print(f"xdotool key failed: {e}")

def _send_xdotool_text(text):
    if SUBPROCESS_AVAILABLE and platform.system() == 'Linux':
        try:
            subprocess.run(['xdotool', 'type', '--clearmodifiers', text], check=True)
        except Exception as e:
            print(f"xdotool text failed: {e}")

def _send_fallback_key(key):
    try:
        pyautogui.press(key)
    except Exception as e:
        print(f"Fallback key failed: {e}")

def _send_fallback_text(text):
    try:
        pyautogui.typewrite(text, interval=0.02)
    except Exception as e:
        print(f"Fallback text failed: {e}")

def _prompt_add_ip(ip):
    if ip in _pending_ips: return
    _pending_ips.add(ip)
    def ask():
        try:
            count = _reject_counts[ip] + 1
            print(f"\n{'═'*50}\n  🔔 New connection request from: {ip}  (attempt {count}/3)")
            print("  Add to whitelist? (y/n): ", end="", flush=True)
            if input().strip().lower() == 'y':
                with _sec_lock:
                    if ip not in security["whitelist"]:
                        security["whitelist"].append(ip)
                    _reject_counts[ip] = 0
                    _save_security()
                print(f"  ✅ Added {ip}")
            else:
                _reject_counts[ip] += 1
                if _reject_counts[ip] >= 3:
                    with _sec_lock:
                        if ip not in security["blacklist"]:
                            security["blacklist"].append(ip)
                            _save_security()
                    print(f"  ⛔ {ip} added to blacklist after 3 rejections")
                else:
                    remaining = 3 - _reject_counts[ip]
                    print(f"  ✗ Rejected {ip} — {remaining} attempt(s) remaining before blacklist")
        except Exception as e:
            print(f"  ⚠ Error: {e}")
        finally:
            _pending_ips.discard(ip)
        print('═'*50)
    threading.Thread(target=ask, daemon=True).start()

@app.before_request
def _check_request():
    ip = request.remote_addr
    if _is_rate_limited(ip):
        return jsonify({"error": "rate limited"}), 429

    if ip in ('127.0.0.1', '::1', 'localhost'):
        return

    # blacklisted → permanent reject, no prompt
    if ip in security.get("blacklist", []):
        return jsonify({"error": "blacklisted"}), 403

    # self-removal route: allowed if IP is in whitelist
    if request.path == '/security/whitelist/remove_self':
        return

    # request-approval route: allowed always so client can ask
    if request.path == '/security/whitelist/request':
        return

    if not _is_allowed(ip):
        _prompt_add_ip(ip)
        return jsonify({"error": "not whitelisted"}), 403

def ws_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        try:
            if not _is_allowed(request.remote_addr): return
        except: pass
        return f(*args, **kwargs)
    return wrapper

# ── CoreTemp ───────────────────────────────────────────────────────────────────
class _CoreTempData(ctypes.Structure):
    _fields_ = [
        ("uiLoad",         ctypes.c_uint  * 256),
        ("uiTjMax",        ctypes.c_uint  * 128),
        ("uiCoreCnt",      ctypes.c_uint),
        ("uiCPUCnt",       ctypes.c_uint),
        ("fTemp",          ctypes.c_float * 256),
        ("fVID",           ctypes.c_float),
        ("fCPUSpeed",      ctypes.c_float),
        ("fFSBSpeed",      ctypes.c_float),
        ("fMultiplier",    ctypes.c_float),
        ("sCPUName",       ctypes.c_char  * 100),
        ("ucFahrenheit",   ctypes.c_ubyte),
        ("ucDeltaToTjMax", ctypes.c_ubyte),
    ]

def _get_coretemp():
    system = platform.system()

    if system == 'Windows':
        if not hasattr(ctypes, 'windll'): return None, None
        try:
            k32  = ctypes.windll.kernel32
            hmap = k32.OpenFileMappingW(0x0004, False, "CoreTempMappingObject")
            if not hmap: return None, None
            k32.MapViewOfFile.restype = ctypes.POINTER(_CoreTempData)
            ptr = k32.MapViewOfFile(hmap, 0x0004, 0, 0, ctypes.sizeof(_CoreTempData))
            if not ptr:
                k32.CloseHandle(hmap)
                return None, None
            try:
                d     = ptr.contents
                temps = [d.fTemp[i] for i in range(d.uiCoreCnt)]
                if d.ucDeltaToTjMax: temps = [d.uiTjMax[i] - temps[i] for i in range(d.uiCoreCnt)]
                if d.ucFahrenheit:   temps = [(t - 32) * 5/9 for t in temps]
                return (round(max(temps), 1) if temps else None), None
            finally:
                k32.UnmapViewOfFile(ptr)
                k32.CloseHandle(hmap)
        except:
            return None, None

    elif system == 'Linux':
        try:
            import psutil
            temps = psutil.sensors_temperatures()
            for key in ('coretemp', 'cpu_thermal', 'k10temp', 'zenpower'):
                if key in temps and temps[key]:
                    vals = [t.current for t in temps[key]]
                    return round(max(vals), 1), None
        except: pass
        try:
            import glob
            paths = glob.glob('/sys/class/thermal/thermal_zone*/temp')
            vals = []
            for p in paths:
                with open(p) as f:
                    vals.append(int(f.read().strip()) / 1000.0)
            if vals: return round(max(vals), 1), None
        except: pass
        return None, None

    elif system == 'Darwin':
        try:
            import subprocess
            out = subprocess.check_output(
                ['sudo', 'powermetrics', '--samplers', 'smc', '-n', '1', '-i', '1'],
                timeout=2, stderr=subprocess.DEVNULL
            ).decode()
            import re
            m = re.search(r'CPU die temperature: ([\d.]+)', out)
            if m: return round(float(m.group(1)), 1), None
        except: pass
        return None, None

    return None, None

def get_system_stats():
    stats = {"cpu_temp": "N/A", "gpu_temp": "N/A", "cpu_usage": 0, "ram_usage": 0}
    try:
        import psutil
        stats["cpu_usage"] = round(psutil.cpu_percent(interval=0.1), 1)
        stats["ram_usage"] = round(psutil.virtual_memory().percent, 1)
    except: pass
    cpu_t, gpu_t = _get_coretemp()
    if cpu_t: stats["cpu_temp"] = cpu_t
    if gpu_t: stats["gpu_temp"] = gpu_t
    return stats

# ── Key Mapping ────────────────────────────────────────────────────────────────
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

def map_key(k): return KEY_MAP.get(k.lower(), k.lower())

def type_text(text):
    if not text: return
    with _pyautogui_lock:
        try:
            import pyperclip
            pyperclip.copy(text)
            time.sleep(0.08)
            if platform.system() == 'Darwin':
                pyautogui.hotkey('command', 'v')
            else:
                pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.05)
        except Exception:
            try: pyautogui.write(text, interval=0.02)
            except Exception as e: print(f"❌ type_text: {e}")

# ── Screen Streaming ───────────────────────────────────────────────────────────
screen_streaming = False
screen_thread    = None
_screen_last_error = ''

stream_config = {
    'height': 720,
    'quality': 65,
    'fps': 30,
    'target_fps': 30,
    'monitor': 1,
    'cursor_color_bgr': (255, 255, 255)
}
_stream_config_lock = threading.Lock()

# ── Mouse position cache (updated ~60hz by background thread) ──────────────────
_mouse_pos      = (0, 0)
_mouse_pos_lock = threading.Lock()

def _mouse_tracker():
    global _mouse_pos
    while True:
        try:
            p = pyautogui.position()
            with _mouse_pos_lock:
                _mouse_pos = (p.x, p.y)
        except Exception:
            pass
        time.sleep(0.016)

threading.Thread(target=_mouse_tracker, daemon=True).start()

def screen_worker():
    global screen_streaming, _screen_last_error
    _screen_last_error = ''
    tj            = None
    use_turbo     = False
    pil_img_class = None

    try:
        from turbojpeg import TurboJPEG, TJPF_BGR, TJSAMP_444 as _TJSAMP_444
        tj, use_turbo = TurboJPEG(), True
        _TJPF_BGR = TJPF_BGR
        print("✅ screen: TurboJPEG active")
    except Exception as _te:
        print(f"⚠ screen: TurboJPEG not available ({_te}), falling back to cv2")

    if not use_turbo:
        try:
            from PIL import Image as _PIL
            pil_img_class = _PIL
        except: pass

    if not MSS_AVAILABLE:
        _screen_last_error = 'mss not available'
        return

    if not CV2_AVAILABLE and not use_turbo:
        try:
            from PIL import Image as _PIL2
        except ImportError:
            _screen_last_error = 'cv2/PIL not available'
            return

    # maxsize=1 → always encode freshest frame, zero backlog
    _pipe = _queue.Queue(maxsize=1)

    fps_frames = 0
    fps_t      = time.perf_counter()

    def _encode_emit():
        nonlocal fps_frames, fps_t
        while screen_streaming:
            try:
                item = _pipe.get(timeout=0.1)
                if item is None:
                    break
                arr, pil_obj, cfg_snap = item

                quality = cfg_snap.get('quality', 65)

                if arr is not None:
                    if use_turbo:
                        buf = tj.encode(arr, quality=quality, jpeg_subsample=_TJSAMP_444, pixel_format=_TJPF_BGR)
                    elif CV2_AVAILABLE:
                        _, enc = cv2.imencode('.jpg', arr, [cv2.IMWRITE_JPEG_QUALITY, quality])
                        buf = enc.tobytes()
                    elif pil_img_class:
                        bio = io.BytesIO()
                        pil_img_class.fromarray(arr[:, :, ::-1]).save(bio, format="JPEG", quality=quality, subsampling=0)
                        buf = bio.getvalue()
                    else:
                        continue
                else:
                    bio = io.BytesIO()
                    pil_obj.save(bio, format="JPEG", quality=quality, subsampling=0)
                    buf = bio.getvalue()

                socketio.emit('frame', {'data': buf, 'size': len(buf)})

                fps_frames += 1
                now = time.perf_counter()
                if now - fps_t >= 1.0:
                    real_fps = fps_frames / (now - fps_t)
                    socketio.emit('fps_update', {'fps': round(real_fps, 1)})
                    fps_frames = 0
                    fps_t = now

            except _queue.Empty:
                continue
            except Exception as e:
                print(f"❌ encode_emit: {e}")

    emit_thread = threading.Thread(target=_encode_emit, daemon=True)
    emit_thread.start()

    with app.app_context():
        try:
            with _mss.mss() as sct:
                while screen_streaming:
                    try:
                        t0 = time.perf_counter()
                        with _stream_config_lock: cfg = stream_config.copy()
                        mon_idx      = min(cfg.get('monitor', 1), len(sct.monitors) - 1)
                        mon          = sct.monitors[max(1, mon_idx)]
                        target_h     = cfg['height']
                        fps          = max(1, cfg['fps'])
                        frame_budget = 1.0 / fps

                        img = sct.grab(mon)

                        if CV2_AVAILABLE:
                            arr = np.frombuffer(img.raw, dtype=np.uint8).reshape((img.height, img.width, 4))[:, :, :3]
                            h, w = arr.shape[:2]
                            nw, nh = int(w * target_h / h), target_h
                            if h != nh:
                                interp = cv2.INTER_AREA if nh < h else cv2.INTER_LINEAR
                                arr = cv2.resize(arr, (nw, nh), interpolation=interp)
                            else:
                                arr = np.ascontiguousarray(arr)
                            with _mouse_pos_lock:
                                mx, my = _mouse_pos
                            sx = int((mx - mon['left']) * nw / w)
                            sy = int((my - mon['top'])  * nh / h)
                            if 0 <= sx < nw and 0 <= sy < nh:
                                cursor_color = cfg.get('cursor_color_bgr', (255, 255, 255))
                                pts = np.array([[sx, sy], [sx+12, sy+12], [sx, sy+16]], np.int32)
                                cv2.fillPoly(arr, [pts], cursor_color)
                                cv2.polylines(arr, [pts], True, (0, 0, 0), 1)
                            pil_obj = None
                        else:
                            from PIL import Image as _PIL2
                            pil_obj = _PIL2.frombytes('RGB', (img.width, img.height), img.rgb)
                            h, w = img.height, img.width
                            nw, nh = int(w * target_h / h), target_h
                            if h != nh:
                                pil_obj = pil_obj.resize((nw, nh), resample=_PIL2.LANCZOS)
                            arr = None

                        # replace stale frame instead of dropping new one
                        if _pipe.full():
                            try: _pipe.get_nowait()
                            except: pass
                        try: _pipe.put_nowait((arr, pil_obj, cfg))
                        except: pass

                        elapsed = time.perf_counter() - t0
                        sleep_t = frame_budget - elapsed
                        if sleep_t > 0.001:
                            time.sleep(sleep_t)

                    except Exception as e:
                        _screen_last_error = str(e)
                        print(f"❌ frame: {e}"); time.sleep(0.1)
        except Exception as e:
            _screen_last_error = str(e)
            print(f"❌ screen_worker: {e}")
        finally:
            try: _pipe.put_nowait(None)
            except: pass
            emit_thread.join(timeout=2)


_mic_queue   = _queue.Queue(maxsize=40)
_mic_active  = False
_mic_worker_thread = None

@socketio.on('disconnect')
def on_disconnect(reason=None):
    global screen_streaming, audio_streaming, _mic_active
    screen_streaming = False
    audio_streaming  = False
    _mic_active      = False
    try: _mic_queue.put_nowait(None)
    except: pass

@socketio.on('screen_start')
@ws_required
def on_screen_start(d):
    global screen_streaming, screen_thread
    screen_streaming = False
    time.sleep(0.1)
    screen_streaming = True
    screen_thread = threading.Thread(target=screen_worker, daemon=True)
    screen_thread.start()

@socketio.on('screen_stop')
@ws_required
def on_screen_stop(d):
    global screen_streaming
    screen_streaming = False

@socketio.on('stream_config')
@ws_required
def on_stream_config(d):
    with _stream_config_lock:
        if 'height'       in d: stream_config['height']          = int(d['height'])
        if 'quality'      in d: stream_config['quality']         = max(10, min(100, int(d['quality'])))
        if 'fps'          in d: stream_config['fps']             = max(1, min(60, int(d['fps'])))
        if 'monitor'      in d: stream_config['monitor']         = max(1, int(d['monitor']))
        if 'cursor_color' in d:
            hex_c = d['cursor_color'].lstrip('#')
            r, g, b = int(hex_c[0:2],16), int(hex_c[2:4],16), int(hex_c[4:6],16)
            stream_config['cursor_color_bgr'] = (b, g, r)

# ── Event Log ──────────────────────────────────────────────────────────────────
LOG_FILE   = os.path.join(BASE_DIR, "portdesk_events.log")
_log_lock  = threading.Lock()

def _log_event(event_type, detail=''):
    try:
        from flask import has_request_context
        ip = request.remote_addr if has_request_context() else 'system'
    except: ip = 'system'
    line = json.dumps({'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'type': event_type, 'ip': ip, 'detail': detail})
    with _log_lock:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    path = os.path.join(BASE_DIR, 'portdesk_client.html')
    if not os.path.isfile(path):
        return "portdesk_client.html not found — place it next to the server", 500
    _log_event('connect')
    return send_file(path)

@app.route('/security/whitelist')
def get_whitelist():
    ip = request.remote_addr
    wl = security.get("whitelist", [])
    return jsonify({"approved": ip in wl, "ip": ip})

@app.route('/security/whitelist/request', methods=['POST'])
def whitelist_request():
    ip = request.remote_addr
    if ip in security.get("blacklist", []):
        return jsonify({"error": "blacklisted"}), 403
    if ip in security.get("whitelist", []):
        return jsonify({"ok": True, "already": True})
    _prompt_add_ip(ip)
    return jsonify({"ok": True, "pending": True})

@app.route('/security/whitelist/remove_self', methods=['POST'])
def whitelist_remove_self():
    ip = request.remote_addr
    with _sec_lock:
        if ip in security.get("whitelist", []):
            security["whitelist"].remove(ip)
            _save_security()
    return jsonify({"ok": True})

@app.route('/security/blacklist/remove', methods=['POST'])
def blacklist_remove():
    # server-only: only localhost can call this, or handle via command line
    if request.remote_addr not in ('127.0.0.1', '::1', 'localhost'):
        return jsonify({"error": "forbidden"}), 403
    ip = (request.get_json() or {}).get("ip", "")
    with _sec_lock:
        if ip in security.get("blacklist", []):
            security["blacklist"].remove(ip)
        _reject_counts[ip] = 0
        _save_security()
    return jsonify({"ok": True})

@app.route('/screen/status')
def screen_status():
    return jsonify({
        'streaming': screen_streaming,
        'thread_alive': screen_thread is not None and screen_thread.is_alive(),
        'mss': MSS_AVAILABLE,
        'error': _screen_last_error,
    })

@app.route('/screen/start', methods=['POST'])
def screen_start_http():
    global screen_thread
    global screen_streaming, screen_thread
    if not screen_streaming:
        screen_streaming = True
        screen_thread = threading.Thread(target=screen_worker, daemon=True)
        screen_thread.start()
    return jsonify({'ok': True})

@app.route('/screen/stop', methods=['POST'])
def screen_stop_http():
    global screen_streaming
    screen_streaming = False
    return jsonify({'ok': True})

@app.route('/ping')
def ping():
    return jsonify({'pong': time.time()})

@app.route('/stats')
def stats():
    return jsonify(get_system_stats())

# ── Socket Handlers ────────────────────────────────────────────────────────────
@socketio.on('selector_start')
@ws_required
def on_selector_start(d):
    with _pyautogui_lock:
        pyautogui.mouseDown()

@socketio.on('selector_move')
@ws_required
def on_selector_move(d):
    with _pyautogui_lock:
        pyautogui.moveRel(int(d.get('dx',0)), int(d.get('dy',0)), duration=0)

@socketio.on('selector_end')
@ws_required
def on_selector_end(d):
    with _pyautogui_lock:
        pyautogui.mouseUp()

@socketio.on('move')
@ws_required
def on_move(d):
    with _pyautogui_lock:
        pyautogui.moveRel(int(d.get('dx',0)), int(d.get('dy',0)), duration=0)

@socketio.on('click')
@ws_required
def on_click(d):
    t = d.get('type','left')
    with _pyautogui_lock:
        if   t=='left':   pyautogui.click()
        elif t=='right':  pyautogui.rightClick()
        elif t=='double': pyautogui.doubleClick()
        elif t=='middle': pyautogui.middleClick()

@socketio.on('scroll')
@ws_required
def on_scroll(d):
    with _pyautogui_lock:
        pyautogui.scroll(int(d.get('dy',0)))

def _press_win_shortcut(keys):
    """Cross-platform Win/Cmd key shortcut"""
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
            # macOS: map winleft/winright → command key
            mac_keys = []
            for k in keys:
                if k in ('winleft', 'winright', 'command', 'cmd'):
                    mac_keys.append('command')
                else:
                    mac_keys.append(k)
            with _pyautogui_lock: pyautogui.hotkey(*mac_keys)
            return True
        else:
            with _pyautogui_lock: pyautogui.hotkey(*keys)
            return True
    except Exception as e:
        print(f"win shortcut error: {e}")
        return False

def _press_mac_shortcut(keys):
    """macOS: map cmd/command to command key"""
    mac_keys = []
    for k in keys:
        if k in ('winleft', 'winright', 'command', 'cmd', 'super'):
            mac_keys.append('command')
        else:
            mac_keys.append(k)
    with _pyautogui_lock: pyautogui.hotkey(*mac_keys)

@socketio.on('shortcut')
@ws_required
def on_shortcut(d):
    keys    = [map_key(k) for k in d.get('keys',[])]
    system  = platform.system()
    if system == 'Linux':
        keys = ['super' if k in ('winleft','winright','command','cmd') else k for k in keys]
    has_win = any(k in ('winleft','winright') for k in keys)
    has_cmd = any(k in ('command','cmd','super') for k in keys)
    try:
        if system == 'Darwin' and (has_win or has_cmd):
            _press_mac_shortcut(keys)
        elif system == 'Windows' and has_win:
            ok = _press_win_shortcut(keys)
            if not ok:
                with _pyautogui_lock: pyautogui.hotkey(*keys)
        else:
            with _pyautogui_lock: pyautogui.hotkey(*keys)
    except Exception as e:
        print(f"shortcut error: {e}")

@socketio.on('key')
@ws_required
def on_key(d):
    key = map_key(d.get('key', ''))
    system = platform.system()
    if system == 'Linux':
        if _virtual_kb_device:
            key_code = getattr(uinput, f'KEY_{key.upper()}', None)
            if key_code:
                _send_virtual_key(key_code, True)
                time.sleep(0.01)
                _send_virtual_key(key_code, False)
        elif SUBPROCESS_AVAILABLE:
            _send_xdotool_key(key)
        else:
            _send_fallback_key(key)
    else:
        try:
            with _pyautogui_lock: pyautogui.press(key)
        except Exception as e: print(f"key: {e}")

@socketio.on('type')
@ws_required
def on_type(d):
    text = d.get('text', '')
    if not text:
        return
    system = platform.system()
    if system == 'Linux':
        if _virtual_kb_device:
            _send_virtual_text(text)
        elif SUBPROCESS_AVAILABLE:
            _send_xdotool_text(text)
        else:
            _send_fallback_text(text)
    else:
        type_text(text)

@socketio.on('key_down')
@ws_required
def on_key_down(d):
    try:
        with _pyautogui_lock: pyautogui.keyDown(map_key(d.get('key','')))
    except Exception as e: print(f"key_down: {e}")

@socketio.on('key_up')
@ws_required
def on_key_up(d):
    try:
        with _pyautogui_lock: pyautogui.keyUp(map_key(d.get('key','')))
    except Exception as e: print(f"key_up: {e}")

# ── File Transfer ──────────────────────────────────────────────────────────────
# ── Clipboard Sync (background only) ──────────────────────────────────────────
_last_clip    = ""
_clip_lock    = threading.Lock()
_clip_running = False

def _clipboard_watcher():
    global _last_clip, _clip_running
    try:
        import pyperclip
    except ImportError:
        print("⚠ pyperclip not available — clipboard sync disabled"); return
    while _clip_running:
        try:
            current = pyperclip.paste()
            with _clip_lock:
                if current and current != _last_clip:
                    _last_clip = current
                    socketio.emit('clipboard_update', {'text': current})
        except: pass
        time.sleep(2)

# ── File Explorer ──────────────────────────────────────────────────────────────
import zipfile

def _list_drives():
    if platform.system() == 'Windows':
        return [d+':\\' for d in _string.ascii_uppercase if os.path.exists(d+':\\')]
    elif platform.system() == 'Darwin':
        vols = ['/Volumes/' + v for v in os.listdir('/Volumes')] if os.path.exists('/Volumes') else []
        return ['/'] + vols
    else:
        return ['/home', '/tmp', '/']

@app.route('/explorer/drives')
def explorer_drives():
    return jsonify(_list_drives())

@app.route('/explorer/list')
def explorer_list():
    path = request.args.get('path', '')
    if not path:
        return jsonify({'drives': _list_drives()})
    if not os.path.exists(path):
        return jsonify({'error': 'Path not found'}), 404
    try:
        entries = []
        for name in sorted(os.listdir(path), key=lambda x: (not os.path.isdir(os.path.join(path,x)), x.lower())):
            full = os.path.join(path, name)
            try:
                stat = os.stat(full)
                if os.path.isdir(full):
                    try:
                        dir_size = sum(
                            os.path.getsize(os.path.join(r, f))
                            for r, _, files in os.walk(full)
                            for f in files
                        )
                    except Exception:
                        dir_size = 0
                    entries.append({
                        'name': name, 'type': 'dir',
                        'size': dir_size, 'modified': int(stat.st_mtime)
                    })
                else:
                    entries.append({
                        'name': name, 'type': 'file',
                        'size': stat.st_size, 'modified': int(stat.st_mtime)
                    })
            except PermissionError:
                entries.append({'name': name, 'type': 'dir' if os.path.isdir(full) else 'file', 'size': 0, 'modified': 0, 'denied': True})
        return jsonify({'path': path, 'entries': entries})
    except PermissionError:
        return jsonify({'error': 'Permission denied'}), 403
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/explorer/download')
def explorer_download():
    path = request.args.get('path', '')
    if not path or not os.path.exists(path):
        return jsonify({'error': 'not found'}), 404
    if os.path.isfile(path):
        return send_file(path, as_attachment=True)
    # folder → zip on the fly
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(path):
            for fname in files:
                full = os.path.join(root, fname)
                try: zf.write(full, os.path.relpath(full, os.path.dirname(path)))
                except: pass
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=os.path.basename(path)+'.zip', mimetype='application/zip')

@app.route('/explorer/download_multi', methods=['POST'])
def explorer_download_multi():
    paths = (request.get_json() or {}).get('paths', [])
    if not paths: return jsonify({'error': 'no paths'}), 400
    if len(paths) == 1 and os.path.isfile(paths[0]):
        return send_file(paths[0], as_attachment=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            if not os.path.exists(p): continue
            if os.path.isfile(p):
                try: zf.write(p, os.path.basename(p))
                except: pass
            else:
                for root, _, files in os.walk(p):
                    for fname in files:
                        full = os.path.join(root, fname)
                        try: zf.write(full, os.path.relpath(full, p))
                        except: pass
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name='pcc_files.zip', mimetype='application/zip')

@app.route('/explorer/upload', methods=['POST'])
def explorer_upload():
    dest_dir = request.form.get('path', os.path.join(os.path.expanduser('~'), 'Downloads'))
    if not os.path.isdir(dest_dir):
        return jsonify({'error': 'Folder not found'}), 400
    saved = []
    for f in request.files.getlist('files'):
        safe = os.path.basename(f.filename)
        if not safe: continue
        dest = os.path.join(dest_dir, safe)
        base, ext = os.path.splitext(safe)
        c = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}_{c}{ext}"); c += 1
        f.save(dest); saved.append(safe)
    return jsonify({'ok': True, 'saved': saved})

@app.route('/explorer/mkdir', methods=['POST'])
def explorer_mkdir():
    d    = request.get_json() or {}
    path = d.get('path','').strip()
    name = d.get('name','').strip()
    if not path or not name: return jsonify({'error': 'missing params'}), 400
    target = os.path.join(path, name)
    try:
        os.makedirs(target, exist_ok=False)
        return jsonify({'ok': True})
    except FileExistsError: return jsonify({'error': 'Name already exists'}), 409
    except Exception as e:  return jsonify({'error': str(e)}), 500

@app.route('/explorer/mkfile', methods=['POST'])
def explorer_mkfile():
    d    = request.get_json() or {}
    path = d.get('path','').strip()
    name = d.get('name','').strip()
    if not path or not name: return jsonify({'error': 'missing params'}), 400
    target = os.path.join(path, name)
    if os.path.exists(target): return jsonify({'error': 'Name already exists'}), 409
    try:
        open(target, 'w').close()
        return jsonify({'ok': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/explorer/rename', methods=['POST'])
def explorer_rename():
    d   = request.get_json() or {}
    src = d.get('src','').strip()
    new_name = d.get('name','').strip()
    if not src or not new_name: return jsonify({'error': 'missing params'}), 400
    dst = os.path.join(os.path.dirname(src), new_name)
    if os.path.exists(dst): return jsonify({'error': 'Name already exists'}), 409
    try:
        os.rename(src, dst)
        return jsonify({'ok': True})
    except Exception as e: return jsonify({'error': str(e)}), 500

@app.route('/explorer/delete', methods=['POST'])
def explorer_delete():
    import shutil as _shutil
    paths = (request.get_json() or {}).get('paths', [])
    errors = []
    for p in paths:
        if not os.path.exists(p): continue
        try:
            if os.path.isdir(p): _shutil.rmtree(p)
            else: os.remove(p)
        except Exception as e: errors.append(str(e))
    return jsonify({'ok': not errors, 'errors': errors})

@app.route('/explorer/copy', methods=['POST'])
def explorer_copy():
    import shutil as _shutil
    d    = request.get_json() or {}
    srcs = d.get('paths', [])
    dst  = d.get('dest', '').strip()
    if not srcs or not dst: return jsonify({'error': 'missing params'}), 400
    if not os.path.isdir(dst): return jsonify({'error': 'Destination not found'}), 400
    errors = []
    for s in srcs:
        try:
            name = os.path.basename(s.rstrip('/\\'))
            t    = os.path.join(dst, name)
            if os.path.isdir(s): _shutil.copytree(s, t)
            else:                _shutil.copy2(s, t)
        except Exception as e: errors.append(str(e))
    return jsonify({'ok': not errors, 'errors': errors})

@app.route('/explorer/move', methods=['POST'])
def explorer_move():
    import shutil as _shutil
    d    = request.get_json() or {}
    srcs = d.get('paths', [])
    dst  = d.get('dest', '').strip()
    if not srcs or not dst: return jsonify({'error': 'missing params'}), 400
    if not os.path.isdir(dst): return jsonify({'error': 'Destination not found'}), 400
    errors = []
    for s in srcs:
        try: _shutil.move(s, os.path.join(dst, os.path.basename(s.rstrip('/\\'))))
        except Exception as e: errors.append(str(e))
    return jsonify({'ok': not errors, 'errors': errors})

@app.route('/explorer/shortcut', methods=['POST'])
def explorer_shortcut():
    d    = request.get_json() or {}
    src  = d.get('src','').strip()
    dest = d.get('dest','').strip()
    if not src or not dest: return jsonify({'error': 'missing params'}), 400
    try:
        if platform.system() == 'Windows':
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            lnk_name = os.path.splitext(os.path.basename(src))[0] + '.lnk'
            lnk = shell.CreateShortCut(os.path.join(dest, lnk_name))
            lnk.Targetpath = src
            lnk.save()
        elif platform.system() == 'Darwin':
            import subprocess
            subprocess.run(['ln', '-s', src, os.path.join(dest, os.path.basename(src))], check=True)
        else:
            # Linux: create .desktop file
            name = os.path.splitext(os.path.basename(src))[0]
            desktop = os.path.join(dest, name + '.desktop')
            with open(desktop, 'w') as f:
                f.write(f'[Desktop Entry]\nType=Link\nName={name}\nURL=file://{src}\nIcon=applications-system\n')
            os.chmod(desktop, 0o755)
            # Optional xdg install for desktop environments
            try:
                import subprocess
                subprocess.run(['xdg-desktop-icon', 'install', '--novendor', desktop], check=False)
            except Exception:
                pass
        return jsonify({'ok': True})
    except Exception as e:
        logging.exception("Failed to create explorer shortcut")
        return jsonify({'error': 'internal error'}), 500

@app.route('/explorer/properties')
def explorer_properties():
    raw_path = request.args.get('path', '').strip()
    if not raw_path:
        return jsonify({'error': 'not found'}), 404

    # Prevent directory traversal by resolving the absolute path
    # and ensuring it doesn't escape the allowed directories
    try:
        # Get the absolute path and resolve any symlinks
        fullpath = os.path.abspath(raw_path)

        # For security, we should restrict access to certain directories
        # but since this is a file explorer, we'll allow access to the entire filesystem
        # but prevent access to sensitive system directories
        sensitive_dirs = ['/proc', '/sys', '/dev', 'C:\\Windows\\System32', 'C:\\Windows']
        for sensitive in sensitive_dirs:
            if fullpath.startswith(os.path.abspath(sensitive)):
                return jsonify({'error': 'access denied'}), 403

        if not os.path.exists(fullpath):
            return jsonify({'error': 'not found'}), 404

        stat = os.stat(fullpath)
        info = {
            'name':     os.path.basename(fullpath),
            'path':     fullpath,
            'type':     'folder' if os.path.isdir(fullpath) else 'file',
            'size':     stat.st_size,
            'modified': int(stat.st_mtime),
            'created':  int(stat.st_ctime),
        }
        if os.path.isdir(fullpath):
            try:
                total = sum(
                    os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(fullpath)
                    for f in fs
                    if os.path.exists(os.path.join(r, f))
                )
                info['size'] = total
            except (OSError, PermissionError):
                info['size'] = 0
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Macro Recorder ─────────────────────────────────────────────────────────────
MACROS_FILE    = os.path.join(BASE_DIR, "portdesk_macros.json")
_macro_lock    = threading.Lock()

def _load_macros():
    try:
        with open(MACROS_FILE) as f: return json.load(f)
    except: return {}

def _save_macros(macros):
    with open(MACROS_FILE, 'w') as f: json.dump(macros, f, indent=2)

macros = _load_macros()

@app.route('/macros/list')
def macros_list():
    with _macro_lock: return jsonify(list(macros.keys()))

@app.route('/macros/save', methods=['POST'])
def macros_save():
    d = request.get_json() or {}
    name, steps = d.get('name',''), d.get('steps',[])
    if not name: return jsonify({'error': 'no name'}), 400
    with _macro_lock:
        macros[name] = steps
        _save_macros(macros)
    return jsonify({'ok': True})

@app.route('/macros/delete', methods=['POST'])
def macros_delete():
    name = (request.get_json() or {}).get('name','')
    with _macro_lock:
        if name in macros:
            del macros[name]
            _save_macros(macros)
    return jsonify({'ok': True})

@app.route('/macros/run', methods=['POST'])
def macros_run():
    name = (request.get_json() or {}).get('name','')
    with _macro_lock: steps = macros.get(name, [])
    if not steps: return jsonify({'error': 'not found'}), 404
    def _run():
        for step in steps:
            t = step.get('type','')
            try:
                with _pyautogui_lock:
                    if   t == 'key':      pyautogui.press(map_key(step['key']))
                    elif t == 'shortcut': pyautogui.hotkey(*[map_key(k) for k in step['keys']])
                    elif t == 'type':     type_text(step['text'])
                    elif t == 'click':
                        bt = step.get('btn','left')
                        if   bt == 'left':   pyautogui.click()
                        elif bt == 'right':  pyautogui.rightClick()
                        elif bt == 'double': pyautogui.doubleClick()
                    elif t == 'scroll':   pyautogui.scroll(int(step.get('dy',0)))
                    elif t == 'move':     pyautogui.moveRel(int(step.get('dx',0)), int(step.get('dy',0)), duration=0)
                delay = step.get('delay', 0.1)
                if delay > 0: time.sleep(delay)
            except Exception as e:
                print(f"macro step error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True})

# ── Multiple Monitors ──────────────────────────────────────────────────────────
@app.route('/monitors/list')
def monitors_list():
    if not MSS_AVAILABLE: return jsonify([])
    try:
        with _mss.mss() as sct:
            mons = [{'index': i, 'w': m['width'], 'h': m['height'],
                     'x': m['left'], 'y': m['top']}
                    for i, m in enumerate(sct.monitors) if i > 0]
        return jsonify(mons)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@socketio.on('set_monitor')
@ws_required
def on_set_monitor(d):
    stream_config['monitor'] = max(1, int(d.get('index', 1)))

# ── Task Manager ───────────────────────────────────────────────────────────────
@app.route('/tasks/list')
def tasks_list():
    try:
        import psutil
        procs_raw = []
        for p in psutil.process_iter(['pid','name','memory_info','status']):
            try:
                p.cpu_percent(interval=None)
                procs_raw.append(p)
            except: pass
        time.sleep(0.2)
        procs = []
        for p in procs_raw:
            try:
                cpu = round(p.cpu_percent(interval=None) or 0, 1)
                mi  = p.info.get('memory_info')
                procs.append({
                    'pid':    p.pid,
                    'name':   p.name(),
                    'cpu':    cpu,
                    'mem':    mi.rss if mi else 0,
                    'status': p.status(),
                })
            except: pass
        procs.sort(key=lambda x: x['cpu'], reverse=True)
        return jsonify(procs[:80])
    except ImportError:
        return jsonify({'error': 'psutil not installed'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/tasks/kill', methods=['POST'])
def tasks_kill():
    pid = (request.get_json() or {}).get('pid')
    if not pid: return jsonify({'error': 'no pid'}), 400
    try:
        import psutil
        psutil.Process(int(pid)).terminate()
        _log_event('task_kill', f'pid={pid}')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/log/list')
def log_list():
    try:
        if not os.path.exists(LOG_FILE): return jsonify([])
        with _log_lock, open(LOG_FILE, encoding='utf-8') as f:
            lines = f.readlines()
        events = []
        for l in reversed(lines[-200:]):
            try: events.append(json.loads(l.strip()))
            except: pass
        return jsonify(events)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/log/clear', methods=['POST'])
def log_clear():
    with _log_lock:
        open(LOG_FILE, 'w').close()
    return jsonify({'ok': True})

# ── Remote Audio ───────────────────────────────────────────────────────────────
audio_streaming  = False
_audio_thread    = None
_AUDIO_CHUNK     = 4096
_AUDIO_RATE      = 22050

def _audio_worker():
    global audio_streaming
    try:
        import sounddevice as sd
        device_idx = None
        for i, dev in enumerate(sd.query_devices()):
            name = dev['name'].lower()
            if 'cable' in name and dev['max_input_channels'] > 0:
                device_idx = i
                break
        if device_idx is None:
            print("❌ audio: CABLE Output not found — start VB-Audio")
            audio_streaming = False
            return
        with sd.InputStream(samplerate=_AUDIO_RATE, channels=1, dtype='int16',
                            blocksize=_AUDIO_CHUNK, device=device_idx) as stream:
            while audio_streaming:
                data, _ = stream.read(_AUDIO_CHUNK)
                encoded = base64.b64encode(data.tobytes()).decode()
                socketio.emit('audio_chunk', {'data': encoded})
                time.sleep(0)
    except Exception as e:
        print(f"❌ audio_worker: {e}")
        audio_streaming = False

@app.route('/audio/start', methods=['POST'])
def audio_start_http():
    global audio_streaming, _audio_thread
    if audio_streaming: return jsonify({'ok': True})
    audio_streaming = True
    _audio_thread = threading.Thread(target=_audio_worker, daemon=True)
    _audio_thread.start()
    _log_event('audio_start')
    return jsonify({'ok': True})

@app.route('/audio/stop', methods=['POST'])
def audio_stop_http():
    global audio_streaming
    audio_streaming = False
    _log_event('audio_stop')
    return jsonify({'ok': True})

@socketio.on('audio_start')
@ws_required
def on_audio_start(d):
    global audio_streaming, _audio_thread
    if _audio_thread and _audio_thread.is_alive(): return
    audio_streaming = True
    _audio_thread = threading.Thread(target=_audio_worker, daemon=True)
    _audio_thread.start()
    _log_event('audio_start')

@socketio.on('audio_stop')
@ws_required
def on_audio_stop(d):
    global audio_streaming
    audio_streaming = False
    _log_event('audio_stop')

# ── Brute Force Protection ─────────────────────────────────────────────────────
_pin_fails      = defaultdict(int)
_pin_lockout    = {}
_pin_lockout_count = defaultdict(int)
PIN_MAX_TRIES   = 5
PIN_LOCKOUT_STEPS = [60, 180, 300]

@app.route('/auth/pin_check', methods=['POST'])
def auth_pin_check():
    ip  = request.remote_addr
    now = time.time()
    if ip in _pin_lockout and now < _pin_lockout[ip]:
        rem = int(_pin_lockout[ip] - now)
        return jsonify({'error': f'Blocked. Wait {rem} seconds'}), 429
    d    = request.get_json() or {}
    ok   = d.get('ok', False)
    if ok:
        _pin_fails[ip] = 0
        _pin_lockout.pop(ip, None)
        _log_event('pin_success')
        return jsonify({'ok': True})
    _pin_fails[ip] += 1
    _log_event('pin_fail', f'attempt={_pin_fails[ip]}')
    if _pin_fails[ip] >= PIN_MAX_TRIES:
        step     = _pin_lockout_count[ip]
        duration = PIN_LOCKOUT_STEPS[min(step, len(PIN_LOCKOUT_STEPS) - 1)]
        _pin_lockout_count[ip] += 1
        _pin_lockout[ip] = now + duration
        _pin_fails[ip]   = 0
        return jsonify({'error': f'Locked for {duration} seconds due to multiple failed attempts'}), 429
    return jsonify({'ok': False, 'remaining': PIN_MAX_TRIES - _pin_fails[ip]})

# ── Scheduled Tasks ────────────────────────────────────────────────────────────
SCHED_FILE   = os.path.join(BASE_DIR, "portdesk_scheduled.json")
_sched_lock  = threading.Lock()
_sched_thread = None
_sched_running = False

def _load_scheduled():
    try:
        with open(SCHED_FILE) as f: return json.load(f)
    except: return []

def _save_scheduled(tasks):
    with open(SCHED_FILE, 'w') as f: json.dump(tasks, f, indent=2)

scheduled_tasks = _load_scheduled()

def _scheduler_worker():
    global _sched_running
    while _sched_running:
        now = time.strftime('%H:%M')
        with _sched_lock:
            for task in scheduled_tasks:
                if not task.get('enabled', True): continue
                if task.get('time') == now and task.get('last_run') != now:
                    task['last_run'] = now
                    _save_scheduled(scheduled_tasks)
                    macro_name = task.get('macro')
                    with _macro_lock:
                        steps = macros.get(macro_name, [])
                    if steps:
                        def _run(s=steps):
                            for step in s:
                                t = step.get('type','')
                                try:
                                    with _pyautogui_lock:
                                        if   t=='key':      pyautogui.press(map_key(step['key']))
                                        elif t=='shortcut': pyautogui.hotkey(*[map_key(k) for k in step['keys']])
                                        elif t=='type':     type_text(step['text'])
                                        elif t=='click':
                                            bt = step.get('btn','left')
                                            if   bt=='left':   pyautogui.click()
                                            elif bt=='right':  pyautogui.rightClick()
                                            elif bt=='double': pyautogui.doubleClick()
                                        elif t=='scroll':   pyautogui.scroll(int(step.get('dy',0)))
                                    delay = step.get('delay', 0.1)
                                    if delay > 0: time.sleep(delay)
                                except Exception as e: print(f"sched step: {e}")
                        threading.Thread(target=_run, daemon=True).start()
                        _log_event('sched_run', macro_name)
        time.sleep(10)

@app.route('/scheduled/list')
def scheduled_list():
    with _sched_lock: return jsonify(scheduled_tasks)

@app.route('/scheduled/save', methods=['POST'])
def scheduled_save():
    d = request.get_json() or {}
    task = {'id': str(int(time.time())), 'name': d.get('name',''), 'time': d.get('time',''),
            'macro': d.get('macro',''), 'enabled': True, 'last_run': ''}
    with _sched_lock:
        scheduled_tasks.append(task)
        _save_scheduled(scheduled_tasks)
    return jsonify({'ok': True})

@app.route('/scheduled/delete', methods=['POST'])
def scheduled_delete():
    tid = (request.get_json() or {}).get('id','')
    with _sched_lock:
        scheduled_tasks[:] = [t for t in scheduled_tasks if t.get('id') != tid]
        _save_scheduled(scheduled_tasks)
    return jsonify({'ok': True})

@app.route('/scheduled/toggle', methods=['POST'])
def scheduled_toggle():
    d   = request.get_json() or {}
    tid = d.get('id','')
    with _sched_lock:
        for t in scheduled_tasks:
            if t.get('id') == tid:
                t['enabled'] = not t.get('enabled', True); break
        _save_scheduled(scheduled_tasks)
    return jsonify({'ok': True})

# ── Entry Point ────────────────────────────────────────────────────────────────
# ── Microphone (Mobile → PC Virtual Mic via VB-Audio) ─────────────────────────

def _mic_worker():
    global _mic_active
    try:
        import sounddevice as sd
        device_idx = None
        for i, dev in enumerate(sd.query_devices()):
            name = dev['name'].lower()
            if 'cable' in name and dev['max_output_channels'] > 0:
                device_idx = i
                break
        if device_idx is None:
            print("❌ mic: CABLE Input not found — start VB-Audio")
            _mic_active = False
            return
        stream = sd.RawOutputStream(samplerate=44100, channels=1, dtype='int16',
                                    blocksize=2048, latency='low', device=device_idx)
        stream.start()
        while _mic_active:
            try:
                pcm = _mic_queue.get(timeout=0.5)
                if pcm is None: break
                stream.write(pcm)
            except _queue.Empty:
                continue
            except Exception as e:
                print(f"mic_worker write: {e}")
        stream.stop()
        stream.close()
    except Exception as e:
        print(f"mic_worker: {e}")

@socketio.on('mic_start')
@ws_required
def on_mic_start(d):
    global _mic_active, _mic_worker_thread
    if _mic_worker_thread and _mic_worker_thread.is_alive():
        _mic_active = False
        try: _mic_queue.put_nowait(None)
        except: pass
        _mic_worker_thread.join(timeout=1.0)
    while not _mic_queue.empty():
        try: _mic_queue.get_nowait()
        except: break
    _mic_active = True
    _mic_worker_thread = threading.Thread(target=_mic_worker, daemon=True)
    _mic_worker_thread.start()
    _log_event('mic_start')

@socketio.on('mic_stop')
@ws_required
def on_mic_stop(d):
    global _mic_active
    _mic_active = False
    try: _mic_queue.put_nowait(None)
    except: pass
    _log_event('mic_stop')

@socketio.on('mic_chunk')
@ws_required
def on_mic_chunk(d):
    if not _mic_active: return
    raw = d.get('data')
    if not raw: return
    try:
        pcm = base64.b64decode(raw)
        try: _mic_queue.put_nowait(pcm)
        except _queue.Full: pass
    except Exception as e:
        print(f"mic_chunk: {e}")

if __name__ == '__main__':
    _clip_running  = True
    _sched_running = True
    threading.Thread(target=_clipboard_watcher, daemon=True).start()
    _sched_thread = threading.Thread(target=_scheduler_worker, daemon=True)
    _sched_thread.start()

    # Linux compatibility diagnostics
    for warn in _check_linux_compatibility():
        print(f"⚠️ Linux compatibility: {warn}")

    # Initialize virtual keyboard if possible
    if _init_virtual_keyboard():
        print("✅ Virtual keyboard initialized successfully.")
    else:
        print("⚠️ Virtual keyboard not available; using fallbacks.")

    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); local_ip = s.getsockname()[0]; s.close()
    except: local_ip = '0.0.0.0'

    cert_file = os.path.join(BASE_DIR, 'cert.pem')
    key_file  = os.path.join(BASE_DIR, 'key.pem')
    use_https = os.path.isfile(cert_file) and os.path.isfile(key_file)

    print(f"\n{'═'*52}")
    print(f"  \U0001f3ae  PortDesk v1.0  \u2014  Official Release")
    print(f"{'─'*52}")
    print(f"  \u270d  Developed by  :  Lucky_abdo")
    print(f"  \U0001f517  GitHub        :  github.com/Lucky-abdo/PortDesk")
    print(f"{'─'*52}")
    print(f"  \u2139  This is the original, unmodified official build.")
    print(f"     Forks or modified copies are NOT endorsed and may")
    print(f"     lack the security and privacy guarantees of this release.")
    print(f"{'═'*52}")
    if use_https:
        print(f"  [USB]  adb reverse tcp:5000 tcp:5000 → https://localhost:5000")
        print(f"  [WiFi] https://{local_ip}:5000")
        print(f"  🔒 HTTPS enabled — microphone works over WiFi")
    else:
        print(f"  [USB]  adb reverse tcp:5000 tcp:5000 → http://localhost:5000")
        print(f"  [WiFi] http://{local_ip}:5000")
        print(f"  ⚠ HTTP only — run gen_cert.py to enable HTTPS")
    print(f"{'═'*50}\n")

    if use_https:
        ssl_ctx = __import__('ssl').SSLContext(__import__('ssl').PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_file, key_file)
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, ssl_context=ssl_ctx, allow_unsafe_werkzeug=True)
    else:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
