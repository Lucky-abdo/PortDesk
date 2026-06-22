"""
pd_capture_process.py — Screen capture subprocess for PortDesk.

Runs in a separate process to bypass GIL for CPU-bound capture/encode.
Communicates via multiprocessing.Queue and shared_memory.
"""
from __future__ import annotations
import os
import sys
import time
import multiprocessing as mp
from multiprocessing import shared_memory
from dataclasses import dataclass
from typing import Optional, Tuple, Any
import numpy as np

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from turbojpeg import TurboJPEG, TJPF_BGR, TJSAMP_444
    TJ_AVAILABLE = True
except ImportError:
    TJ_AVAILABLE = False


@dataclass
class CaptureConfig:
    height: int = 720
    fps: int = 30
    monitor: int = 1
    scale: float = 1.0
    grey: bool = False
    cursor_color_bgr: Tuple[int, int, int] = (255, 255, 255)
    codec: str = 'auto'


@dataclass
class FrameMetadata:
    width: int
    height: int
    timestamp: float
    seq: int
    shm_name: str
    shm_size: int


class CaptureProcess:
    def __init__(self, config_queue: mp.Queue, frame_queue: mp.Queue, control_queue: mp.Queue):
        self.config_queue = config_queue
        self.frame_queue = frame_queue
        self.control_queue = control_queue
        self.running = False
        self.config = CaptureConfig()
        self.cap = None
        self.tj = None
        self._frame_seq = 0
        self._shm_pool = []
        self._shm_index = 0

    def _init_capture(self):
        sys.path.insert(0, os.path.dirname(__file__))
        from pd_capture import _create_capture_backend
        mon_idx = max(0, self.config.monitor - 1)
        self.cap = _create_capture_backend(mon_idx)
        if self.cap is None:
            raise RuntimeError("No capture backend available")
        
        if TJ_AVAILABLE:
            self.tj = TurboJPEG()

    def _get_shm_buffer(self, size: int) -> shared_memory.SharedMemory:
        for shm in self._shm_pool:
            if shm.size >= size:
                return shm
        shm = shared_memory.SharedMemory(create=True, size=size)
        self._shm_pool.append(shm)
        return shm

    def _encode_jpeg(self, arr: np.ndarray, quality: int) -> bytes:
        if self.tj:
            return bytes(self.tj.encode(arr, quality=quality, jpeg_subsample=TJSAMP_444, pixel_format=TJPF_BGR))
        _, enc = cv2.imencode('.jpg', arr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return enc.tobytes()

    def run(self):
        self.running = True
        self._init_capture()
        
        while self.running:
            try:
                while not self.config_queue.empty():
                    new_config = self.config_queue.get_nowait()
                    for k, v in new_config.items():
                        if hasattr(self.config, k):
                            setattr(self.config, k, v)
                
                while not self.control_queue.empty():
                    cmd = self.control_queue.get_nowait()
                    if cmd == 'stop':
                        self.running = False
                        break
                
                if not self.running:
                    break

                arr, mon_info = self.cap.grab(max(0, self.config.monitor - 1))
                if arr is None:
                    time.sleep(0.001)
                    continue

                target_h = self.config.height
                src_h, src_w = arr.shape[:2]
                nw = int(src_w * target_h / src_h)
                
                if src_h != target_h:
                    interp = cv2.INTER_AREA if target_h < src_h else cv2.INTER_LINEAR
                    arr = cv2.resize(arr, (nw, target_h), interpolation=interp)
                else:
                    arr = np.ascontiguousarray(arr)

                scale_val = self.config.scale
                if scale_val != 1.0:
                    sh, sw = arr.shape[:2]
                    arr = cv2.resize(arr, (int(sw * scale_val), int(sh * scale_val)), interpolation=cv2.INTER_AREA)

                if self.config.grey:
                    g = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
                    arr = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)

                h, w = arr.shape[:2]
                frame_size = h * w * 3
                
                shm = self._get_shm_buffer(frame_size)
                shm.buf[:frame_size] = arr.tobytes()
                
                metadata = FrameMetadata(
                    width=w, height=h,
                    timestamp=time.time(),
                    seq=self._frame_seq,
                    shm_name=shm.name,
                    shm_size=frame_size
                )
                self._frame_seq += 1
                
                try:
                    self.frame_queue.put_nowait(metadata)
                except:
                    pass

                frame_budget = 1.0 / max(1, self.config.fps)
                elapsed = time.time() - metadata.timestamp
                if frame_budget - elapsed > 0.001:
                    time.sleep(frame_budget - elapsed)

            except Exception as e:
                print(f"Capture process error: {e}", flush=True)
                time.sleep(0.1)
        
        self._cleanup()

    def _cleanup(self):
        if self.cap:
            try: self.cap.close()
            except: pass
        for shm in self._shm_pool:
            try: shm.close(); shm.unlink()
            except: pass
        self._shm_pool.clear()


def capture_process_entry(config_queue: mp.Queue, frame_queue: mp.Queue, control_queue: mp.Queue):
    proc = CaptureProcess(config_queue, frame_queue, control_queue)
    proc.run()