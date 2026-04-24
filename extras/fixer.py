#!/usr/bin/env python3
"""
PortDesk Fixer - Server Repair and Diagnostics Tool
Automatically diagnoses and fixes common issues with the PortDesk server.

### When to Use
- After modifying server code
- When server fails to start
- For routine maintenance
- Before deployment

### Limitations
- Cannot fix all possible issues
- Some fixes require manual intervention
- Advanced problems may need developer attention

### Log File
All fixer activities are logged to `fixer_log.txt` for troubleshooting.

Usage:
python fixer.py [command]

Command         Description
check           Run full diagnostics and report issues
fix             Attempt to fix issues with user confirmation
repair          Automatic repair mode (no prompts)
run             Start server with pre-run checks
diagnose        Run complete system diagnostics
help            Show help message
"""

import sys
import io
import os
import subprocess
import time
import json
import shutil
import socket as net_socket
import platform
import logging
from pathlib import Path
from datetime import datetime

# ====================== Fix console encoding for Windows ======================
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ====================== Constants ======================
BASE_DIR    = Path(__file__).parent
SERVER_FILE = BASE_DIR / "portdesk-server.py"
CLIENT_FILE = BASE_DIR / "portdesk_client.html"
FIXER_LOG   = BASE_DIR / "fixer_log.txt"
SECURITY_FILE = BASE_DIR / "portdesk_security.json"
MACROS_FILE   = BASE_DIR / "portdesk_macros.json"
SCHED_FILE    = BASE_DIR / "portdesk_scheduled.json"
CERT_FILE     = BASE_DIR / "cert.pem"
KEY_FILE      = BASE_DIR / "key.pem"
PORT = 5000
PYTHON_MIN_VERSION = (3, 8)

# ====================== Logging ======================
def log(message, level="INFO"):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    entry = f"[{timestamp}] {level}: {message}"
    print(entry)
    with open(FIXER_LOG, 'a', encoding='utf-8') as f:
        f.write(entry + '\n')

# ====================== Backup ======================
def backup_file(filepath):
    if not filepath.exists():
        return None
    backup_name = f"{filepath}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup_name)
    log(f"Backed up {filepath.name}")
    return backup_name

def backup_configs():
    files = [SECURITY_FILE, MACROS_FILE, SCHED_FILE, CERT_FILE, KEY_FILE, SERVER_FILE, CLIENT_FILE]
    return [backup_file(f) for f in files if f.exists()]

# ====================== System Checks ======================
def check_python_version():
    log("Checking Python version...")
    ver = sys.version_info
    if ver >= PYTHON_MIN_VERSION:
        log(f"✅ Python {ver.major}.{ver.minor} OK (minimum {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]})")
        return True
    log(f"❌ Python {ver.major}.{ver.minor} too old — need {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+")
    return False

def check_port():
    log(f"Checking port {PORT}...")
    sock = net_socket.socket(net_socket.AF_INET, net_socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', PORT))
        sock.close()
        log(f"✅ Port {PORT} is free")
        return True
    except net_socket.error as e:
        log(f"⚠️ Port {PORT} in use: {e}")
        return False

def check_ssl():
    log("Checking SSL certificates...")
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            import ssl
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            log("✅ SSL certificates valid")
            return True
        except Exception as e:
            log(f"❌ SSL certificates corrupt: {e}")
            return False
    log("ℹ️ SSL certificates missing — HTTPS disabled")
    return False

def check_linux_compatibility():
    if platform.system() != 'Linux':
        return []
    errors = []
    if 'DISPLAY' not in os.environ:
        if 'WAYLAND_DISPLAY' in os.environ:
            errors.append('Wayland detected without DISPLAY; run XWayland or use X11 session')
        else:
            errors.append('DISPLAY not set; headless mode — use xvfb-run')
    for tool in ['xclip', 'xsel', 'xdotool']:
        if not shutil.which(tool):
            errors.append(f'{tool} not installed — clipboard/automation may fail')
    return errors

# ====================== Dependency Checks ======================
REQUIRED_PACKAGES = {
    'fastapi':    'fastapi',
    'uvicorn':    'uvicorn',
    'starlette':  'starlette',
    'pyautogui':  'pyautogui',
    'psutil':     'psutil',
    'mss':        'mss',
    'cv2':        'opencv-python',
    'PIL':        'Pillow',
    'pyperclip':  'pyperclip',
    'sounddevice':'sounddevice',
    'numpy':      'numpy',
}

OPTIONAL_PACKAGES = {
    'dxcam':      'dxcam',
    'turbojpeg':  'PyTurboJPEG',
    'aiortc':     'aiortc',
}

def check_dependencies():
    log("Checking required packages...")
    missing = []
    for import_name, pkg_name in REQUIRED_PACKAGES.items():
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pkg_name)
    if missing:
        log(f"❌ Missing packages: {', '.join(missing)}")
        return False, missing
    log("✅ All required packages installed")

    log("Checking optional packages...")
    for import_name, pkg_name in OPTIONAL_PACKAGES.items():
        try:
            __import__(import_name)
            log(f"  ✅ {pkg_name}")
        except ImportError:
            log(f"  ℹ️ {pkg_name} not installed (optional — performance enhancement)")
    return True, []

def install_packages(packages, interactive=True):
    if not packages:
        return True
    log(f"Attempting to install: {', '.join(packages)}")
    if interactive:
        answer = input("Install now? (y/N): ").strip().lower()
        if answer != 'y':
            return False
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install'] + packages, check=True, capture_output=True, text=True)
        log("✅ Packages installed")
        return True
    except subprocess.CalledProcessError as e:
        log(f"❌ Install failed: {e.stderr}")
        return False

# ====================== Config Validation ======================
def check_config_files():
    log("Checking config files...")
    files = {'security': SECURITY_FILE, 'macros': MACROS_FILE, 'scheduled': SCHED_FILE}
    issues = []
    for name, path in files.items():
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    json.load(f)
                log(f"✅ {name}.json valid")
            except json.JSONDecodeError as e:
                log(f"❌ {name}.json corrupt: {e}")
                issues.append((name, path))
        else:
            log(f"ℹ️ {name}.json not found — will create default")
    return issues

def fix_config_file(name, path, default_content):
    backup_file(path) if path.exists() else None
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=2)
        log(f"✅ {name}.json fixed")
        return True
    except Exception as e:
        log(f"❌ Failed to fix {name}.json: {e}")
        return False

# ====================== Syntax Check ======================
def check_syntax():
    log("Checking Python syntax...")
    try:
        import py_compile
        py_compile.compile(str(SERVER_FILE), doraise=True)
        log("✅ Syntax OK")
        return True
    except py_compile.PyCompileError as e:
        log(f"❌ Syntax error: {e}")
        return False
    except Exception as e:
        log(f"❌ Syntax check failed: {e}")
        return False

# ====================== HTML Client Check ======================
def check_client_file():
    log("Checking client HTML...")
    if not CLIENT_FILE.exists():
        log("❌ portdesk_client.html not found")
        return False
    size = CLIENT_FILE.stat().st_size
    if size < 10000:
        log(f"⚠️ Client file suspiciously small ({size} bytes)")
        return False
    log(f"✅ Client HTML found ({size//1024} KB)")
    return True

# ====================== Process Management ======================
def kill_process_on_port(port):
    system = platform.system()
    try:
        if system == 'Windows':
            output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True, text=True)
            pids = set()
            for line in output.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 5 and 'LISTENING' in line:
                    pids.add(parts[-1])
            for pid in pids:
                subprocess.run(f'taskkill /F /PID {pid}', shell=True)
            if pids:
                log(f"Killed process(es) on port {port}")
                return True
        else:
            output = subprocess.check_output(f'lsof -t -i:{port}', shell=True, text=True)
            for pid in output.strip().split('\n'):
                if pid:
                    os.kill(int(pid), 9)
            log(f"Killed process(es) on port {port}")
            return True
    except subprocess.CalledProcessError:
        pass
    except Exception as e:
        log(f"Failed to kill process: {e}")
    return False

# ====================== Log Analysis ======================
def analyze_server_log():
    log_file = BASE_DIR / "portdesk_events.log"
    if not log_file.exists():
        return []
    errors = []
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get('type') in ['pin_fail', 'task_kill']:
                        continue
                    if 'error' in entry.get('detail', '').lower():
                        errors.append(entry)
                except:
                    pass
    except Exception as e:
        log(f"Failed to parse log: {e}")
    return errors

# ====================== Server Test ======================
def test_server_start(timeout=10):
    log(f"Testing server start (timeout {timeout}s)...")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_FILE)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(BASE_DIR), text=True, encoding='utf-8', errors='replace'
        )
        time.sleep(timeout)
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            log(f"❌ Server crashed (code {proc.returncode})")
            if stderr: log(f"Error: {stderr[:500]}")
            return False, stderr
        log("✅ Server started and stayed alive")
        proc.terminate(); proc.wait()
        return True, None
    except Exception as e:
        log(f"❌ Test failed: {e}")
        return False, str(e)

# ====================== Apply Fixes ======================
def apply_fixes(auto=False):
    log("Applying fixes...")
    fixes_applied = []

    if not check_syntax():
        log("Cannot auto-fix syntax errors — check server code manually")
        return False

    ok, missing = check_dependencies()
    if not ok and missing:
        if install_packages(missing, interactive=not auto):
            fixes_applied.append(f"Installed: {', '.join(missing)}")

    issues = check_config_files()
    defaults = {'security': {"whitelist": [], "blacklist": []}, 'macros': {}, 'scheduled': []}
    for name, path in issues:
        if fix_config_file(name, path, defaults[name]):
            fixes_applied.append(f"Fixed {name}.json")

    if not check_port():
        if auto or input("Port 5000 busy — kill process? (y/N): ").strip().lower() == 'y':
            if kill_process_on_port(PORT):
                fixes_applied.append("Freed port 5000")

    if platform.system() == 'Linux':
        missing_tools = [t for t in ['xdotool', 'xclip', 'xsel'] if not shutil.which(t)]
        if missing_tools:
            log(f"ℹ️ Missing Linux tools: {', '.join(missing_tools)}")
            log(f"  Install with: sudo apt install {' '.join(missing_tools)}")
            if not auto and input("Install now? (y/N): ").strip().lower() == 'y':
                try:
                    subprocess.run(['sudo', 'apt', 'install', '-y'] + missing_tools, check=True)
                    fixes_applied.append(f"Installed: {', '.join(missing_tools)}")
                except:
                    log("Failed — run manually")

    if not check_ssl():
        if auto or input("Generate self-signed SSL certificate? (y/N): ").strip().lower() == 'y':
            gen_cert = BASE_DIR / "gen_cert.py"
            if gen_cert.exists():
                try:
                    subprocess.run([sys.executable, str(gen_cert)], check=True)
                    fixes_applied.append("Generated SSL certificate")
                except Exception as e:
                    log(f"SSL generation failed: {e}")
            else:
                log("gen_cert.py not found")

    if fixes_applied:
        log(f"✅ Applied: {', '.join(fixes_applied)}")
    else:
        log("ℹ️ No fixes needed")
    return bool(fixes_applied)

# ====================== Full Diagnostics ======================
def full_diagnostics():
    log("Running full diagnostics...")
    results = {
        'python_version': check_python_version(),
        'port':           check_port(),
        'ssl':            check_ssl(),
        'syntax':         check_syntax(),
        'dependencies':   check_dependencies()[0],
        'configs':        len(check_config_files()) == 0,
        'client_file':    check_client_file(),
    }

    if platform.system() == 'Linux':
        issues = check_linux_compatibility()
        results['linux_tools'] = len(issues) == 0
        if issues:
            log("⚠️ Linux issues:\n  " + "\n  ".join(issues))
    else:
        results['linux_tools'] = True

    server_ok, server_err = test_server_start()
    results['server_start'] = server_ok
    if server_err:
        log(f"Server error: {server_err[:300]}")

    errors = analyze_server_log()
    results['log_errors'] = len(errors) == 0
    if errors:
        log(f"⚠️ {len(errors)} error entries in log")

    passed = sum(results.values())
    total  = len(results)
    log(f"Diagnostics: {passed}/{total} passed")

    if passed == total:
        log("🎉 All checks passed!")
    else:
        log("⚠️ Issues found:")
        for k, v in results.items():
            if not v:
                log(f"  ✗ {k}")
    return results

# ====================== Repair ======================
def repair():
    log("Starting auto-repair...")
    backup_configs()
    apply_fixes(auto=True)
    log("Re-running diagnostics after repair...")
    results = full_diagnostics()
    if all(results.values()):
        log("🎉 Repair successful!")
    else:
        log("⚠️ Some issues remain — manual intervention needed")
    return results

# ====================== CLI ======================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1].lower()
    if cmd in ('check', 'diagnose'):
        full_diagnostics()
    elif cmd == 'fix':
        apply_fixes(auto=False)
    elif cmd == 'repair':
        repair()
    elif cmd == 'run':
        log("Pre-run check...")
        ok, _ = test_server_start(timeout=3)
        if ok:
            log("Starting server...")
            subprocess.run([sys.executable, str(SERVER_FILE)])
        else:
            log("Pre-run check failed — run 'fix' or 'repair' first")
    elif cmd == 'help':
        print(__doc__)
    else:
        print(f"Unknown command: {cmd}")
        print("Available: check, fix, run, diagnose, repair, help")

if __name__ == "__main__":
    main()
