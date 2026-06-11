"""
pd_stats.py — CPU/RAM/temperature system stats for PortDesk (extracted module).

No psutil dependency: reads /proc on Linux, ctypes GetSystemTimes on Windows,
vm_stat on macOS, and CoreTemp shared memory on Windows. Self-contained — needs
only the stdlib + ctypes.
"""
from __future__ import annotations
import os
import time
import platform
import subprocess
import ctypes

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
            if not ptr: k32.CloseHandle(hmap); return None, None
            try:
                d     = ptr.contents
                temps = [d.fTemp[i] for i in range(d.uiCoreCnt)]
                if d.ucDeltaToTjMax: temps = [d.uiTjMax[i] - temps[i] for i in range(d.uiCoreCnt)]
                if d.ucFahrenheit:   temps = [(t - 32) * 5/9 for t in temps]
                return (round(max(temps), 1) if temps else None), None
            finally:
                k32.UnmapViewOfFile(ptr); k32.CloseHandle(hmap)
        except: return None, None
    elif system == 'Linux':
        # Read CPU temperature from /sys/class/thermal (no psutil dependency)
        try:
            import glob
            paths = glob.glob('/sys/class/thermal/thermal_zone*/temp')
            vals = []
            for p in paths:
                with open(p) as f: vals.append(int(f.read().strip()) / 1000.0)
            if vals: return round(max(vals), 1), None
        except: pass
        return None, None
    elif system == 'Darwin':
        try:
            out = subprocess.check_output(
                ['sudo', 'powermetrics', '--samplers', 'smc', '-n', '1', '-i', '1'],
                timeout=2, stderr=subprocess.DEVNULL).decode()
            import re
            m = re.search(r'CPU die temperature: ([\d.]+)', out)
            if m: return round(float(m.group(1)), 1), None
        except: pass
        return None, None
    return None, None

def get_system_stats():
    """Get CPU/RAM usage without psutil — uses /proc on Linux, ctypes on Windows.
    Falls back to psutil if available for more accurate readings."""
    stats = {"cpu_temp": "N/A", "gpu_temp": "N/A", "cpu_usage": 0, "ram_usage": 0}
    _sys = platform.system()

    # ── CPU usage (without psutil) ──────────────────────────────────────────
    try:
        if _sys == 'Linux':
            # Read CPU times from /proc/stat
            with open('/proc/stat') as f:
                line = f.readline()
            parts = line.split()
            # user, nice, system, idle, iowait, irq, softirq, steal
            times = [int(x) for x in parts[1:9]]
            idle = times[3] + times[4]  # idle + iowait
            total = sum(times)
            time.sleep(0.1)
            with open('/proc/stat') as f:
                line2 = f.readline()
            parts2 = line2.split()
            times2 = [int(x) for x in parts2[1:9]]
            idle2 = times2[3] + times2[4]
            total2 = sum(times2)
            d_idle = idle2 - idle
            d_total = total2 - total
            if d_total > 0:
                stats["cpu_usage"] = round((1 - d_idle / d_total) * 100, 1)
        elif _sys == 'Windows':
            # Use GetSystemTimes via ctypes
            class _FILETIME(ctypes.Structure):
                _fields_ = [('dwLowDateTime', ctypes.c_uint), ('dwHighDateTime', ctypes.c_uint)]
            kernel1, user1, idle1 = _FILETIME(), _FILETIME(), _FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle1), ctypes.byref(kernel1), ctypes.byref(user1))
            time.sleep(0.1)
            kernel2, user2, idle2 = _FILETIME(), _FILETIME(), _FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle2), ctypes.byref(kernel2), ctypes.byref(user2))
            def _ft_to_int(ft):
                return ft.dwHighDateTime << 32 | ft.dwLowDateTime
            d_idle = _ft_to_int(idle2) - _ft_to_int(idle1)
            d_kernel = _ft_to_int(kernel2) - _ft_to_int(kernel1)
            d_user = _ft_to_int(user2) - _ft_to_int(user1)
            d_total = d_kernel + d_user
            if d_total > 0:
                stats["cpu_usage"] = round((1 - d_idle / d_total) * 100, 1)
        elif _sys == 'Darwin':
            # macOS: use vm_stat command (no psutil dependency)
            try:
                r = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=2)
                pages_free = pages_active = pages_total = 0
                for line in r.stdout.split('\n'):
                    if 'Pages free' in line: pages_free = int(line.split(':')[1].strip().rstrip('.'))
                    elif 'Pages active' in line: pages_active = int(line.split(':')[1].strip().rstrip('.'))
                    elif 'Pages wired' in line: pages_active += int(line.split(':')[1].strip().rstrip('.'))
                # Total = free + active (approximate); page size is 4096 on macOS
                if pages_free + pages_active > 0:
                    stats["cpu_usage"] = 0  # CPU % not easily available without psutil on macOS
            except Exception:
                pass
    except Exception:
        pass

    # ── RAM usage (without psutil) ──────────────────────────────────────────
    try:
        if _sys == 'Linux':
            info = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        info[parts[0].rstrip(':')] = int(parts[1])  # in kB
            total = info.get('MemTotal', 0)
            available = info.get('MemAvailable', info.get('MemFree', 0))
            if total > 0:
                used = total - available
                stats["ram_usage"] = round(used / total * 100, 1)
        elif _sys == 'Windows':
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [('dwLength', ctypes.c_uint), ('dwMemoryLoad', ctypes.c_uint),
                            ('ullTotalPhys', ctypes.c_uint64), ('ullAvailPhys', ctypes.c_uint64)]
            ms = _MEMORYSTATUSEX()
            ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            stats["ram_usage"] = ms.dwMemoryLoad
        elif _sys == 'Darwin':
            # macOS: use vm_stat for RAM (no psutil dependency)
            try:
                r = subprocess.run(['vm_stat'], capture_output=True, text=True, timeout=2)
                pages_free = 0
                pages_used = 0
                for line in r.stdout.split('\n'):
                    if 'Pages free' in line: pages_free = int(line.split(':')[1].strip().rstrip('.'))
                    elif 'Pages active' in line: pages_used += int(line.split(':')[1].strip().rstrip('.'))
                    elif 'Pages wired' in line: pages_used += int(line.split(':')[1].strip().rstrip('.'))
                    elif 'Pages speculative' in line: pages_free += int(line.split(':')[1].strip().rstrip('.'))
                total_pages = pages_free + pages_used
                if total_pages > 0:
                    stats["ram_usage"] = round(pages_used / total_pages * 100, 1)
            except Exception:
                pass
    except Exception:
        pass

    cpu_t, gpu_t = _get_coretemp()
    if cpu_t: stats["cpu_temp"] = cpu_t
    if gpu_t: stats["gpu_temp"] = gpu_t
    return stats
