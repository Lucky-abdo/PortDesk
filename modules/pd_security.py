# ──────────────────────────────────────────────────────────────────────────────
# pd_security — extracted security CORE for PortDesk (refactor)
#
# Scope (deliberately narrow): only the *self-contained* security primitives whose
# state is fully owned here are moved into this module:
#   • PIN nonce single-use guard      (_used_pin_nonces)
#   • HTTP rate limiting              (_req_counts)
#   • Multi-IP attack detection       (_unknown_attempts + ATTACK_THRESHOLD/WINDOW)
#   • HMAC challenge-response verify   (reads injected `security` dict)
#   • whitelist / blacklist checks     (reads injected `security` dict)
#
# Deliberately LEFT in the server (cross-cutting "glue", high coupling, low gain):
#   • _trigger_lockdown / _approve_ip / _prompt_add_ip   (orchestrate stream/manager/state)
#   • _require_active_pin                                  (touches session state)
#   • _load/_save/_restore_security                        (bootstrap + watcher + argparse + _HMAC_SECRET)
#
# DEPENDENCY INJECTION: the server owns the mutable `security` dict and `_sec_lock`.
# We hold the SAME references (not copies) so writes from the server's watcher /
# approve / save paths and reads here always see one shared object. Call
# configure(security, sec_lock) exactly once at import time.
#
# Behaviour is byte-for-byte identical to the original inline implementation.
# ──────────────────────────────────────────────────────────────────────────────
import time
import threading
import hashlib
import hmac as _hmac_mod
from collections import defaultdict

import pd_config as _pd_config
BASE_DIR = _pd_config.BASE_DIR

# ── Injected shared state (set by configure()) ────────────────────────────────
_security = None          # the shared `security` dict (same reference as server)
_sec_lock = None          # the shared `_sec_lock` (same reference as server)


def configure(security, sec_lock):
    """Wire up the shared mutable security state. Must be called once before use."""
    global _security, _sec_lock
    _security = security
    _sec_lock = sec_lock


# ── Tunables (identical to original) ──────────────────────────────────────────
ATTACK_THRESHOLD = 5
ATTACK_WINDOW    = 30

# ── PIN nonce single-use guard ────────────────────────────────────────────────
_used_pin_nonces: dict = {}
_used_pin_nonces_lock  = threading.Lock()


def _check_and_consume_nonce(nonce: str, ip: str) -> bool:
    key = f'{ip}:{nonce}'
    now = time.time()
    expiry = now + 60
    with _used_pin_nonces_lock:
        expired = [k for k, exp in list(_used_pin_nonces.items()) if exp < now]
        for k in expired:
            del _used_pin_nonces[k]
        if key in _used_pin_nonces:
            return False
        _used_pin_nonces[key] = expiry
        return True


# ── HTTP rate limiting ────────────────────────────────────────────────────────
# NOTE: _reject_counts stays in the server — it belongs to approve/prompt
# orchestration (which is NOT moved) and no function here touches it.
_req_counts = defaultdict(list)


def _is_rate_limited(ip):
    # Limit raised from 7 → 20 per 30 s.
    # A fresh page open (or reconnect after disconnect) fires ~8-12 HTTP requests
    # in rapid succession (WS upgrade, /auth/get_client_key, /security/fingerprint,
    # /security/whitelist, /monitors/list, /flags/status, …).  The old limit of 7
    # was routinely hit on every page load, which returned 429 to the WS upgrade
    # and produced the "connection lost → rate limited on refresh" symptom.
    # 20 requests per 30 s still blocks genuine floods while allowing normal use.
    now, window, limit = time.time(), 30, 20
    with _sec_lock:
        _req_counts[ip] = [t for t in _req_counts[ip] if now - t < window]
        if len(_req_counts[ip]) >= limit: return True
        _req_counts[ip].append(now)
    return False


def cleanup_req_counts():
    """Drop stale per-IP request-count entries (called periodically by the
    server's security file watcher). Mirrors the original inline cleanup."""
    cutoff = time.time() - 10
    with _sec_lock:
        stale = [ip for ip, ts in list(_req_counts.items()) if not ts or ts[-1] < cutoff]
        for ip in stale:
            del _req_counts[ip]


# ── Multi-IP attack detection ─────────────────────────────────────────────────
_unknown_attempts      = []
_unknown_attempts_lock = threading.Lock()


def _record_unknown_attempt(ip):
    now = time.time()
    with _unknown_attempts_lock:
        _unknown_attempts.append((now, ip))
        recent = [(t, i) for t, i in _unknown_attempts if now - t <= ATTACK_WINDOW]
        _unknown_attempts[:] = recent
        unique_ips = {i for _, i in recent}
        if len(unique_ips) >= ATTACK_THRESHOLD:
            return True
    return False


# ── HMAC challenge-response verification ──────────────────────────────────────
def _hmac_verify(challenge: str, response: str, ip: str) -> bool:
    """Verify HMAC-SHA256(derived_key, challenge) == response.
    derived_key = HMAC(master_secret, 'portdesk-client:{ip}') — same derivation as /auth/get_client_key."""
    with _sec_lock:
        master = _security.get('hmac_secret', '')
    if not master:
        return False
    derived = _hmac_mod.new(master.encode(), f'portdesk-client:{ip}'.encode(), hashlib.sha256).hexdigest()
    expected = _hmac_mod.new(derived.encode(), challenge.encode(), hashlib.sha256).hexdigest()
    return _hmac_mod.compare_digest(expected, response)


# ── whitelist / blacklist checks ──────────────────────────────────────────────
def _is_blacklisted(ip):
    with _sec_lock:
        return ip in _security.get("blacklist", [])


def _is_allowed(ip):
    if ip in ('127.0.0.1', '::1', 'localhost'): return True
    with _sec_lock:
        if ip in _security.get("blacklist", []): return False
        return ip in _security.get("whitelist", [])
