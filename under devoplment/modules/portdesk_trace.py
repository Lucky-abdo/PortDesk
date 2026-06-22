"""
PortDesk Trace Module — True debug tracing (function entry/exit, subprocess, timestamps).
Replaces _vprint with structured tracing. Zero overhead when disabled.
"""
import sys
import time
import threading
import functools
import asyncio
import subprocess as _subprocess
from typing import Any, Callable, Optional


class Tracer:
    """Thread-safe tracer with function entry/exit, subprocess, and debug logging."""
    __slots__ = ('enabled', '_lock', '_depth', '_thread_names')
    
    def __init__(self) -> None:
        self.enabled = False
        self._lock = threading.Lock()
        self._depth = 0
        self._thread_names = {}
    
    def _format(self, level: str, msg: str) -> str:
        tid = threading.current_thread().ident
        tname = self._thread_names.get(tid, threading.current_thread().name)
        ts = time.strftime('%H:%M:%S') + f'.{int(time.time() * 1000) % 1000:03d}'
        indent = '  ' * self._depth
        return f'[{level}] [{tname}:{tid}] [{ts}] {indent}{msg}'
    
    def enter(self, func_name: str, **kwargs) -> float:
        if not self.enabled:
            return 0.0
        args = ', '.join(f'{k}={v!r}' for k, v in kwargs.items())
        with self._lock:
            print(self._format('TRACE', f'▶ {func_name}({args}) ENTER'), file=sys.stderr)
            self._depth += 1
        return time.perf_counter()
    
    def exit(self, func_name: str, start: float = 0.0, result: Any = None) -> None:
        if not self.enabled:
            return
        duration = (time.perf_counter() - start) * 1000 if start else 0.0
        with self._lock:
            self._depth = max(0, self._depth - 1)
            extra = f' ({duration:.1f}ms)' if start else ''
            if result is not None:
                extra += f' → {result!r}'
            print(self._format('TRACE', f'◀ {func_name} EXIT{extra}'), file=sys.stderr)
    
    def debug(self, msg: str) -> None:
        if self.enabled:
            print(self._format('DEBUG', msg), file=sys.stderr)
    
    def info(self, msg: str) -> None:
        if self.enabled:
            print(self._format('INFO', msg), file=sys.stderr)
    
    def warning(self, msg: str) -> None:
        if self.enabled:
            print(self._format('WARN', msg), file=sys.stderr)
    
    def error(self, msg: str) -> None:
        if self.enabled:
            print(self._format('ERROR', msg), file=sys.stderr)
    
    def subprocess(self, cmd: list | str, **kwargs) -> None:
        if self.enabled:
            if isinstance(cmd, list):
                cmd_str = ' '.join(cmd)
            else:
                cmd_str = str(cmd)
            print(self._format('SUBPROC', f'$ {cmd_str}'), file=sys.stderr)


tracer = Tracer()


def trace(func: Callable) -> Callable:
    """Decorator for automatic function entry/exit tracing. Supports both sync and async functions."""
    if asyncio.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            start = tracer.enter(func.__name__, **kwargs)
            result = None
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                tracer.exit(func.__name__, start, result)
        return async_wrapper
    else:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            start = tracer.enter(func.__name__, **kwargs)
            result = None
            try:
                result = func(*args, **kwargs)
                return result
            finally:
                tracer.exit(func.__name__, start, result)
        return sync_wrapper


class trace_block:
    """Context manager for manual code block tracing."""
    __slots__ = ('name', 'kwargs', 'start')
    
    def __init__(self, name: str, **kwargs) -> None:
        self.name = name
        self.kwargs = kwargs
        self.start = 0.0
    
    def __enter__(self) -> None:
        self.start = tracer.enter(self.name, **self.kwargs)
    
    def __exit__(self, *exc) -> None:
        tracer.exit(self.name, self.start)


def trace_subprocess_run(cmd, *args, **kwargs):
    """Wrapper for subprocess.run with tracing."""
    tracer.subprocess(cmd)
    return _subprocess.run(cmd, *args, **kwargs)


def trace_subprocess_popen(cmd, *args, **kwargs):
    """Wrapper for subprocess.Popen with tracing."""
    tracer.subprocess(cmd)
    return _subprocess.Popen(cmd, *args, **kwargs)