"""
pd_process.py — task manager (process list/info/kill) for PortDesk (extracted).

Phase 5: now uses psutil as the primary backend for process listing and
deep-info gathering. Falls back to the legacy subprocess-based path
(ps/tasklist/wmic + /proc reads) if psutil is not importable — so the
module remains self-contained and the Docker image still works even if
psutil is removed from requirements-docker.txt.

The new _list_processes() returns richer per-process dicts including:
  - type:        'system' | 'application' | 'user'
  - suspicious:  bool — heuristic flags (non-system path, C2 ports, etc.)
  - verified:    bool — system user + system path (high-confidence legit)
  - exe, username, ram (RSS in bytes), cpu (percent, 1-decimal)

The 'mem' key is kept for back-compat with the existing CLIENT.html
which reads p.mem.
"""
from __future__ import annotations
import os
import platform
import subprocess
try:
    import ctypes
except Exception:
    ctypes = None

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

_CRITICAL_PROCS = {
    'systemd', 'init', 'kernel', 'launchd', 'csrss.exe',
    'wininit.exe', 'services.exe', 'lsass.exe', 'winlogon.exe',
    'svchost.exe', 'smss.exe', 'dwm.exe',
}

_LEGIT_PATHS = [
    'system32', 'system32\\', '/usr/', '/sbin/', '/lib/',
    'windows\\', '\\windows\\',
]

# Common C2 (command-and-control) listener ports. A process with an
# ESTABLISHED outbound connection to one of these is suspicious — most
# legitimate apps don't dial out to 4444/5555/6666/8080/8443/9001.
_C2_PORTS = {4444, 5555, 6666, 8080, 8443, 9001}

# System paths that are normally only inhabited by signed OS components.
# Used to classify a process as 'verified' (system user + system path).
_SYSTEM_PATH_PREFIXES_WIN = (
    r'c:\windows\system32',
    r'c:\windows\syswow64',
    r'c:\windows\system',
    r'c:\windows\winsxs',
)
_SYSTEM_PATH_PREFIXES_NIX = (
    '/usr/bin', '/usr/sbin', '/usr/lib', '/usr/libexec',
    '/sbin', '/bin', '/lib', '/lib64',
)

def _classify_process(username: str, exe: str) -> tuple:
    """Return (task_type, suspicious, verified) for a process given its
    username and exe path. Heuristics:

      - system user + system path → ('system', False, True)  # verified legit
      - system user + NON-system path → ('system', True, False)  # suspicious
      - non-system user + system path → ('system', False, False)  # running as user but from system dir
      - otherwise → ('application'/'user', False, False)
    """
    is_system_user = False
    if username:
        u = username.upper()
        is_system_user = ('SYSTEM' in u) or ('ROOT' in u) or (u.endswith('\\SYSTEM'))

    is_system_path = False
    if exe:
        e = exe.lower().replace('/', '\\')
        for p in _SYSTEM_PATH_PREFIXES_WIN:
            if e.startswith(p):
                is_system_path = True
                break
        if not is_system_path:
            el = exe.lower()
            for p in _SYSTEM_PATH_PREFIXES_NIX:
                if el.startswith(p):
                    is_system_path = True
                    break

    if is_system_user and is_system_path:
        return 'system', False, True
    if is_system_user and not is_system_path and exe:
        # e.g. SYSTEM running a process from C:\\Users\\Public\\temp\\x.exe
        return 'system', True, False
    if is_system_path:
        return 'system', False, False
    # Non-system user, non-system path → normal user application
    return 'application', False, False


def _list_processes_psutil() -> list:
    """psutil-based process listing. Returns at most ~80 procs, sorted by
    CPU% descending so the hottest/most-suspicious surface first."""
    processes = []
    # Don't pass interval>0 to cpu_percent — it blocks for `interval` seconds
    # per process. Instead, prime each proc with a 0-interval call (returns
    # the value computed since the previous call, or 0.0 on first call) and
    # rely on the per-proc info we already fetched. This keeps the listing
    # responsive (≈100ms total) instead of 4+ seconds.
    for proc in psutil.process_iter(['pid', 'name', 'exe', 'username',
                                      'memory_info', 'cpu_percent']):
        try:
            info = proc.info
            pid  = info.get('pid')
            name = info.get('name') or ''
            exe  = info.get('exe') or ''
            username = info.get('username') or ''
            mem_info = info.get('memory_info')
            ram = mem_info.rss if mem_info else 0
            cpu = info.get('cpu_percent') or 0.0

            task_type, suspicious, verified = _classify_process(username, exe)

            # Network connection check — only for non-verified procs (saves
            # ~50ms per proc on the verified ones, which is most of them).
            if not verified:
                try:
                    for c in proc.connections(kind='inet'):
                        if c.status == 'ESTABLISHED' and c.raddr:
                            if c.raddr.port in _C2_PORTS:
                                suspicious = True
                                break
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
                except Exception:
                    pass

            processes.append({
                'pid':        pid,
                'name':       name[:64],
                'cpu':        round(float(cpu), 1),
                'mem':        ram,            # back-compat with CLIENT.html
                'ram':        ram,            # new explicit name
                'exe':        exe,
                'username':   username,
                'status':     'running',
                'type':       task_type,
                'suspicious': suspicious,
                'verified':   verified,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception:
            continue

    # Sort by CPU% desc, then by RAM desc, so the hottest procs surface first.
    processes.sort(key=lambda p: (p.get('cpu', 0), p.get('ram', 0)), reverse=True)
    return processes[:80]


def _list_processes_legacy() -> list:
    """Subprocess-based fallback when psutil is not available.
    Returns list of dicts with the minimal fields the client requires.
    Suspicious/verified flags are always False here — the legacy path
    doesn't have enough info to classify reliably."""
    _sys = platform.system()
    procs = []
    try:
        if _sys == 'Linux':
            r = subprocess.run(['ps', 'aux', '--sort=-pcpu'], capture_output=True, text=True, timeout=3)
            for line in r.stdout.strip().split('\n')[1:]:
                parts = line.split(None, 10)
                if len(parts) < 11: continue
                try:
                    procs.append({
                        'pid': int(parts[1]),
                        'name': os.path.basename(parts[10])[:64],
                        'cpu': round(float(parts[2]), 1),
                        'mem': int(float(parts[5]) * 1024),
                        'ram': int(float(parts[5]) * 1024),
                        'exe': '',
                        'username': parts[0],
                        'status': parts[7][:1],
                        'type': 'application',
                        'suspicious': False,
                        'verified': False,
                    })
                except (ValueError, IndexError): continue
        elif _sys == 'Windows':
            r = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=5)
            for line in r.stdout.strip().split('\n'):
                line = line.strip()
                if not line: continue
                try:
                    parts = [p.strip('"') for p in line.split('","')]
                    if len(parts) >= 5:
                        pid = int(parts[1])
                        mem_str = parts[4].replace(',', '').replace(' K', '').replace(' ', '')
                        mem = int(mem_str) * 1024 if mem_str.isdigit() else 0
                        procs.append({
                            'pid': pid,
                            'name': parts[0][:64],
                            'cpu': 0,
                            'mem': mem,
                            'ram': mem,
                            'exe': '',
                            'username': '',
                            'status': 'running',
                            'type': 'application',
                            'suspicious': False,
                            'verified': False,
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
                        'ram': int(float(parts[5]) * 1024),
                        'exe': '',
                        'username': parts[0],
                        'status': parts[7][:1],
                        'type': 'application',
                        'suspicious': False,
                        'verified': False,
                    })
                except (ValueError, IndexError): continue
    except Exception:
        pass
    return procs[:80]


def _list_processes() -> list:
    """List running processes. Uses psutil when available (richer info +
    malware-detection heuristics); falls back to ps/tasklist otherwise."""
    if PSUTIL_AVAILABLE:
        try:
            return _list_processes_psutil()
        except Exception:
            # If psutil blows up at runtime (e.g. /proc unreadable), fall
            # back to the legacy path so the Tasks tab doesn't go blank.
            pass
    return _list_processes_legacy()


def _get_proc_info(pid: int) -> dict:
    """Get detailed info about a single process — used by /tasks/verify.
    Prefers psutil; falls back to /proc + wmic if psutil is missing."""
    info = {
        'pid': pid, 'name': '', 'status': '', 'exe': None, 'cwd': None,
        'cmdline': [], 'username': None, 'create_time': None,
        'suspicious': False, 'warnings': [],
    }
    if PSUTIL_AVAILABLE:
        try:
            p = psutil.Process(pid)
            info['name']        = p.name() or ''
            info['status']      = p.status() or ''
            info['exe']         = p.exe() or None
            info['cwd']         = p.cwd() or None
            info['cmdline']     = p.cmdline() or []
            info['username']    = p.username() or None
            info['create_time'] = p.create_time() or None
            # Reuse the classifier for consistent suspicious/verified flags
            task_type, suspicious, verified = _classify_process(info['username'] or '', info['exe'] or '')
            info['type']       = task_type
            info['suspicious'] = suspicious
            info['verified']   = verified
            return info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return info
        except Exception:
            pass
    # Legacy fallback
    _sys = platform.system()
    try:
        if _sys == 'Linux':
            proc_dir = f'/proc/{pid}'
            if os.path.isdir(proc_dir):
                try:
                    with open(f'{proc_dir}/stat') as f:
                        stat = f.read()
                    comm_start = stat.index('(') + 1
                    comm_end = stat.rindex(')')
                    info['name'] = stat[comm_start:comm_end]
                    state_char = stat[comm_end + 2:comm_end + 3]
                    state_map = {'R': 'running', 'S': 'sleeping', 'D': 'disk-sleep',
                                 'Z': 'zombie', 'T': 'stopped', 't': 'tracing-stop'}
                    info['status'] = state_map.get(state_char, state_char)
                except Exception: pass
                try:
                    info['exe'] = os.readlink(f'{proc_dir}/exe')
                except Exception: pass
                try:
                    info['cwd'] = os.readlink(f'{proc_dir}/cwd')
                except Exception: pass
                try:
                    with open(f'{proc_dir}/cmdline', 'rb') as f:
                        info['cmdline'] = f.read().decode('utf-8', errors='replace').split('\x00')[:-1]
                except Exception: pass
                try:
                    with open(f'{proc_dir}/status') as f:
                        for line in f:
                            if line.startswith('Uid:'):
                                uid = int(line.split()[1])
                                import pwd
                                info['username'] = pwd.getpwuid(uid).pw_name
                                break
                except Exception: pass
                try:
                    info['create_time'] = os.stat(f'{proc_dir}').st_ctime
                except Exception: pass
        elif _sys == 'Windows':
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
    """Kill a process. Returns (success: bool, error_msg: str|None).
    Prefers psutil.Process.terminate(); falls back to os.kill / taskkill."""
    if PSUTIL_AVAILABLE:
        try:
            p = psutil.Process(pid)
            p.terminate()  # graceful SIGTERM
            try:
                p.wait(timeout=3)
            except Exception:
                # Didn't die in time — escalate to kill()
                p.kill()
            return True, None
        except psutil.NoSuchProcess:
            return False, 'process not found'
        except psutil.AccessDenied:
            return False, 'access denied'
        except Exception as e:
            return False, str(e)
    # Legacy fallback
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
