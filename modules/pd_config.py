# ──────────────────────────────────────────────────────────────────────────────
# pd_config — IMMUTABLE configuration constants for PortDesk (refactor)
#
# SCOPE (deliberately narrow — see REFACTOR_README "الموجة 4"):
# Only constants that are written ONCE at import time and thereafter READ-ONLY
# live here. They are safe to move because there is no runtime mutation, so no
# snapshot/aliasing hazard (Pattern 65).
#
# Moved here:
#   • Paths:     BASE_DIR, DATA_DIR, LOG_FILE, SCHED_FILE, MACROS_FILE
#   • Tunables:  PIN_MAX_TRIES, PIN_LOCKOUT_STEPS, MACRO_TIMEOUT,
#                _MAX_LOG_SIZE, _SEC_BACKUP_COUNT
#
# DELIBERATELY LEFT in the server (MUTABLE at runtime — moving them would risk a
# silent live-update break, because int/bool can't be shared by reference):
#   • FLAG_*           (~120 reads, written live by /flags/update + argparse)
#   • SECURITY_FILE    (rewritten by --whitelist argparse)
#   • STUN_SERVERS     (appended by --stun argparse)
# The correct long-term fix for FLAG_* is a single `cfg` object (a redesign,
# not an extraction). Documented in REFACTOR_README.
#
# NOTE: BASE_DIR is derived from THIS module's __file__. Because pd_config.py
# sits in the same directory as portdesk_server.py, BASE_DIR is identical to the
# original `os.path.dirname(os.path.abspath(__file__))` computed in the server.
# ──────────────────────────────────────────────────────────────────────────────
import os

# ── Paths ─────────────────────────────────────────────────────────────────────
# ── Flexible BASE_DIR detection ─────────────────────────────────────────────
def _find_project_root():
    """
    Find the project root intelligently.
    Priority:
      1. Directory of the running main script (SERVER.py)
      2. Directory of this pd_config.py
      3. Current working directory
    This allows users to reorganize files freely.
    """
    import sys
    main_file = None
    if hasattr(sys, 'argv') and sys.argv:
        main_file = sys.argv[0]
    elif '__main__' in sys.modules:
        main_file = getattr(sys.modules['__main__'], '__file__', None)

    if main_file:
        root = os.path.dirname(os.path.abspath(main_file))
        # Walk up until we find typical PortDesk files
        while root and not any(
            os.path.exists(os.path.join(root, f)) for f in
            ['SERVER.py', 'portdesk-server.py', 'CLIENT.html', 'portdesk_client.html']
        ):
            parent = os.path.dirname(root)
            if parent == root:
                break
            root = parent
        if root:
            return root

    # Fallback
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _find_project_root()
DATA_DIR = os.path.join(BASE_DIR, "portdesk_data")

# Ensure the data directory exists with owner-only permissions (identical to the
# original inline bootstrap in the server). Done once at import.
os.makedirs(DATA_DIR, exist_ok=True)
try:
    os.chmod(DATA_DIR, 0o700)   # owner read/write/execute only
except Exception:
    pass

LOG_FILE    = os.path.join(DATA_DIR, "portdesk_events.log")
SCHED_FILE  = os.path.join(DATA_DIR, "portdesk_scheduled.json")
MACROS_FILE = os.path.join(DATA_DIR, "portdesk_macros.json")

# ── Tunables (immutable) ──────────────────────────────────────────────────────
PIN_MAX_TRIES     = 3
PIN_LOCKOUT_STEPS = [60, 180, 300, 600, 1800, 3600]
MACRO_TIMEOUT     = 30                 # seconds max per macro run
_MAX_LOG_SIZE     = 10 * 1024 * 1024   # 10 MB before rotating to .1
_SEC_BACKUP_COUNT = 3

# ── Client HTML discovery (supports renaming + reorganization) ────────────────
def get_client_html_path():
    """
    Find the client HTML file even if the user renamed it or reorganized folders.
    Searches common locations and common names.
    """
    candidates = [
        os.path.join(BASE_DIR, "CLIENT.html"),
        os.path.join(BASE_DIR, "client.html"),
        os.path.join(BASE_DIR, "portdesk_client.html"),
        os.path.join(BASE_DIR, "static", "CLIENT.html"),
        os.path.join(BASE_DIR, "web", "CLIENT.html"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            return path

    # Last resort: recursive search (slow but works for unusual layouts)
    for root, _, files in os.walk(BASE_DIR):
        for f in files:
            if f.lower() in ("client.html", "portdesk_client.html"):
                return os.path.join(root, f)

    return None
