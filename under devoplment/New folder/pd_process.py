"""
pd_process.py — task manager (process list/info/kill) for PortDesk (extracted).

No psutil dependency: uses ps/tasklist for listing, /proc + ctypes for details,
os.kill/taskkill for termination. Self-contained — stdlib only.
"""
from __future__ import annotations
import os
import platform
import subprocess
try:
    import ctypes
except Exception:
    ctypes = None

_CRITICAL_PROCS = {
    'systemd', 'init', 'kernel', 'launchd', 'csrss.exe',
    'wininit.exe', 'services.exe', 'lsass.exe', 'winlogon.exe',
    'svchost.exe', 'smss.exe', 'dwm.exe',
}

_LEGIT_PATHS = [
    'system32', 'system32\\', '/usr/', '/sbin/', '/lib/',
    'windows\\', '\\windows\\',
]

def _list_processes() -> list:
    """List running processes using subprocess — replaces psutil.process_iter().
    Returns list of dicts: {'pid', 'name', 'cpu', 'mem', 'status'}"""
    _sys = platform.system()
    procs = []
    try:
        if _sys == 'Linux':
            # ps aux format: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND
            r = subprocess.run(['ps', 'aux', '--sort=-pcpu'], capture_output=True, text=True, timeout=3)
            for line in r.stdout.strip().split('\n')[1:]:  # skip header
                parts = line.split(None, 10)
                if len(parts) < 11: continue
                try:
                    procs.append({
                        'pid': int(parts[1]),
                        'name': os.path.basename(parts[10])[:64],
                        'cpu': round(float(parts[2]), 1),
                        'mem': int(float(parts[5]) * 1024),  # RSS in KB → bytes
                        'status': parts[7][:1],  # S, R, Z, etc.
                    })
                except (ValueError, IndexError): continue
        elif _sys == 'Windows':
            # tasklist /FO CSV /NH
            r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=5)
            for line in r.stdout.strip().split('\n'):
                line = line.strip()
                if not line: continue
                # CSV: "name","pid","session","session#","mem"
                try:
                    parts = [p.strip('"') for p in line.split('","')]
                    if len(parts) >= 5:
                        pid = int(parts[1])
                        mem_str = parts[4].replace(',', '').replace(' K', '').replace(' ', '')
                        mem = int(mem_str) * 1024 if mem_str.isdigit() else 0
                        procs.append({
                            'pid': pid,
                            'name': parts[0][:64],
                            'cpu': 0,  # tasklist doesn't give CPU%; available via wmic but slow
                            'mem': mem,
                            'status': 'running',
                        })
                except (ValueError, IndexError): continue
        elif _sys == 'Darwin':
            r = subprocess.run(['ps', 'aux', '-r'], capture_output=True, text=True, timeout=3)
            for line in r.stdout.strip().split('\n')[1:]:
                parts = line.split(None, 10)
                if len(parts) < 11: continue
                try:
                    procs.append({
                        'pid': int(parts[1]),
                        'name': os.path.basename(parts[10])[:64],
                        'cpu': round(float(parts[2]), 1),
                        'mem': int(float(parts[5]) * 1024),
                        'status': parts[7][:1],
                    })
                except (ValueError, IndexError): continue
    except Exception:
        pass
    return procs[:80]

def _get_proc_info(pid: int) -> dict:
    """Get process info via subprocess — replaces psutil.Process().
    Returns dict with: pid, name, status, exe, cwd, cmdline, username, create_time"""
    _sys = platform.system()
    info = {
        'pid': pid, 'name': '', 'status': '', 'exe': None, 'cwd': None,
        'cmdline': [], 'username': None, 'create_time': None,
        'suspicious': False, 'warnings': [],
    }
    try:
        if _sys == 'Linux':
            # Read from /proc/<pid>/
            proc_dir = f'/proc/{pid}'
            if os.path.isdir(proc_dir):
                # Name and status from /proc/<pid>/stat
                try:
                    with open(f'{proc_dir}/stat') as f:
                        stat = f.read()
                    # Format: pid (comm) state ...
                    comm_start = stat.index('(') + 1
                    comm_end = stat.rindex(')')
                    info['name'] = stat[comm_start:comm_end]
                    state_char = stat[comm_end + 2:comm_end + 3]
                    state_map = {'R': 'running', 'S': 'sleeping', 'D': 'disk-sleep',
                                 'Z': 'zombie', 'T': 'stopped', 't': 'tracing-stop'}
                    info['status'] = state_map.get(state_char, state_char)
                except Exception: pass
                # Exe path
                try:
                    info['exe'] = os.readlink(f'{proc_dir}/exe')
                except Exception: pass
                # CWD
                try:
                    info['cwd'] = os.readlink(f'{proc_dir}/cwd')
                except Exception: pass
                # Cmdline
                try:
                    with open(f'{proc_dir}/cmdline', 'rb') as f:
                        info['cmdline'] = f.read().decode('utf-8', errors='replace').split('\x00')[:-1]
                except Exception: pass
                # Username from /proc/<pid>/status
                try:
                    with open(f'{proc_dir}/status') as f:
                        for line in f:
                            if line.startswith('Uid:'):
                                uid = int(line.split()[1])
                                import pwd
                                info['username'] = pwd.getpwuid(uid).pw_name
                                break
                except Exception: pass
                # Create time
                try:
                    info['create_time'] = os.stat(f'{proc_dir}').st_ctime
                except Exception: pass
        elif _sys == 'Windows':
            # Use wmic / tasklist for info
            try:
                r = subprocess.run(
                    ['wmic', 'process', 'where', f'ProcessId={pid}',
                     'get', 'Name,ExecutablePath,CommandLine,CreationDate',
                     '/FORMAT:LIST'],
                    capture_output=True, text=True, timeout=5
                )
                for line in r.stdout.strip().split('\n'):
                    line = line.strip()
                    if line.startswith('Name='): info['name'] = line[5:]
                    elif line.startswith('ExecutablePath='): info['exe'] = line[16:]
                    elif line.startswith('CommandLine='): info['cmdline'] = [line[12:]]
                    elif line.startswith('CreationDate='): info['create_time'] = line[13:]
            except Exception: pass
            info['status'] = 'running'
        elif _sys == 'Darwin':
            try:
                r = subprocess.run(['ps', '-p', str(pid), '-o', 'comm=,state=,etime='],
                                   capture_output=True, text=True, timeout=2)
                parts = r.stdout.strip().split(None, 2)
                if len(parts) >= 1: info['name'] = parts[0]
                if len(parts) >= 2: info['status'] = parts[1]
            except Exception: pass
    except Exception:
        pass
    return info

def _kill_process(pid: int) -> tuple:
    """Kill a process — replaces psutil.Process.terminate().
    Returns (success: bool, error_msg: str|None)"""
    _sys = platform.system()
    try:
        if _sys == 'Linux' or _sys == 'Darwin':
            import signal
            os.kill(pid, signal.SIGTERM)
        elif _sys == 'Windows':
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           capture_output=True, timeout=5)
        return True, None
    except ProcessLookupError:
        return False, 'process not found'
    except PermissionError:
        return False, 'access denied'
    except Exception as e:
        return False, str(e)
