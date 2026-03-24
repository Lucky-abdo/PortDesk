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

Command		Description
check		Run full diagnostics and report issues
fix		Attempt to fix issues with user confirmation
repair		Automatic repair mode (no prompts)
run         	Start server with pre-run checks
diagnose	Run complete system diagnostics
help	        Show help message

This tool helps maintain PortDesk server stability by:
- Checking Python syntax
- Verifying required imports
- Monitoring server runtime
- Applying automatic fixes for known issues
- Providing diagnostic reports
"""

import sys
import io
import os
import subprocess
import importlib.util
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
BASE_DIR = Path(__file__).parent
SERVER_FILE = BASE_DIR / "portdesk-server.py"
CLIENT_FILE = BASE_DIR / "portdesk_client.html"
FIXER_LOG = BASE_DIR / "fixer_log.txt"
SECURITY_FILE = BASE_DIR / "portdesk_security.json"
MACROS_FILE = BASE_DIR / "portdesk_macros.json"
SCHED_FILE = BASE_DIR / "portdesk_scheduled.json"
CERT_FILE = BASE_DIR / "cert.pem"
KEY_FILE = BASE_DIR / "key.pem"
PORT = 5000
PYTHON_MIN_VERSION = (3, 6)

# ====================== Logging ======================
def log(message, level="INFO"):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] {level}: {message}"
    print(log_entry)
    with open(FIXER_LOG, 'a', encoding='utf-8') as f:
        f.write(log_entry + '\n')

# ====================== Backup ======================
def backup_file(filepath):
    """Create backup of a file with timestamp."""
    if not filepath.exists():
        return None
    backup_name = f"{filepath}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(filepath, backup_name)
    log(f"Backed up {filepath} to {backup_name}")
    return backup_name

def backup_configs():
    """Backup all config files."""
    files = [SECURITY_FILE, MACROS_FILE, SCHED_FILE, CERT_FILE, KEY_FILE, SERVER_FILE, CLIENT_FILE]
    backups = []
    for f in files:
        if f.exists():
            backups.append(backup_file(f))
    return backups

# ====================== System Checks ======================
def check_python_version():
    log("Checking Python version...")
    ver = sys.version_info
    if ver >= PYTHON_MIN_VERSION:
        log(f"✅ Python {ver.major}.{ver.minor} meets minimum {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}")
        return True
    log(f"❌ Python {ver.major}.{ver.minor} too old, need {PYTHON_MIN_VERSION[0]}.{PYTHON_MIN_VERSION[1]}+")
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
        log(f"⚠️ Port {PORT} is in use: {e}")
        return False

def check_ssl():
    log("Checking SSL certificates...")
    if CERT_FILE.exists() and KEY_FILE.exists():
        try:
            import ssl
            ctx = ssl.create_default_context()
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            log("✅ SSL certificates present and loadable")
            return True
        except Exception as e:
            log(f"❌ SSL certificates corrupt: {e}")
            return False
    else:
        log("ℹ️ SSL certificates missing, HTTPS not available")
        return False

def check_linux_compatibility():
    """Check for Linux-specific requirements."""
    if platform.system() != 'Linux':
        return []
    errors = []
    if 'DISPLAY' not in os.environ:
        if 'WAYLAND_DISPLAY' in os.environ:
            errors.append('Wayland detected without DISPLAY; run xwayland or use X11 session')
        else:
            errors.append('DISPLAY variable not set; headless mode. Use xvfb-run to start the app.')
    for tool in ['xclip', 'xsel', 'xdotool']:
        if not shutil.which(tool):
            errors.append(f'{tool} not installed; clipboard/automation may fail')
    return errors

# ====================== Dependency Checks ======================
def get_required_packages():
    """Return list of third-party packages that need to be installed via pip."""
    return [
        'flask',
        'flask-socketio',
        'pyautogui',
        'psutil',
        'mss',
        'opencv-python',
        'Pillow',
        'pyperclip',
        'sounddevice',
        'numpy'
    ]

def check_dependencies():
    """Check for required Python packages."""
    log("Checking Python dependencies...")
    missing = []
    # Map import names to pip package names
    import_map = {
        'flask': 'flask',
        'flask_socketio': 'flask-socketio',
        'pyautogui': 'pyautogui',
        'psutil': 'psutil',
        'mss': 'mss',
        'cv2': 'opencv-python',
        'PIL': 'Pillow',
        'pyperclip': 'pyperclip',
        'sounddevice': 'sounddevice',
        'numpy': 'numpy'
    }
    for import_name, pkg_name in import_map.items():
        try:
            # For cv2 and PIL, we need to import them specially
            if import_name == 'cv2':
                __import__('cv2')
            elif import_name == 'PIL':
                __import__('PIL')
            else:
                __import__(import_name)
        except ImportError:
            missing.append(pkg_name)
    if missing:
        log(f"❌ Missing packages: {', '.join(missing)}")
        return False, missing
    else:
        log("✅ All required packages installed")
        return True, []

def install_packages(packages, interactive=True):
    """Attempt to install missing packages via pip."""
    if not packages:
        return True
    log(f"Attempting to install missing packages: {', '.join(packages)}")
    if interactive:
        answer = input("Do you want to install them now? (y/N): ").strip().lower()
        if answer != 'y':
            return False
    # Use pip from same Python interpreter
    pip_cmd = [sys.executable, '-m', 'pip', 'install'] + packages
    try:
        subprocess.run(pip_cmd, check=True, capture_output=True, text=True)
        log("✅ Packages installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        log(f"❌ Installation failed: {e.stderr}")
        return False

# ====================== Config Validation ======================
def check_config_files():
    """Check JSON config files for validity."""
    log("Checking configuration files...")
    files = {
        'security': SECURITY_FILE,
        'macros': MACROS_FILE,
        'scheduled': SCHED_FILE
    }
    issues = []
    for name, path in files.items():
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    json.load(f)
                log(f"✅ {name}.json is valid")
            except json.JSONDecodeError as e:
                log(f"❌ {name}.json corrupt: {e}")
                issues.append((name, path))
        else:
            log(f"ℹ️ {name}.json not found, will create default")
    return issues

def fix_config_file(name, path, default_content):
    """Attempt to fix or create a config file."""
    backup_file(path) if path.exists() else None
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=2)
        log(f"✅ {name}.json fixed/created")
        return True
    except Exception as e:
        log(f"❌ Failed to fix {name}.json: {e}")
        return False

# ====================== Server Syntax Check ======================
def check_syntax():
    log("Checking Python syntax...")
    try:
        import py_compile
        py_compile.compile(str(SERVER_FILE), doraise=True)
        log("✅ Syntax check passed")
        return True
    except py_compile.PyCompileError as e:
        log(f"❌ Syntax error: {e}")
        return False
    except Exception as e:
        log(f"❌ Syntax check failed: {e}")
        return False

# ====================== Process Check ======================
def kill_process_on_port(port):
    """Attempt to kill process using given port."""
    system = platform.system()
    try:
        if system == 'Windows':
            # netstat -ano | findstr :5000
            output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True, text=True)
            lines = output.strip().split('\n')
            pids = set()
            for line in lines:
                parts = line.split()
                if len(parts) >= 5 and 'LISTENING' in line:
                    pid = parts[-1]
                    pids.add(pid)
            if pids:
                for pid in pids:
                    subprocess.run(f'taskkill /F /PID {pid}', shell=True)
                log(f"Killed process(es) on port {port}")
                return True
        else:  # Unix-like
            output = subprocess.check_output(f'lsof -t -i:{port}', shell=True, text=True)
            pids = output.strip().split('\n')
            if pids and pids[0]:
                for pid in pids:
                    os.kill(int(pid), 9)
                log(f"Killed process(es) on port {port}")
                return True
    except subprocess.CalledProcessError:
        # No process found
        pass
    except Exception as e:
        log(f"Failed to kill process: {e}")
    return False

# ====================== Log Analysis ======================
def analyze_server_log():
    """Look for common errors in log files."""
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
                        continue  # not critical
                    if 'error' in entry.get('detail', '').lower():
                        errors.append(entry)
                except:
                    pass
    except Exception as e:
        log(f"Failed to parse log: {e}")
    return errors

# ====================== Server Test Run ======================
def test_server_start(timeout=10):
    """Start server and check if it runs without immediate crash."""
    log(f"Testing server start (timeout {timeout}s)...")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_FILE)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(BASE_DIR),
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        # Wait a bit to see if it crashes
        time.sleep(timeout)
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            log(f"Server crashed with code {proc.returncode}")
            if stderr:
                log(f"Error output: {stderr}")
            return False, stderr
        else:
            log("Server started successfully and stayed alive")
            proc.terminate()
            proc.wait()
            return True, None
    except Exception as e:
        log(f"Test server start failed: {e}")
        return False, str(e)

# ====================== Apply Fixes ======================
def apply_fixes(auto=False):
    """Attempt to fix common issues."""
    log("Applying fixes...")
    fixes_applied = []

    # 1. Syntax errors
    if not check_syntax():
        log("Cannot fix syntax errors automatically. Check server code.")
        return False

    # 2. Missing dependencies
    ok, missing = check_dependencies()
    if not ok and missing:
        if install_packages(missing, interactive=not auto):
            fixes_applied.append(f"Installed packages: {', '.join(missing)}")
        else:
            log("Skipping dependency installation")
    else:
        log("Dependencies OK")

    # 3. Config file fixes
    issues = check_config_files()
    default_configs = {
        'security': {"whitelist": []},
        'macros': {},
        'scheduled': []
    }
    for name, path in issues:
        if fix_config_file(name, path, default_configs[name]):
            fixes_applied.append(f"Fixed {name}.json")
        else:
            log(f"Could not fix {name}.json")

    # 4. Port in use
    if not check_port():
        if auto or input("Port 5000 is busy. Kill process? (y/N): ").strip().lower() == 'y':
            if kill_process_on_port(PORT):
                fixes_applied.append("Killed process using port 5000")
            else:
                log("Could not free port 5000")

    # 5. Linux tools suggestion
    if platform.system() == 'Linux':
        missing_tools = [tool for tool in ['xdotool', 'xclip', 'xsel'] if not shutil.which(tool)]
        if missing_tools:
            log(f"Missing Linux tools: {', '.join(missing_tools)}. Install with: sudo apt install {' '.join(missing_tools)}")
            if not auto:
                if input("Install missing tools? (y/N): ").strip().lower() == 'y':
                    try:
                        subprocess.run(['sudo', 'apt', 'install', '-y'] + missing_tools, check=True)
                        fixes_applied.append(f"Installed Linux tools: {', '.join(missing_tools)}")
                    except:
                        log("Failed to install tools. Run manually.")
            else:
                log("Automatic installation not supported, skipping.")

    # 6. SSL cert generation
    if not check_ssl():
        if auto or input("Generate self-signed SSL certificate? (y/N): ").strip().lower() == 'y':
            gen_cert = BASE_DIR / "gen_cert.py"
            if gen_cert.exists():
                try:
                    subprocess.run([sys.executable, str(gen_cert)], check=True)
                    fixes_applied.append("Generated SSL certificate")
                except Exception as e:
                    log(f"Failed to generate SSL cert: {e}")
            else:
                log("gen_cert.py not found, cannot generate SSL certificate")

    if fixes_applied:
        log(f"✅ Applied fixes: {', '.join(fixes_applied)}")
        return True
    else:
        log("ℹ️ No fixes applied")
        return False

# ====================== Full Diagnostics ======================
def full_diagnostics():
    log("Running full diagnostics...")
    results = {}

    # System
    results['python_version'] = check_python_version()
    results['port'] = check_port()
    results['ssl'] = check_ssl()
    results['syntax'] = check_syntax()
    results['dependencies'] = check_dependencies()[0]
    results['configs'] = len(check_config_files()) == 0

    # Additional
    if platform.system() == 'Linux':
        linux_issues = check_linux_compatibility()
        results['linux_tools'] = (len(linux_issues) == 0)
        if linux_issues:
            log("⚠️ Linux issues:\n  " + "\n  ".join(linux_issues))
    else:
        results['linux_tools'] = True

    # Test run
    server_ok, server_err = test_server_start()
    results['server_start'] = server_ok
    if server_err:
        log(f"Server error: {server_err}")

    # Log analysis
    errors = analyze_server_log()
    results['log_errors'] = len(errors) == 0
    if errors:
        log(f"⚠️ Found {len(errors)} error entries in log")

    passed = sum(results.values())
    total = len(results)

    log(f"Diagnostics complete: {passed}/{total} checks passed")

    if passed == total:
        log("🎉 All checks passed! Server should be working.")
    else:
        log("⚠️ Some issues found. Use 'fix' or 'repair' to attempt fixes.")
        for key, val in results.items():
            if not val:
                log(f"  - Failed: {key}")

    return results

# ====================== Repair (Full auto fix) ======================
def repair():
    """Attempt to automatically fix as many issues as possible."""
    log("Starting repair mode (automatic fixes where possible)...")
    backup_configs()
    fixes = apply_fixes(auto=True)
    # After fixes, run diagnostics again
    log("Re-running diagnostics after fixes...")
    results = full_diagnostics()
    if all(results.values()):
        log("🎉 Repair successful! Server should be fully operational.")
    else:
        log("⚠️ Some issues remain. Manual intervention may be required.")
    return results

# ====================== Command Line Interface ======================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()
    if command == 'check':
        full_diagnostics()
    elif command == 'fix':
        apply_fixes(auto=False)
    elif command == 'run':
        # Run server with monitoring (existing functionality)
        log("Running server with pre-run checks...")
        ok, _ = test_server_start(timeout=3)
        if ok:
            log("Server seems ready. Starting normally...")
            # Start normally (original run_server from old fixer)
            subprocess.run([sys.executable, str(SERVER_FILE)])
        else:
            log("Pre-run check failed. Consider running 'fix' or 'repair'.")
    elif command == 'diagnose':
        full_diagnostics()
    elif command == 'repair':
        repair()
    elif command == 'help':
        print(__doc__)
    else:
        print(f"Unknown command: {command}")
        print("Available: check, fix, run, diagnose, repair, help")

if __name__ == "__main__":
    main()