"""
pd_pool_manager.py — Dynamic process/thread pool manager for PortDesk.

Manages capture processes, encode processes, and I/O threads based on
runtime system profiling. No static equations.
"""
from __future__ import annotations
import os
import sys
import asyncio
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import dataclass
from typing import Optional, Dict, Any
import threading

from pd_system_probe import get_pool_config, get_system_profile, SystemProfile


@dataclass
class PoolHandles:
    capture_config_queue: Optional[mp.Queue] = None
    capture_frame_queue: Optional[mp.Queue] = None
    capture_control_queue: Optional[mp.Queue] = None
    capture_process: Optional[mp.Process] = None
    
    jpeg_encode_executor: Optional[ProcessPoolExecutor] = None
    webrtc_encode_executor: Optional[ProcessPoolExecutor] = None
    
    io_executor: Optional[ThreadPoolExecutor] = None
    input_executor: Optional[ThreadPoolExecutor] = None


_pool_handles: Optional[PoolHandles] = None
_init_lock = threading.Lock()
_initialized = False


def initialize_pools():
    global _pool_handles, _initialized
    with _init_lock:
        if _initialized:
            return
        
        config = get_pool_config()
        
        ctx = mp.get_context('spawn')
        
        capture_config_queue = ctx.Queue()
        capture_frame_queue = ctx.Queue(maxsize=4)
        capture_control_queue = ctx.Queue()
        
        from pd_capture_process import capture_process_entry
        capture_process = ctx.Process(
            target=capture_process_entry,
            args=(capture_config_queue, capture_frame_queue, capture_control_queue),
            daemon=True
        )
        capture_process.start()
        
        jpeg_encode_executor = ProcessPoolExecutor(
            max_workers=config['jpeg_encode_processes'],
            mp_context=ctx
        ) if config['jpeg_encode_processes'] > 0 else None
        
        webrtc_encode_executor = ProcessPoolExecutor(
            max_workers=config['webrtc_encode_processes'],
            mp_context=ctx
        ) if config['webrtc_encode_processes'] > 0 else None
        
        io_executor = ThreadPoolExecutor(
            max_workers=config['io_threads'],
            thread_name_prefix='portdesk-io'
        )
        
        input_executor = ThreadPoolExecutor(
            max_workers=config['input_threads'],
            thread_name_prefix='portdesk-input'
        )
        
        _pool_handles = PoolHandles(
            capture_config_queue=capture_config_queue,
            capture_frame_queue=capture_frame_queue,
            capture_control_queue=capture_control_queue,
            capture_process=capture_process,
            jpeg_encode_executor=jpeg_encode_executor,
            webrtc_encode_executor=webrtc_encode_executor,
            io_executor=io_executor,
            input_executor=input_executor,
        )
        _initialized = True


def get_pools() -> PoolHandles:
    if not _initialized:
        initialize_pools()
    return _pool_handles


def send_capture_config(config: Dict[str, Any]):
    pools = get_pools()
    try:
        pools.capture_config_queue.put_nowait(config)
    except:
        pass


def apply_stream_config_to_capture():
    """Apply current stream_config to capture process. Call when stream settings change."""
    from SERVER import stream_config, _stream_config_lock
    with _stream_config_lock:
        cfg = stream_config.copy()
    send_capture_config({
        'height': cfg['height'],
        'fps': cfg['fps'],
        'monitor': cfg.get('monitor', 1),
        'scale': cfg.get('scale', 1.0),
        'grey': cfg.get('grey', False),
        'cursor_color_bgr': cfg.get('cursor_color_bgr', (255, 255, 255)),
        'codec': cfg.get('codec', 'auto'),
    })


def get_capture_frame(timeout: float = 0.1):
    pools = get_pools()
    try:
        return pools.capture_frame_queue.get(timeout=timeout)
    except:
        return None


def stop_capture():
    pools = get_pools()
    try:
        pools.capture_control_queue.put_nowait('stop')
    except:
        pass
    if pools.capture_process and pools.capture_process.is_alive():
        pools.capture_process.join(timeout=2.0)


def shutdown_pools():
    global _pool_handles, _initialized
    with _init_lock:
        if not _initialized:
            return
        
        stop_capture()
        
        if _pool_handles.jpeg_encode_executor:
            _pool_handles.jpeg_encode_executor.shutdown(wait=True)
        if _pool_handles.webrtc_encode_executor:
            _pool_handles.webrtc_encode_executor.shutdown(wait=True)
        if _pool_handles.io_executor:
            _pool_handles.io_executor.shutdown(wait=True)
        if _pool_handles.input_executor:
            _pool_handles.input_executor.shutdown(wait=True)
        
        _pool_handles = None
        _initialized = False


def get_io_executor() -> ThreadPoolExecutor:
    return get_pools().io_executor


def get_input_executor() -> ThreadPoolExecutor:
    return get_pools().input_executor


def get_jpeg_encode_executor() -> Optional[ProcessPoolExecutor]:
    return get_pools().jpeg_encode_executor


def get_webrtc_encode_executor() -> Optional[ProcessPoolExecutor]:
    return get_pools().webrtc_encode_executor