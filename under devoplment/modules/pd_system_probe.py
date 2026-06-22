"""
pd_system_probe.py — Dynamic system capability detection for PortDesk.

Provides runtime system profiling for adaptive resource allocation.
No static equations — all values derived from actual hardware/OS state.
"""
from __future__ import annotations
import os
import sys
import platform
import subprocess
import shutil
from dataclasses import dataclass
from typing import List, Optional

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


@dataclass(frozen=True)
class SystemProfile:
    cpu_count: int
    cpu_freq_max_mhz: float
    memory_gb: float
    gpu_vendors: List[str]
    hw_encoders: List[str]
    os_name: str
    arch: str
    python_version: tuple
    has_uvloop: bool
    has_turbojpeg: bool
    has_dxcam: bool
    has_mss: bool
    disk_free_gb: float


def _detect_gpu_vendors() -> List[str]:
    vendors = []
    sys_name = platform.system()
    
    if sys_name == 'Windows':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\VIDEO")
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    subkey = winreg.OpenKey(key, subkey_name)
                    desc, _ = winreg.QueryValueEx(subkey, "InstalledDisplayDrivers")
                    desc_lower = desc.lower()
                    if 'nvidia' in desc_lower or 'nvlddmkm' in desc_lower:
                        vendors.append('nvidia')
                    elif 'amd' in desc_lower or 'atikmdag' in desc_lower:
                        vendors.append('amd')
                    elif 'intel' in desc_lower or 'igdkmd' in desc_lower:
                        vendors.append('intel')
                    winreg.CloseKey(subkey)
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except Exception:
            pass
    
    elif sys_name == 'Linux':
        try:
            out = subprocess.run(['lspci', '-nn'], capture_output=True, text=True, timeout=3).stdout.lower()
            if 'nvidia' in out: vendors.append('nvidia')
            if 'amd' in out or 'ati' in out: vendors.append('amd')
            if 'intel' in out: vendors.append('intel')
        except Exception:
            pass
    
    elif sys_name == 'Darwin':
        try:
            out = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, timeout=5).stdout.lower()
            if 'nvidia' in out: vendors.append('nvidia')
            if 'amd' in out or 'ati' in out: vendors.append('amd')
            if 'intel' in out: vendors.append('intel')
            if 'apple' in out or 'm1' in out or 'm2' in out or 'm3' in out: vendors.append('apple')
        except Exception:
            pass
    
    return list(set(vendors))


def _detect_hw_encoders() -> List[str]:
    encoders = []
    if not shutil.which('ffmpeg'):
        return encoders
    
    try:
        out = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=5).stdout.lower()
        encoder_map = {
            'h264_nvenc': 'nvenc',
            'hevc_nvenc': 'nvenc',
            'h264_amf': 'amf',
            'hevc_amf': 'amf',
            'h264_qsv': 'qsv',
            'hevc_qsv': 'qsv',
            'h264_vaapi': 'vaapi',
            'hevc_vaapi': 'vaapi',
            'h264_videotoolbox': 'videotoolbox',
            'hevc_videotoolbox': 'videotoolbox',
        }
        for ffmpeg_enc, name in encoder_map.items():
            if ffmpeg_enc in out and name not in encoders:
                encoders.append(name)
    except Exception:
        pass
    
    return encoders


def _check_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


def probe_system() -> SystemProfile:
    cpu_count = os.cpu_count() or 4
    
    cpu_freq_max = 0.0
    memory_gb = 8.0
    disk_free_gb = 100.0
    
    if PSUTIL_AVAILABLE:
        try:
            freq = psutil.cpu_freq()
            if freq and freq.max:
                cpu_freq_max = freq.max
            memory_gb = psutil.virtual_memory().total / (1024 ** 3)
            disk_free_gb = psutil.disk_usage('/').free / (1024 ** 3)
        except Exception:
            pass
    
    gpu_vendors = _detect_gpu_vendors()
    hw_encoders = _detect_hw_encoders()
    
    return SystemProfile(
        cpu_count=cpu_count,
        cpu_freq_max_mhz=cpu_freq_max,
        memory_gb=memory_gb,
        gpu_vendors=gpu_vendors,
        hw_encoders=hw_encoders,
        os_name=platform.system(),
        arch=platform.machine(),
        python_version=sys.version_info[:2],
        has_uvloop=_check_import('uvloop'),
        has_turbojpeg=_check_import('turbojpeg'),
        has_dxcam=_check_import('dxcam'),
        has_mss=_check_import('mss'),
        disk_free_gb=disk_free_gb,
    )


def compute_pool_config(profile: SystemProfile) -> dict:
    cpu = profile.cpu_count
    mem_gb = profile.memory_gb
    is_windows = profile.os_name == 'Windows'
    is_macos = profile.os_name == 'Darwin'
    is_linux = profile.os_name == 'Linux'
    
    jpeg_workers = min(max(2, cpu // 3), max(1, int(mem_gb * 0.4)), 8)
    
    if is_windows:
        jpeg_workers = max(1, jpeg_workers - 1)
    elif is_macos:
        jpeg_workers = min(jpeg_workers, 4)
    
    has_hw_encoder = bool(profile.hw_encoders)
    webrtc_encode = 1 if has_hw_encoder else 0
    
    io_threads = min(max(4, cpu), 32)
    if is_windows:
        io_threads = min(io_threads, 16)
    
    frame_buffers = min(max(3, cpu // 2), 8)
    if mem_gb < 4:
        frame_buffers = min(frame_buffers, 3)
    
    return {
        'capture_processes': 1,
        'jpeg_encode_processes': jpeg_workers,
        'webrtc_encode_processes': webrtc_encode,
        'io_threads': io_threads,
        'input_threads': 2,
        'frame_buffer_count': frame_buffers,
        'hw_encoders': profile.hw_encoders,
        'gpu_vendors': profile.gpu_vendors,
    }


_SYSTEM_PROFILE: Optional[SystemProfile] = None
_POOL_CONFIG: Optional[dict] = None


def get_system_profile() -> SystemProfile:
    global _SYSTEM_PROFILE
    if _SYSTEM_PROFILE is None:
        _SYSTEM_PROFILE = probe_system()
    return _SYSTEM_PROFILE


def get_pool_config() -> dict:
    global _POOL_CONFIG
    if _POOL_CONFIG is None:
        _POOL_CONFIG = compute_pool_config(get_system_profile())
    return _POOL_CONFIG


def invalidate_cache():
    global _SYSTEM_PROFILE, _POOL_CONFIG
    _SYSTEM_PROFILE = None
    _POOL_CONFIG = None