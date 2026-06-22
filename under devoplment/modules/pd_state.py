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
# Uses RLock (reentrant) to prevent deadlock when same lock acquired recursively (Pattern 33).
import threading
import logging
import time


class DeadlockDetectingRLock:
    """RLock with timeout + deadlock detection logging."""
    __slots__ = ('_lock', '_timeout', '_owner', '_name')
    
    def __init__(self, timeout=5.0, name='lock'):
        self._lock = threading.RLock()
        self._timeout = timeout
        self._owner = None
        self._name = name
    
    def acquire(self, blocking=True, timeout=-1):
        if timeout < 0:
            timeout = self._timeout
        if not self._lock.acquire(blocking, timeout):
            owner_name = f"thread {self._owner}" if self._owner else "unknown"
            logging.critical(f"DEADLOCK DETECTED on {self._name}: "
                           f"{threading.current_thread().name} waited {timeout}s, "
                           f"held by {owner_name}")
            raise RuntimeError(f"Deadlock on {self._name} — held by {owner_name}")
        self._owner = threading.current_thread().ident
        return True
    
    def release(self):
        self._owner = None
        self._lock.release()
    
    def __enter__(self):
        self.acquire()
        return self
    
    def __exit__(self, *args):
        self.release()
    
    # Delegate other RLock methods
    def _is_owned(self): return self._lock._is_owned()
    def __repr__(self): return f"<DeadlockDetectingRLock {self._name} owner={self._owner}>"


class _SessionManager:
    def __init__(self) -> None:
        self._lock            = DeadlockDetectingRLock(timeout=5.0, name='session')
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
        self._lock              = DeadlockDetectingRLock(timeout=5.0, name='stream')
        self._screen_streaming   = False
        self._audio_streaming    = False
        self._mic_active         = False
        self._mode               = ''
        self._transport          = ''
        self._screen_error       = ''
        self._screen_thread: threading.Thread | None = None
        self._audio_thread:  threading.Thread | None = None
        self._mic_thread:    threading.Thread | None = None

    @property
    def screen_streaming(self) -> bool:
        with self._lock: return self._screen_streaming

    @screen_streaming.setter
    def screen_streaming(self, v: bool) -> None:
        with self._lock: self._screen_streaming = v

    @property
    def audio_streaming(self) -> bool:
        with self._lock: return self._audio_streaming

    @audio_streaming.setter
    def audio_streaming(self, v: bool) -> None:
        with self._lock: self._audio_streaming = v

    @property
    def mic_active(self) -> bool:
        with self._lock: return self._mic_active

    @mic_active.setter
    def mic_active(self, v: bool) -> None:
        with self._lock: self._mic_active = v

    @property
    def mode(self) -> str:
        with self._lock: return self._mode

    @mode.setter
    def mode(self, v: str) -> None:
        with self._lock: self._mode = v

    @property
    def transport(self) -> str:
        with self._lock: return self._transport

    @transport.setter
    def transport(self, v: str) -> None:
        with self._lock: self._transport = v

    @property
    def screen_error(self) -> str:
        with self._lock: return self._screen_error

    @screen_error.setter
    def screen_error(self, v: str) -> None:
        with self._lock: self._screen_error = v

    @property
    def screen_thread(self) -> threading.Thread | None:
        with self._lock: return self._screen_thread

    @screen_thread.setter
    def screen_thread(self, v: threading.Thread | None) -> None:
        with self._lock: self._screen_thread = v

    @property
    def audio_thread(self) -> threading.Thread | None:
        with self._lock: return self._audio_thread

    @audio_thread.setter
    def audio_thread(self, v: threading.Thread | None) -> None:
        with self._lock: self._audio_thread = v

    @property
    def mic_thread(self) -> threading.Thread | None:
        with self._lock: return self._mic_thread

    @mic_thread.setter
    def mic_thread(self, v: threading.Thread | None) -> None:
        with self._lock: self._mic_thread = v

    def start_screen(self, transport: str, worker) -> bool:
        with self._lock:
            if self._screen_streaming: return False
            self._screen_streaming = True
            self._transport        = transport
            self._mode             = 'starting'
            self._screen_error     = ''
        
        # Initialize pool manager (starts capture process)
        from modules.pd_pool_manager import initialize_pools
        initialize_pools()
        
        # Start encode/emit task (worker function now uses capture process)
        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self._screen_thread = t
        return True

    def stop_screen(self) -> None:
        with self._lock:
            self._screen_streaming = False
            self._mode             = 'stopped'
        
        # Stop capture process
        from modules.pd_pool_manager import stop_capture
        stop_capture()

    def restart_screen(self, transport: str, worker) -> None:
        with self._lock:
            self._screen_streaming = False
        if self._screen_thread and self._screen_thread.is_alive():
            self._screen_thread.join(timeout=1.5)
        
        # Restart capture process
        from modules.pd_pool_manager import stop_capture, initialize_pools
        stop_capture()
        initialize_pools()
        
        with self._lock:
            self._screen_streaming = True
            self._transport        = transport
            self._mode             = 'starting'
            self._screen_error     = ''
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self._screen_thread = t

    def set_mode(self, mode: str) -> None:
        with self._lock: self._mode = mode

    def set_error(self, err: str) -> None:
        with self._lock: self._screen_error = err

    def start_audio(self, worker) -> bool:
        with self._lock:
            if self._audio_streaming: return False
            self._audio_streaming = True
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self._audio_thread = t
        return True

    def stop_audio(self) -> None:
        with self._lock: self._audio_streaming = False; self._audio_thread = None

    def start_mic(self, worker) -> bool:
        with self._lock:
            if self._mic_active: return False
            self._mic_active = True
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        with self._lock: self._mic_thread = t
        return True

    def stop_mic(self) -> None:
        with self._lock: self._mic_active = False; self._mic_thread = None

    def stop_all(self) -> None:
        with self._lock:
            self._screen_streaming = False
            self._audio_streaming  = False
            self._mic_active       = False
            self._mode             = 'idle'
            self._transport        = ''

    @property
    def status(self) -> dict:
        with self._lock:
            return {'screen': self._screen_streaming, 'audio': self._audio_streaming,
                    'mic': self._mic_active, 'mode': self._mode, 'transport': self._transport}

