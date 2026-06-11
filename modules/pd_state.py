# ── pd_state — shared state managers (refactor) ─────────────────────────────
# Extracted verbatim from portdesk_server.py. Behaviour is byte-for-byte
# identical; only the file location changed. Pure module: depends on `threading`
# only (no server globals, no FLAG_*, no event loop).
#
#   _SessionManager     — single source of truth for client connection + PIN.
#   _StreamStateManager — single source of truth for all streaming state.
#
# The server imports these and keeps `_session = _SessionManager()` /
# `_stream = _StreamStateManager()` instances locally so all existing
# backward-compat aliases keep working unchanged.
import threading


class _SessionManager:
    def __init__(self) -> None:
        self._lock            = threading.Lock()
        self._ws              = None
        self._ip: str | None  = None
        self._verified: dict  = {}
        # Auto-sleep state
        self._sleeping        = False
        self._sleep_since     = 0.0  # time.time() when client went to sleep

    def try_claim(self, ws, ip: str) -> tuple[bool, str]:
        """Atomically claim active slot.
        Returns (True, '') on success, (False, 'occupied') if active client is awake,
        or (False, 'sleeping') if active client is in sleep mode."""
        with self._lock:
            if self._ws is not None:
                if self._sleeping:
                    # Active client is sleeping — we'll let the new one in below
                    pass
                else:
                    return False, 'occupied'
            self._ws = ws; self._ip = ip; self._sleeping = False; self._sleep_since = 0.0
            return True, ''

    def release(self, ws) -> None:
        with self._lock:
            if self._ws is ws: self._ws = None; self._ip = None

    def force_release(self) -> None:
        with self._lock: self._ws = None; self._ip = None; self._sleeping = False; self._sleep_since = 0.0

    def put_to_sleep(self, ws) -> bool:
        """Mark the active session as sleeping. Returns True if successful."""
        with self._lock:
            if self._ws is not ws:
                return False
            self._sleeping = True
            self._sleep_since = __import__('time').time()
            return True

    def wake_up(self, ws) -> bool:
        """Wake a sleeping session. Returns True if successful."""
        with self._lock:
            if self._ws is not ws:
                return False
            self._sleeping = False
            self._sleep_since = 0.0
            return True

    @property
    def is_sleeping(self) -> bool:
        with self._lock: return self._sleeping

    @property
    def sleep_duration(self) -> float:
        """How long the client has been sleeping (seconds)."""
        with self._lock:
            if not self._sleeping: return 0.0
            return __import__('time').time() - self._sleep_since

    @property
    def ws(self): return self._ws
    @property
    def ip(self) -> str | None: return self._ip

    def is_verified(self, ip: str) -> bool:
        with self._lock: return self._verified.get(ip, False)
    def set_verified(self, ip: str, value: bool = True) -> None:
        with self._lock: self._verified[ip] = value
    def clear_verified(self, ip: str) -> None:
        with self._lock: self._verified.pop(ip, None)
    def clear_all(self) -> None:
        with self._lock: self._ws = None; self._ip = None; self._verified.clear()



class _StreamStateManager:
    def __init__(self) -> None:
        self._lock              = threading.Lock()
        self.screen_streaming   = False
        self.audio_streaming    = False
        self.mic_active         = False
        self.mode               = ''
        self.transport          = ''
        self.screen_error       = ''
        self.screen_thread: threading.Thread | None = None
        self.audio_thread:  threading.Thread | None = None
        self.mic_thread:    threading.Thread | None = None

    def start_screen(self, transport: str, worker) -> bool:
        with self._lock:
            if self.screen_streaming: return False
            self.screen_streaming = True
            self.transport        = transport
            self.mode             = 'starting'
            self.screen_error     = ''
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self.screen_thread = t
        return True

    def stop_screen(self) -> None:
        with self._lock:
            self.screen_streaming = False
            self.mode             = 'stopped'

    def restart_screen(self, transport: str, worker) -> None:
        with self._lock:
            self.screen_streaming = False
        if self.screen_thread and self.screen_thread.is_alive():
            self.screen_thread.join(timeout=1.5)
        with self._lock:
            self.screen_streaming = True
            self.transport        = transport
            self.mode             = 'starting'
            self.screen_error     = ''
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self.screen_thread = t

    def set_mode(self, mode: str) -> None:
        with self._lock: self.mode = mode

    def set_error(self, err: str) -> None:
        with self._lock: self.screen_error = err

    def start_audio(self, worker) -> bool:
        with self._lock:
            if self.audio_streaming: return False
            self.audio_streaming = True
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self.audio_thread = t
        return True

    def stop_audio(self) -> None:
        with self._lock: self.audio_streaming = False; self.audio_thread = None

    def start_mic(self, worker) -> bool:
        with self._lock:
            if self.mic_active: return False
            self.mic_active = True
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self.mic_thread = t
        return True

    def stop_mic(self) -> None:
        with self._lock: self.mic_active = False; self.mic_thread = None

    def stop_all(self) -> None:
        with self._lock:
            self.screen_streaming = False
            self.audio_streaming  = False
            self.mic_active       = False
            self.mode             = 'idle'
            self.transport        = ''

    @property
    def status(self) -> dict:
        with self._lock:
            return {'screen': self.screen_streaming, 'audio': self.audio_streaming,
                    'mic': self.mic_active, 'mode': self.mode, 'transport': self.transport}

