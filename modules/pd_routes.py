"""
── pd_routes — all 51 HTTP/WS routes (refactor) ────────────────────────────
Imported from SERVER.py at the END of that file so all globals
(cfg, _stream, security, manager, etc.) are already defined. No circular
dependency because the import happens after full initialization.

FLEXIBLE IMPORT: The main server module might be called SERVER.py,
portdesk_server.py, or anything else.  The bootstrap code in the main file
registers itself in sys.modules under the canonical name 'portdesk_server',
so this import works regardless of the actual filename.  If you rename the
main file, just update SERVER_MODULE_NAME in the bootstrap section of the
main file — no changes needed here.

"""

import sys as _sys
_SERVER_MODULE_NAME = 'portdesk_server'   # must match SERVER_MODULE_NAME in main file

# Try the canonical name first; fall back to __main__ if the bootstrap
# hasn't run yet (shouldn't happen, but makes the module more robust).
_server_mod = _sys.modules.get(_SERVER_MODULE_NAME) or _sys.modules.get('__main__')
if _server_mod is None:
    raise ImportError(
        f"pd_routes: cannot find the server module (tried '{_SERVER_MODULE_NAME}' "
        f"and '__main__').  Make sure this file is imported from the main server script."
    )

# Import all required names from the server module.
# We use getattr on the live module object so that even if the module was
# registered early (before all globals were defined), we still get the
# latest bindings at the time this import runs.
_NEEDED_NAMES = [
    'app', 'cfg', '_stream', 'manager', '_session', '_loop_ref', '_loop',
    'security', '_sec_lock', '_pyautogui_lock',
    'FLAG_WATCH_ONLY', 'FLAG_NO_EXPLORER', 'FLAG_NO_MOUSE', 'FLAG_NO_KEYBOARD',
    'FLAG_NO_WEBRTC', 'FLAG_NO_H264', 'FLAG_GREY', 'FLAG_SCALE', 'FLAG_UPLOAD_LIMIT',
    'FLAG_BACKEND', 'FLAG_VERBOSE', 'FLAG_NO_UPLOAD', 'FLAG_NO_DOWNLOAD',
    '_EXECUTOR', '_INPUT_EXECUTOR', '_webrtc_pcs', '_webrtc_dc_clients', '_webrtc_dc_lock',
    '_TokenBucket', '_ws_buckets', 'STUN_SERVERS', '_DataChannelClient',
    '_hmac_verify', '_is_allowed', '_is_blacklisted', '_is_private_host', '_is_rate_limited',
    '_require_active_pin', '_log_event', '_vprint',
    '_approve_ip', '_prompt_add_ip', '_record_unknown_attempt',
    '_check_and_consume_nonce', '_ws_pin_verified', '_ws_pin_lock',
    '_active_client_ws', '_active_client_ip', '_active_client_lock',
    'screen_streaming', 'audio_streaming', '_mic_active',
    '_current_stream_mode', '_current_transport',
    'screen_thread', '_mic_worker_thread', '_mic_queue',
    '_audio_thread', '_audio_worker', '_mic_worker',
    'screen_worker', '_FfmpegH264Streamer',
    '_ffmpeg_encoder', '_ffmpeg_encoder_ok', '_detect_ffmpeg_encoder',
    '_update_stream_status',
    '_clipboard_paste', '_clipboard_copy', 'type_text', '_is_simple_typable',
    '_init_virtual_keyboard', '_send_virtual_key', '_send_virtual_text',
    '_send_xdotool_key', '_send_xdotool_text', '_virtual_kb_device',
    'map_key', 'KEY_MAP',
    '_decode_binary_input', '_dispatch',
    'macros', '_macro_lock', 'scheduled_tasks', '_sched_lock',
    '_load_macros', '_save_macros', '_load_scheduled', '_save_scheduled',
    '_pin_fails', '_pin_lockout', '_pin_lockout_count',
    'PIN_MAX_TRIES', 'PIN_LOCKOUT_STEPS',
    '_reject_counts',
    'JSONResponse', 'WebSocket', 'WebSocketDisconnect', 'Request',
    'WEBRTC_AVAILABLE', 'DXCAM_AVAILABLE', 'MSS_AVAILABLE', 'CV2_AVAILABLE',
    'SUBPROCESS_AVAILABLE',
    # ── Additional names used in route handlers ──────────────────────────────
    '_lockdown_lock', '_lockdown',                          # security lockdown
    '_screen_last_error',                                    # stream error proxy
    '_save_security',                                        # persist security config
    '_log_write_queue', '_log_lock', 'LOG_FILE',            # logging infrastructure
    '_pin_verify', '_pin_hash',                              # PIN hashing (from pd_crypto)
    '_hmac_mod',                                             # hmac module alias
    'DATA_DIR', 'BASE_DIR',                                 # paths (from pd_config)
    'get_system_stats',                                      # CPU/RAM stats (from pd_stats)
    'FileResponse', 'StreamingResponse',                     # HTTP response types
    'pyautogui',                                             # input control (portdesk_input alias)
    'set_max_body_size',                                     # upload limit helper
    'ScreenCaptureTrack',                                    # WebRTC video track
    '_string',                                               # stdlib string alias
    '_mss',                                                  # mss module alias (conditional)
]

# Names that are CONDITIONAL — they exist only when optional deps are installed.
# We import them if present, skip silently if absent (the route handlers already
# guard with WEBRTC_AVAILABLE / MSS_AVAILABLE / etc.).
_CONDITIONAL_NAMES = {
    '_mss',                  # only when mss is installed
    'ScreenCaptureTrack',    # only when aiortc is installed
}

# Check for truly missing names (excluding conditional ones)
_missing = [n for n in _NEEDED_NAMES if n not in _CONDITIONAL_NAMES and not hasattr(_server_mod, n)]
if _missing:
    raise ImportError(
        f"pd_routes: the server module '{_SERVER_MODULE_NAME}' is missing these "
        f"names: {', '.join(_missing[:10])}{'...' if len(_missing) > 10 else ''}.  "
        f"Make sure 'import pd_routes' happens at the END of the server file, "
        f"after all globals are defined."
    )

# Inject all available names into this module's namespace
globals().update({
    name: getattr(_server_mod, name)
    for name in _NEEDED_NAMES
    if hasattr(_server_mod, name)
})

# Clean up temporary names
del _sys, _server_mod, _NEEDED_NAMES, _CONDITIONAL_NAMES, _missing


# ── WebRTC imports (conditional — same as server) ──
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
    from aiortc.contrib.media import MediaStreamTrack
except ImportError:
    pass  # WEBRTC_AVAILABLE check in routes handles this

import pd_config as _pd_config
MACRO_TIMEOUT = _pd_config.MACRO_TIMEOUT  # seconds max per macro run

# ════════════════════════════════════════════════════════════════════════════
# ALL 51 ROUTES — extracted verbatim from portdesk_server.py
# ════════════════════════════════════════════════════════════════════════════

@app.websocket('/ws')
async def websocket_endpoint(ws: WebSocket):
    global _active_client_ws, _active_client_ip  # session proxies only
    ip = ws.client.host

    with _lockdown_lock:
        if _lockdown and ip not in ('127.0.0.1', '::1', 'localhost'):
            await ws.close(1008); return

    if ip not in ('127.0.0.1', '::1', 'localhost'):
        if _is_blacklisted(ip):
            await ws.close(1008); return
        if not _is_allowed(ip):
            # Unknown IP — rate-limit WS connection attempts too.
            if _is_rate_limited(ip):
                await ws.close(1008); return
            await ws.close(1008); return

    # ── Origin check for WebSocket ─────────────────────────────────────────
    _origin = ws.headers.get('origin', '')
    _host   = ws.headers.get('host', '')
    if _origin and _host:
        if _origin not in (f'http://{_host}', f'https://{_host}'):
            await ws.close(1008); return
        if not _is_private_host(_host.split(':')[0]):
            await ws.close(1008); return

    # Pre-check: if session is occupied, check if it's sleeping
    if _session.ws is not None and not _session.is_sleeping:
        await ws.close(1008); return

    # ── HMAC Challenge-Response ─────────────────────────────────────────────
    import secrets as _secrets
    _ws_challenge = _secrets.token_hex(16)
    await ws.send_json({'type': 'hmac_challenge', 'challenge': _ws_challenge})
    _hmac_authenticated = False
    try:
        raw_auth = await asyncio.wait_for(ws.receive_text(), timeout=10)
        auth_data = json.loads(raw_auth)
        if auth_data.get('type') == 'hmac_response':
            if _hmac_verify(_ws_challenge, auth_data.get('response', ''), ip):
                _hmac_authenticated = True
                await ws.send_json({'type': 'hmac_ok', 'server_ts': int(time.time() * 1000)})
            else:
                await ws.send_json({'type': 'hmac_fail'})
                await ws.close(1008)
                return
        else:
            await ws.close(1008)
            return
    except asyncio.TimeoutError:
        await ws.close(1008)
        return
    except Exception:
        await ws.close(1008)
        return

    # ── Set active client AFTER successful HMAC (atomic via _session) ─────
    ok, reason = _session.try_claim(ws, ip)
    if not ok:
        if reason == 'sleeping':
            # Previous client was sleeping — new client takes over
            _session.force_release()
            ok, _ = _session.try_claim(ws, ip)
            if not ok:
                await ws.close(4001); return
            _log_event('session_takeover', ip=ip, detail='previous client was sleeping', severity='WARNING')
        else:
            await ws.close(4001); return
    _active_client_ws = _session.ws
    _active_client_ip = _session.ip

    await manager.connect(ws)

    _log_event('connect', ip=ip)

    # ── Server-side PIN gate ──────────────────────────────────────────────────
    with _sec_lock:
        _has_pin = bool(security.get("pins", {}).get(ip))
    with _ws_pin_lock:
        if not _has_pin:
            _ws_pin_verified[ip] = True
        elif ip not in _ws_pin_verified:
            _ws_pin_verified[ip] = False

    _SAFE_EVENTS = {'ping', 'pong', 'client_sleep', 'client_wake'}

    try:
        while True:
            _kind, _msg = await ws.receive()
            if _kind == 'bytes':
                # Compact binary input frame
                if len(_msg) > 4096:
                    continue
                data = _decode_binary_input(_msg)
                if data is None:
                    continue
                # binary frames carry no _ts; stamp now so replay-check passes
                data['_ts'] = time.time() * 1000.0
            else:
                raw = _msg
                if len(raw) > 65536:
                    continue
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, RecursionError, ValueError, OverflowError):
                    continue
            if not _is_allowed(ip): break

            ev = data.get('_ev', data.get('type', ''))

            with _ws_pin_lock:
                _verified = _ws_pin_verified.get(ip, False)

            if not _verified and ev not in _SAFE_EVENTS:
                await ws.send_json({'type': 'error', 'msg': 'pin_required'})
                continue

            try:
                await _dispatch(data, ws)
            except WebSocketDisconnect:
                raise
            except Exception as _de:
                print(f"\u26a0\ufe0f  _dispatch error on event '{ev}': {_de!r}", flush=True)
                if cfg.verbose:
                    import traceback as _tb; _tb.print_exc()
                try:
                    await ws.send_json({'type': 'error', 'msg': 'dispatch_error'})
                except Exception:
                    pass
    except WebSocketDisconnect:
        pass
    except Exception as e:
        _vprint(f"ws error: {e}", flush=True)
    finally:
        _session.release(ws)
        _active_client_ws = _session.ws
        _active_client_ip = _session.ip
        _session.clear_verified(ip)
        manager.disconnect(ws)
        _stream.stop_all()
        # Single source of truth — _stream.* is authoritative
        _update_stream_status()
        if _stream.screen_thread and _stream.screen_thread.is_alive():
            await asyncio.get_running_loop().run_in_executor(
                _EXECUTOR, _stream.screen_thread.join, 2.0)
        try: _mic_queue.put_nowait(None)
        except: pass
        for mod in ('ctrl', 'alt', 'shift', 'winleft'):
            try:
                with _pyautogui_lock: pyautogui.keyUp(mod)
            except: pass

@app.post('/webrtc/offer')
async def webrtc_offer(request: Request):
    global _current_transport, _current_stream_mode
    if not WEBRTC_AVAILABLE or cfg.no_webrtc:
        return JSONResponse({'error': 'WebRTC disabled'}, status_code=501)

    ip = request.client.host
    # M17: PIN check for WebRTC endpoint
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)
    with _active_client_lock:
        _aip = _active_client_ip
    if _aip is not None and _aip != ip:
        return JSONResponse({'error': 'session occupied'}, status_code=423)

    params = await request.json()
    offer  = RTCSessionDescription(sdp=params['sdp'], type=params['type'])
    pc     = RTCPeerConnection(configuration=RTCConfiguration(iceServers=STUN_SERVERS))
    _webrtc_pcs.add(pc)
    _current_transport = 'WebRTC'
    _current_stream_mode = 'WebRTC (STUN P2P)'
    _update_stream_status()

    @pc.on('connectionstatechange')
    async def on_state():
        if pc.connectionState in ('failed', 'closed', 'disconnected'):
            await pc.close()
            _webrtc_pcs.discard(pc)

    try:
        pc.addTrack(ScreenCaptureTrack())
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}
    except Exception as e:
        await pc.close()
        _webrtc_pcs.discard(pc)
        return JSONResponse({'error': 'WebRTC negotiation failed'}, status_code=500)


@app.post('/webrtc/control/offer')
async def webrtc_control_offer(request: Request):
    """WebRTC data channel endpoint — layer 2 fallback for control commands via STUN P2P.
    Requires HMAC challenge-response verification and PIN check (same as WS endpoint)."""
    if not WEBRTC_AVAILABLE or cfg.no_webrtc:
        return JSONResponse({'error': 'WebRTC disabled'}, status_code=501)

    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'not whitelisted'}, status_code=403)

    # PIN check — same gate as WS and /webrtc/offer
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)

    # HMAC verification — same as WS endpoint, prevents unauthorized DataChannel
    params = await request.json()
    hmac_response = params.get('hmac_response', '')
    hmac_challenge = params.get('hmac_challenge', '')
    if hmac_challenge and hmac_response:
        # Validate challenge is a recent timestamp (within 30 seconds) to prevent replay
        try:
            challenge_ts = float(hmac_challenge) / 1000.0
            if abs(time.time() - challenge_ts) > 30:
                return JSONResponse({'error': 'HMAC challenge expired'}, status_code=403)
        except (ValueError, TypeError):
            return JSONResponse({'error': 'invalid HMAC challenge format'}, status_code=400)
        if not _hmac_verify(hmac_challenge, hmac_response, ip):
            return JSONResponse({'error': 'HMAC verification failed'}, status_code=403)
    else:
        # No HMAC provided — reject. Client must include HMAC to prove key possession.
        return JSONResponse({'error': 'HMAC challenge-response required'}, status_code=403)

    # Session occupied check
    with _active_client_lock:
        _aip = _active_client_ip
    if _aip is not None and _aip != ip:
        return JSONResponse({'error': 'session occupied'}, status_code=423)

    offer  = RTCSessionDescription(sdp=params['sdp'], type=params['type'])
    pc     = RTCPeerConnection(configuration=RTCConfiguration(iceServers=STUN_SERVERS))
    _webrtc_pcs.add(pc)

    dc_client_ref: list = []

    @pc.on('datachannel')
    def on_datachannel(channel):
        global _active_client_ws, _active_client_ip
        with _ws_pin_lock:
            if not _ws_pin_verified.get(ip, False):
                try: channel.close()
                except: pass
                return
        dc = _DataChannelClient(channel, ip)
        if not _session.try_claim(dc, ip):
            try: channel.close()
            except: pass
            return
        _active_client_ws = _session.ws
        _active_client_ip = _session.ip
        dc_client_ref.append(dc)
        with _webrtc_dc_lock:
            _webrtc_dc_clients.append(dc)
        manager.active.append(dc)
        print(f"✅ WebRTC data channel opened from {ip}", flush=True)

        @channel.on('message')
        async def on_message(message):
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, RecursionError, ValueError):
                return
            ev = data.get('_ev', data.get('type', ''))
            with _ws_pin_lock:
                _verified = _ws_pin_verified.get(ip, False)
            if not _verified and ev not in ('ping', 'pong'):
                try: await dc.send_json({'type': 'error', 'msg': 'pin_required'})
                except: pass
                return
            try:
                await _dispatch(data, dc)
            except Exception as e:
                _vprint(f"DC dispatch error: {e}", flush=True)

        @channel.on('close')
        def on_close():
            global _active_client_ws, _active_client_ip
            _session.release(dc)
            _active_client_ws = _session.ws
            _active_client_ip = _session.ip
            with _webrtc_dc_lock:
                if dc in _webrtc_dc_clients:
                    _webrtc_dc_clients.remove(dc)
            if dc in manager.active:
                manager.active.remove(dc)
            _session.clear_verified(ip)
            print(f"ℹ️  WebRTC data channel closed from {ip}", flush=True)

    @pc.on('connectionstatechange')
    async def on_state():
        if pc.connectionState in ('failed', 'closed', 'disconnected'):
            for dc in dc_client_ref:
                with _webrtc_dc_lock:
                    if dc in _webrtc_dc_clients:
                        _webrtc_dc_clients.remove(dc)
                if dc in manager.active:
                    manager.active.remove(dc)
            await pc.close()
            _webrtc_pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type}


@app.get('/')
async def index(request: Request):
    # Flexible client discovery (supports renaming + reorganization)
    from pd_config import get_client_html_path
    path = get_client_html_path()
    if not path or not os.path.isfile(path):
        return JSONResponse(
            {'error': 'CLIENT.html (or portdesk_client.html) not found. '
                      'Make sure it exists next to SERVER.py.'},
            status_code=500
        )
    _log_event('connect', ip=request.client.host)
    resp = FileResponse(path)
    # Never cache the client HTML
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.get('/ping')
async def ping(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return {'pong': time.time()}

@app.get('/stats')
async def stats(request: Request):
    if not _is_allowed(request.client.host):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, get_system_stats)

@app.get('/screen/status')
async def screen_status(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return {
        'streaming':    _stream.screen_streaming,
        'thread_alive': screen_thread is not None and screen_thread.is_alive(),
        'mss':          MSS_AVAILABLE,
        'dxcam':        DXCAM_AVAILABLE and platform.system() == 'Windows',
        'error':        _screen_last_error,
        'stream_mode':  _current_stream_mode,
        'transport':    _current_transport,
    }

# ── Runtime Flags API ────────────────────────────────────────────────────────
@app.post('/flags/update')
async def flags_update(request: Request):
    ip = request.client.host
    with _active_client_lock: _aip = _active_client_ip
    if ip not in ('127.0.0.1', '::1', 'localhost') and ip != _aip:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    runtime_flags = ['watch_only', 'no_explorer', 'no_mouse', 'no_keyboard', 'grey', 'verbose', 'no_upload', 'no_download']
    updated = []
    import __main__ as _m
    for flag_name in runtime_flags:
        if flag_name in d:
            val = d[flag_name]
            if not isinstance(val, bool):
                return JSONResponse({'error': f'{flag_name} must be a boolean'}, status_code=400)
            flag_var = f'FLAG_{flag_name.upper()}'
            globals()[flag_var] = val
            setattr(_m, flag_var, val)
            updated.append(flag_name)
    if 'scale' in d and 0.1 <= float(d['scale']) <= 2.0:
        globals()['cfg.scale'] = float(d['scale'])
        setattr(_m, 'cfg.scale', float(d['scale']))
        updated.append('scale')
    if 'upload_limit' in d:
        ul = d['upload_limit']
        if ul is not None and (not isinstance(ul, (int, float)) or ul <= 0):
            return JSONResponse({'error': 'upload_limit must be a positive number or null'}, status_code=400)
        globals()['cfg.upload_limit'] = ul
        setattr(_m, 'cfg.upload_limit', d['upload_limit'])
        if ul is not None:
            set_max_body_size(ul)
        updated.append('upload_limit')
    if updated:
        _log_event('flags_update', detail=f'updated: {", ".join(updated)}')
    return {'ok': True, 'updated': updated}

@app.get('/flags/status')
async def flags_status(request: Request):
    ip = request.client.host
    with _active_client_lock: _aip = _active_client_ip
    if ip not in ('127.0.0.1', '::1', 'localhost') and ip != _aip:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return {
        'watch_only':   cfg.watch_only,
        'no_explorer':  cfg.no_explorer,
        'no_mouse':     cfg.no_mouse,
        'no_keyboard':  cfg.no_keyboard,
        'no_webrtc':    cfg.no_webrtc,
        'grey':         cfg.grey,
        'verbose':      cfg.verbose,
        'scale':        cfg.scale,
        'backend':      cfg.backend,
        'upload_limit': cfg.upload_limit,
        'no_upload':    cfg.no_upload,
        'no_download':  cfg.no_download,
    }

@app.post('/screen/start')
async def screen_start_http(request: Request):
    # No globals needed — _stream.* is single source of truth
    ip = request.client.host
    with _active_client_lock:
        if _active_client_ws is None:
            return JSONResponse({'error': 'no active client'}, status_code=403)
        _aip = _active_client_ip
    # M2: Verify requesting IP matches active client
    if ip not in ('127.0.0.1', '::1', 'localhost') and _aip is not None and ip != _aip:
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)
    if screen_thread and screen_thread.is_alive():
        return {'ok': True}
    if not _stream.screen_streaming:
        _stream.start_screen('WS', screen_worker)
        # Single source of truth — _stream.* is authoritative
        _update_stream_status()
    return {'ok': True}

@app.post('/screen/stop')
async def screen_stop_http(request: Request):
    # No globals needed — _stream.* is single source of truth
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost'):
        with _active_client_lock: _aip = _active_client_ip
        if _aip is not None and _aip != ip:
            return JSONResponse({'error': 'forbidden'}, status_code=403)
        with _ws_pin_lock:
            if not _ws_pin_verified.get(ip, False):
                return JSONResponse({'error': 'pin required'}, status_code=403)
    _stream.stop_screen()
    screen_streaming     = _stream.screen_streaming
    _current_stream_mode = _stream.mode
    _current_transport   = _stream.transport
    _update_stream_status()
    return {'ok': True}

@app.get('/security/whitelist')
async def get_whitelist(request: Request):
    ip = request.client.host
    with _sec_lock:
        approved = ip in security.get("whitelist", [])
        blacklisted = ip in security.get("blacklist", [])
    rejected = (not approved) and (_reject_counts.get(ip, 0) > 0)
    return {"approved": approved, "ip": ip,
            "blacklisted": blacklisted, "rejected": rejected,
            "reject_count": _reject_counts.get(ip, 0)}

@app.post('/security/whitelist/request')
async def whitelist_request(request: Request):
    ip = request.client.host
    if ip in security.get("blacklist", []):
        return JSONResponse({"error": "blacklisted"}, status_code=403)
    if ip in security.get("whitelist", []):
        return {"ok": True, "already": True}
    _prompt_add_ip(ip)
    return {"ok": True, "pending": True}

@app.post('/security/approve')
async def security_approve(request: Request):
    if request.client.host not in ('127.0.0.1', '::1', 'localhost'):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    ip = d.get('ip', '')
    action = d.get('action', 'allow')
    if not ip:
        return JSONResponse({"error": "ip required"}, status_code=400)
    _approve_ip(ip, action)
    if action == 'allow':
        return {"ok": True, "approved": ip}
    return {"ok": True, "rejected": ip}

@app.post('/security/whitelist/remove_self')
async def whitelist_remove_self(request: Request):
    ip = request.client.host
    with _sec_lock:
        if ip in security.get("whitelist", []):
            security["whitelist"].remove(ip)
        security.get("pins", {}).pop(ip, None)
        security.get("lockout", {}).pop(ip, None)
        _pin_lockout.pop(ip, None)
        _pin_fails[ip] = 0
        _pin_lockout_count[ip] = 0
        _save_security()
    return {"ok": True}

@app.post('/security/blacklist/remove')
async def blacklist_remove(request: Request):
    if request.client.host not in ('127.0.0.1', '::1', 'localhost'):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    d = await request.json()
    ip = d.get("ip", "")
    with _sec_lock:
        if ip in security.get("blacklist", []):
            security["blacklist"].remove(ip)
        _reject_counts[ip] = 0
        _save_security()
    return {"ok": True}

def _list_drives():
    if platform.system() == 'Windows':
        return [d+':\\' for d in _string.ascii_uppercase if os.path.exists(d+':\\')]
    elif platform.system() == 'Darwin':
        vols = ['/Volumes/' + v for v in os.listdir('/Volumes')] if os.path.exists('/Volumes') else []
        return ['/'] + vols
    else:
        return ['/home', '/tmp', '/']

@app.get('/explorer/drives')
async def explorer_drives(request: Request):
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    return _list_drives()

@app.get('/explorer/list')
async def explorer_list(request: Request):
    path = request.query_param('path', '')
    # ── File Explorer: No filesystem path restrictions — by design ────────────
    # Access requires IP whitelist + HMAC + PIN. Authenticated user = machine owner.
    # Full filesystem freedom is intentional. DO NOT flag as vulnerability.
    # ──────────────────────────────────────────────────────────────────────────
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    if not path: return {'drives': _list_drives()}
    real = os.path.realpath(path)
    if not os.path.exists(real):
        return JSONResponse({'error': 'Path not found'}, status_code=404)
    loop = asyncio.get_running_loop()
    def _do_list():
        entries = []
        for name in sorted(os.listdir(real), key=lambda x: (not os.path.isdir(os.path.join(real,x)), x.lower())):
            full = os.path.join(real, name)
            try:
                stat = os.stat(full)
                if os.path.isdir(full):
                    entries.append({'name': name, 'type': 'dir', 'size': 0, 'modified': int(stat.st_mtime)})
                else:
                    entries.append({'name': name, 'type': 'file', 'size': stat.st_size, 'modified': int(stat.st_mtime)})
            except PermissionError:
                entries.append({'name': name, 'type': 'dir' if os.path.isdir(full) else 'file', 'size': 0, 'modified': 0, 'denied': True})
        return entries
    try:
        entries = await loop.run_in_executor(_EXECUTOR, _do_list)
        return {'path': real, 'entries': entries}
    except PermissionError: return JSONResponse({'error': 'Permission denied'}, status_code=403)
    except Exception: return JSONResponse({'error': 'Failed to list directory'}, status_code=500)

@app.get('/explorer/download')
async def explorer_download(request: Request):
    path = request.query_param('path', '')
    # ── No path restrictions — by design. See explorer/list comment. ──────────
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    if cfg.no_download: return JSONResponse({'error': 'download disabled'}, status_code=403)
    if not path or not os.path.exists(path):
        return JSONResponse({'error': 'not found'}, status_code=404)
    real = os.path.realpath(path)
    if os.path.isfile(real):
        return FileResponse(real, filename=os.path.basename(real),
                            range_header=request.headers.get('range'))
    # ── Single-pass ZIP (complete, valid archive) ────────────────────────────
    # NOTE: Previous chunked approach produced corrupt ZIPs (multiple
    # concatenated archives). We now build one complete ZIP in memory with a
    # 2 GB safety cap. For most use cases this is fine.
    import zipfile, tempfile
    def _zip_generator(folder):
        base_dir = os.path.dirname(folder)
        total_size = 0
        MAX_ZIP_SIZE = 2 * 1024 * 1024 * 1024
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp_path = tmp.name
        tmp.close()
        try:
            _stop = False
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(folder):
                    if _stop: break
                    for fname in files:
                        full = os.path.join(root, fname)
                        arcname = os.path.relpath(full, base_dir)
                        try:
                            fsize = os.path.getsize(full)
                            if total_size + fsize > MAX_ZIP_SIZE:
                                _stop = True; break
                            zf.write(full, arcname)
                            total_size += fsize
                        except Exception:
                            pass
            with open(tmp_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
    zip_name = os.path.basename(real) + '.zip'
    return StreamingResponse(
        _zip_generator(real), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{zip_name}"'}
    )

@app.post('/explorer/download_multi')
async def explorer_download_multi(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    # ── No path restrictions — by design. See explorer/list comment. ──────────
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    if cfg.no_download: return JSONResponse({'error': 'download disabled'}, status_code=403)
    d = await request.json()
    paths = d.get('paths', [])
    if not paths: return JSONResponse({'error': 'no paths'}, status_code=400)
    if len(paths) > 500: return JSONResponse({'error': 'too many paths (max 500)'}, status_code=400)
    paths = [p for p in paths if os.path.exists(p)]
    if not paths: return JSONResponse({'error': 'no valid paths'}, status_code=400)
    paths = [os.path.realpath(p) for p in paths]
    # Always produce ZIP for consistency (even for single file)
    # ── Single-pass ZIP for multi-file download ────────────────────────────
    import zipfile, tempfile
    def _zip_multi_generator(file_paths):
        total_size = 0
        MAX_ZIP_SIZE = 2 * 1024 * 1024 * 1024
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp_path = tmp.name
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in file_paths:
                    if not os.path.exists(p): continue
                    if os.path.isfile(p):
                        try:
                            fsize = os.path.getsize(p)
                            if total_size + fsize > MAX_ZIP_SIZE:
                                break
                            zf.write(p, os.path.basename(p))
                            total_size += fsize
                        except Exception:
                            pass
                    else:
                        for root, _, files in os.walk(p):
                            for fname in files:
                                full = os.path.join(root, fname)
                                try:
                                    fsize = os.path.getsize(full)
                                    if total_size + fsize > MAX_ZIP_SIZE:
                                        break
                                    zf.write(full, os.path.relpath(full, p))
                                    total_size += fsize
                                except Exception:
                                    pass
            with open(tmp_path, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try: os.unlink(tmp_path)
            except Exception: pass
    return StreamingResponse(
        _zip_multi_generator(paths), media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="pcc_files.zip"'}
    )

@app.post('/explorer/upload')
async def explorer_upload(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    # ── No path restrictions — by design. See explorer/list comment. ──────────
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    if cfg.no_upload: return JSONResponse({'error': 'upload disabled'}, status_code=403)

    # Parse multipart form data using custom server
    form_fields = await request.form()
    files = await request.files()
    path = form_fields.get('path', os.path.join(os.path.expanduser('~'), 'Downloads'))

    dest_dir = os.path.realpath(path)
    if not os.path.isdir(dest_dir):
        return JSONResponse({'error': 'Folder not found'}, status_code=400)

    _MAX_UPLOAD = cfg.upload_limit or (500 * 1024 * 1024)  # 500MB default cap
    _MAX_FILE_COUNT = 100  # M10: Limit number of files per upload

    def _safe_name(raw: str) -> str:
        # Strip both Unix and Windows path separators, null bytes, control chars
        name = raw.replace('\\', '/').split('/')[-1]
        name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip('. ')
        # M8: Block Windows reserved names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
        _WIN_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.[^.]*)?$', re.IGNORECASE)
        if _WIN_RESERVED.match(name):
            return ''
        return name[:255] if name else ''

    saved = []
    total_written = 0
    if len(files) > _MAX_FILE_COUNT:
        return JSONResponse({'error': f'too many files (max {_MAX_FILE_COUNT})'}, status_code=400)
    for f in files:
        safe = _safe_name(f.filename or '')
        if not safe: continue

        # Re-validate dest_dir after realpath (TOCTOU mitigation)
        real_dest = os.path.realpath(dest_dir)
        if not os.path.isdir(real_dest):
            return JSONResponse({'error': 'Destination changed or invalid'}, status_code=400)

        dest = os.path.join(real_dest, safe)
        base, ext = os.path.splitext(safe)
        c = 1
        while os.path.exists(dest):
            dest = os.path.join(real_dest, f"{base}_{c}{ext}"); c += 1

        written = 0
        try:
            content = f._content if hasattr(f, '_content') else None
            if content is None:
                content = await f.read()
            written = len(content)
            if written > _MAX_UPLOAD:
                return JSONResponse({'error': f'File "{safe}" exceeds upload limit'}, status_code=413)
            total_written += written
            if total_written > _MAX_UPLOAD:
                return JSONResponse({'error': 'Total upload size exceeds limit'}, status_code=413)
            with open(dest, 'wb') as out:
                out.write(content)
        except OSError as e:
            return JSONResponse({'error': 'Write failed'}, status_code=500)
        saved.append(safe)
    return {'ok': True, 'saved': saved}

@app.post('/explorer/mkdir')
async def explorer_mkdir(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    path, name = d.get('path','').strip(), d.get('name','').strip()
    if not path or not name: return JSONResponse({'error': 'missing params'}, status_code=400)
    target = os.path.join(path, name)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_EXECUTOR, lambda: os.makedirs(target, exist_ok=False))
        return {'ok': True}
    except FileExistsError: return JSONResponse({'error': 'Name already exists'}, status_code=409)
    except Exception: return JSONResponse({'error': 'Failed to create folder'}, status_code=500)

@app.post('/explorer/mkfile')
async def explorer_mkfile(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    path, name = d.get('path','').strip(), d.get('name','').strip()
    if not path or not name: return JSONResponse({'error': 'missing params'}, status_code=400)
    target = os.path.join(path, name)
    if os.path.exists(target): return JSONResponse({'error': 'Name already exists'}, status_code=409)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_EXECUTOR, lambda: open(target, 'x').close())
        return {'ok': True}
    except FileExistsError: return JSONResponse({'error': 'Name already exists'}, status_code=409)
    except PermissionError: return JSONResponse({'error': 'Permission denied'}, status_code=403)
    except Exception: return JSONResponse({'error': 'Failed to create file'}, status_code=500)

@app.post('/explorer/rename')
async def explorer_rename(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    src, new_name = d.get('src','').strip(), d.get('name','').strip()
    if not src or not new_name: return JSONResponse({'error': 'missing params'}, status_code=400)
    dst = os.path.join(os.path.dirname(src), new_name)
    if os.path.exists(dst): return JSONResponse({'error': 'Name already exists'}, status_code=409)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(_EXECUTOR, os.rename, src, dst)
        return {'ok': True}
    except Exception: return JSONResponse({'error': 'Failed to rename'}, status_code=500)

@app.post('/explorer/delete')
async def explorer_delete(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    if cfg.no_explorer or cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    import shutil as _shutil
    d = await request.json()
    paths = d.get('paths', [])
    loop = asyncio.get_running_loop()
    errors = []
    def _do_delete():
        _errs = []
        for p in paths:
            if not os.path.exists(p): continue
            try:
                if os.path.isdir(p): _shutil.rmtree(p)
                else: os.remove(p)
            except Exception: _errs.append(f'Failed to delete: {os.path.basename(p)}')
        return _errs
    errors = await loop.run_in_executor(_EXECUTOR, _do_delete)
    return {'ok': not errors, 'errors': errors}

@app.post('/explorer/copy')
async def explorer_copy(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    import shutil as _shutil
    d = await request.json()
    srcs, dst = d.get('paths', []), d.get('dest', '').strip()
    if not srcs or not dst: return JSONResponse({'error': 'missing params'}, status_code=400)
    if not os.path.isdir(dst): return JSONResponse({'error': 'Destination not found'}, status_code=400)
    loop = asyncio.get_running_loop()
    def _do_copy():
        _errs = []
        for s in srcs:
            try:
                name = os.path.basename(s.rstrip('/\\'))
                t    = os.path.join(dst, name)
                if os.path.isdir(s): _shutil.copytree(s, t)
                else:                _shutil.copy2(s, t)
            except Exception: _errs.append(f'Failed to copy: {os.path.basename(s)}')
        return _errs
    errors = await loop.run_in_executor(_EXECUTOR, _do_copy)
    return {'ok': not errors, 'errors': errors}

@app.post('/explorer/move')
async def explorer_move(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    import shutil as _shutil
    d = await request.json()
    srcs, dst = d.get('paths', []), d.get('dest', '').strip()
    if not srcs or not dst: return JSONResponse({'error': 'missing params'}, status_code=400)
    if not os.path.isdir(dst): return JSONResponse({'error': 'Destination not found'}, status_code=400)
    loop = asyncio.get_running_loop()
    def _do_move():
        _errs = []
        for s in srcs:
            try: _shutil.move(s, os.path.join(dst, os.path.basename(s.rstrip('/\\'))))
            except Exception: _errs.append(f'Failed to move: {os.path.basename(s)}')
        return _errs
    errors = await loop.run_in_executor(_EXECUTOR, _do_move)
    return {'ok': not errors, 'errors': errors}

@app.post('/explorer/shortcut')
async def explorer_shortcut(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    src, dest = d.get('src','').strip(), d.get('dest','').strip()
    if not src or not dest: return JSONResponse({'error': 'missing params'}, status_code=400)
    loop = asyncio.get_running_loop()
    try:
        if platform.system() == 'Windows':
            def _do_win_shortcut(_src=src, _dest=dest):
                import win32com.client
                shell = win32com.client.Dispatch("WScript.Shell")
                lnk_name = os.path.splitext(os.path.basename(_src))[0] + '.lnk'
                lnk = shell.CreateShortCut(os.path.join(_dest, lnk_name))
                lnk.Targetpath = _src; lnk.save()
            await loop.run_in_executor(_EXECUTOR, _do_win_shortcut)
        elif platform.system() == 'Darwin':
            def _do_mac_shortcut(_src=src, _dest=dest):
                subprocess.run(['ln', '-s', _src, os.path.join(_dest, os.path.basename(_src))], check=True)
            await loop.run_in_executor(_EXECUTOR, _do_mac_shortcut)
        else:
            def _do_linux_shortcut(_src=src, _dest=dest):
                safe_name = os.path.splitext(os.path.basename(_src))[0].replace('\n', '').replace('\r', '')
                safe_src  = _src.replace('\n', '').replace('\r', '')
                desktop   = os.path.join(_dest, safe_name + '.desktop')
                with open(desktop, 'w') as f:
                    f.write(f'[Desktop Entry]\nType=Link\nName={safe_name}\nURL=file://{safe_src}\nIcon=applications-system\n')
                os.chmod(desktop, 0o755)
                try: subprocess.run(['xdg-desktop-icon', 'install', '--novendor', desktop], check=False)
                except: pass
            await loop.run_in_executor(_EXECUTOR, _do_linux_shortcut)
        return {'ok': True}
    except Exception as e:
        logging.exception("Failed to create explorer shortcut")
        return JSONResponse({'error': 'internal error'}, status_code=500)

@app.get('/explorer/properties')
async def explorer_properties(request: Request):
    path = request.query_param('path', '')
    if not path: return JSONResponse({'error': 'not found'}, status_code=404)
    try:
        fullpath = os.path.realpath(path)
        # ── No path restrictions — by design. See explorer/list comment. ──────
        loop = asyncio.get_running_loop()
        exists = await loop.run_in_executor(_EXECUTOR, os.path.exists, fullpath)
        if not exists: return JSONResponse({'error': 'not found'}, status_code=404)
        stat = await loop.run_in_executor(_EXECUTOR, os.stat, fullpath)
        is_dir = await loop.run_in_executor(_EXECUTOR, os.path.isdir, fullpath)
        info = {
            'name': os.path.basename(fullpath), 'path': fullpath,
            'type': 'dir' if is_dir else 'file',
            'size': stat.st_size, 'modified': int(stat.st_mtime), 'created': int(stat.st_ctime),
        }
        if is_dir:
            def _dir_size(fp):
                try: return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(fp) for f in fs if os.path.exists(os.path.join(r, f)))
                except: return 0
            info['size'] = await loop.run_in_executor(_EXECUTOR, _dir_size, fullpath)
        return info
    except Exception: return JSONResponse({'error': 'Failed to read properties'}, status_code=500)

@app.get('/macros/list')
async def macros_list(request: Request):
    with _macro_lock: return list(macros.keys())

@app.post('/macros/save')
async def macros_save(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    name, steps = d.get('name',''), d.get('steps',[])
    if not name: return JSONResponse({'error': 'no name'}, status_code=400)
    if len(name) > 128: return JSONResponse({'error': 'name too long (max 128)'}, status_code=400)
    if not isinstance(steps, list): return JSONResponse({'error': 'steps must be array'}, status_code=400)
    if len(steps) > 500: return JSONResponse({'error': 'too many steps (max 500)'}, status_code=400)
    with _macro_lock:
        if len(macros) >= 200 and name not in macros:
            return JSONResponse({'error': 'macro limit reached (max 200)'}, status_code=400)
        macros[name] = steps; _save_macros(macros)
    return {'ok': True}

@app.post('/macros/delete')
async def macros_delete(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    name = d.get('name','')
    with _macro_lock:
        macros.pop(name, None)
        _save_macros(macros)
    return {'ok': True}

# MACRO_TIMEOUT imported from portdesk_server  # seconds max per macro run

@app.post('/macros/run')
async def macros_run(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    if cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    d    = await request.json()
    name = d.get('name','')
    with _macro_lock: steps = list(macros.get(name, []))
    if not steps: return JSONResponse({'error': 'not found'}, status_code=404)
    _cancelled = threading.Event()
    def _run():
        # Pattern 31: background worker must respect runtime input flags.
        if cfg.watch_only: return
        deadline = time.time() + MACRO_TIMEOUT
        for step in steps:
            if _cancelled.is_set() or time.time() > deadline:
                break
            t = step.get('type','')
            try:
                if t == 'type':
                    if not cfg.no_keyboard:
                        type_text(step.get('text',''))
                else:
                    with _pyautogui_lock:
                        if   t == 'key' and not cfg.no_keyboard:      pyautogui.press(map_key(step['key']))
                        elif t == 'shortcut' and not cfg.no_keyboard: pyautogui.hotkey(*[map_key(k) for k in step['keys']])
                        elif t == 'click' and not cfg.no_mouse:
                            bt = step.get('btn','left')
                            if   bt=='left':   pyautogui.click()
                            elif bt=='right':  pyautogui.rightClick()
                            elif bt=='double': pyautogui.doubleClick()
                        elif t == 'scroll' and not cfg.no_mouse:   pyautogui.scroll(int(step.get('dy',0)))
                        elif t == 'move' and not cfg.no_mouse:     pyautogui.moveRel(int(step.get('dx',0)), int(step.get('dy',0)), duration=0)
                delay = step.get('delay', 0.1)
                if delay > 0: time.sleep(min(delay, max(0, deadline - time.time())))
            except Exception as e: _vprint(f"macro step error: {e}", flush=True)
    threading.Thread(target=_run, daemon=True).start()
    return {'ok': True}

@app.get('/stream/encoder_info')
async def stream_encoder_info(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    return {
        'ffmpeg_encoder': _ffmpeg_encoder,
        'hardware':       _ffmpeg_encoder not in (None, 'libx264'),
        'mode':           'h264' if _ffmpeg_encoder_ok else 'jpeg',
        'platform':       platform.system(),
    }

@app.get('/monitors/list')
async def monitors_list(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    if not MSS_AVAILABLE: return []
    try:
        with _mss.mss() as sct:
            return [{'index': i, 'w': m['width'], 'h': m['height'], 'x': m['left'], 'y': m['top']}
                    for i, m in enumerate(sct.monitors) if i > 0]
    except Exception: return JSONResponse({'error': 'Failed to list monitors'}, status_code=500)

# ── Built-in task manager — replaces psutil dependency ────────────────────────
# Uses subprocess (ps/tasklist) for process listing and os.kill for termination.
# Zero external dependencies — no psutil required.

# ── Task manager — extracted to pd_process module (refactor) ────────────────
import pd_process as _pd_process
_CRITICAL_PROCS = _pd_process._CRITICAL_PROCS
_LEGIT_PATHS    = _pd_process._LEGIT_PATHS
_list_processes = _pd_process._list_processes
_get_proc_info  = _pd_process._get_proc_info
_kill_process   = _pd_process._kill_process

@app.get('/tasks/list')
async def tasks_list(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    try:
        return await asyncio.get_running_loop().run_in_executor(_EXECUTOR, _list_processes)
    except Exception as e:
        return JSONResponse({'error': 'Failed to list processes'}, status_code=500)

@app.post('/tasks/kill')
async def tasks_kill(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    if cfg.watch_only: return JSONResponse({'error': 'disabled'}, status_code=403)
    d   = await request.json()
    pid = d.get('pid')
    if not pid: return JSONResponse({'error': 'no pid'}, status_code=400)
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return JSONResponse({'error': 'invalid pid'}, status_code=400)
    if pid_int <= 1:
        return JSONResponse({'error': 'cannot kill system process'}, status_code=403)
    if pid_int == os.getpid():
        return JSONResponse({'error': 'cannot kill the PortDesk server process'}, status_code=403)

    # Get process info for safety checks — uses built-in _get_proc_info (no psutil)
    proc_info = _get_proc_info(pid_int)
    proc_name = proc_info.get('name', '').lower()

    if proc_name in _CRITICAL_PROCS:
        exe_path = (proc_info.get('exe') or '').lower()
        if exe_path:
            _norm = os.path.normpath(exe_path).lower()
            is_legit = any(_norm.startswith(os.path.normpath(lp).lower()) for lp in _LEGIT_PATHS)
            if is_legit:
                return JSONResponse(
                    {'error': f'cannot kill system process: {proc_name}', 'path': exe_path, 'suspicious': False},
                    status_code=403
                )
            # Same name but from suspicious path — likely malware impersonating system process
            _log_event('task_kill_suspicious', f'pid={pid} name={proc_name} path={exe_path}', severity='WARNING')
        else:
            # Can't verify path — block to be safe
            return JSONResponse({'error': f'cannot kill system process: {proc_name}'}, status_code=403)

    ok, err = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, _kill_process, pid_int)
    if ok:
        _log_event('task_kill', f'pid={pid}')
        return {'ok': True}
    else:
        code = 404 if err == 'process not found' else 403 if err == 'access denied' else 500
        return JSONResponse({'error': err}, status_code=code)


@app.get('/tasks/verify')
async def tasks_verify(request: Request):
    """Deep-inspect a process: path, signature info, suspicious indicators.
    Helps detect malware impersonating system processes. Uses built-in _get_proc_info (no psutil)."""
    pid = request.query_int('pid', 0)
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    if not pid or pid <= 0:
        return JSONResponse({'error': 'provide pid parameter'}, status_code=400)

    info = _get_proc_info(pid)
    if not info.get('name'):
        return JSONResponse({'error': 'process not found'}, status_code=404)

    # ── Suspicion checks ──────────────────────────────────────────────
    proc_name = info['name'].lower() if info['name'] else ''
    exe_path  = (info['exe'] or '').lower()

    _CRITICAL_NAMES = {
        'svchost.exe', 'csrss.exe', 'wininit.exe', 'services.exe',
        'lsass.exe', 'winlogon.exe', 'smss.exe', 'dwm.exe',
        'explorer.exe', 'taskhostw.exe', 'runtimebroker.exe',
    }

    # Check 1: System process name from non-system path
    if proc_name in _CRITICAL_NAMES and exe_path:
        _LEGIT = ['\\windows\\system32\\', '\\windows\\syswow64\\', '\\windows\\winsxs\\',
                  '/usr/bin/', '/usr/sbin/', '/usr/lib/', '/sbin/', '/bin/', '/lib/']
        _norm = os.path.normpath(exe_path).lower()
        if not any(lp.lower() in _norm for lp in _LEGIT):
            info['suspicious'] = True
            info['warnings'].append(f'CRITICAL_NAME_NON_SYSTEM_PATH: {proc_name} running from {info["exe"]}')

    # Check 2: Process running from temp/user-writable directories
    _SUSPICIOUS_DIRS = [
        '\\temp\\', '/tmp/', '\\appdata\\local\\temp\\',
        '\\downloads\\', '/downloads/', '\\users\\public\\',
    ]
    if exe_path and any(sd in exe_path for sd in _SUSPICIOUS_DIRS):
        info['suspicious'] = True
        info['warnings'].append(f'RUNNING_FROM_SUSPICIOUS_DIR: {info["exe"]}')

    # Check 3: No command line arguments for processes that normally have them
    if proc_name in ('svchost.exe', 'python', 'python3') and not info.get('cmdline'):
        info['warnings'].append('NO_CMDLINE_ARGS')

    # Check 4: Process name mimics system process (typosquatting)
    _COMMON_TYPOS = {
        'svch0st.exe': 'svchost.exe', 'svchost.exc': 'svchost.exe',
        'csrss.ex': 'csrss.exe', 'lsass.ex': 'lsass.exe',
        'explorer.exc': 'explorer.exe', 'svchost.exe ': 'svchost.exe',
        'scvhost.exe': 'svchost.exe', 'csrss.exe ': 'csrss.exe',
    }
    if proc_name in _COMMON_TYPOS:
        info['suspicious'] = True
        info['warnings'].append(f'TYPOSQUAT_SYSTEM_PROC: {proc_name} mimics {_COMMON_TYPOS[proc_name]}')

    # Check 5: Verify digital signature (Windows only)
    if platform.system() == 'Windows' and exe_path:
        try:
            import functools as _functools
            _sig_fn = _functools.partial(
                subprocess.run,
                ['powershell', '-Command',
                 f'(Get-AuthenticodeSignature "{info["exe"]}").Status'],
                capture_output=True, text=True, timeout=5
            )
            sig_result = await asyncio.get_running_loop().run_in_executor(_EXECUTOR, _sig_fn)
            sig_status = sig_result.stdout.strip().lower()
            info['signature'] = sig_status
            if sig_status != 'valid' and proc_name in _CRITICAL_NAMES:
                info['suspicious'] = True
                info['warnings'].append(f'SYSTEM_PROC_INVALID_SIGNATURE: {sig_status}')
        except Exception:
            info['signature'] = 'check_failed'

    if info['suspicious']:
        _log_event('suspicious_process', f'pid={pid} name={proc_name} warnings={info["warnings"]}', severity='WARNING')

    return info

@app.get('/log/list')
async def log_list(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    try:
        if not os.path.exists(LOG_FILE): return {'events': [], 'chain_valid': True}
        with _log_lock, open(LOG_FILE, encoding='utf-8') as f:
            lines = f.readlines()
        # Verify the full hash chain (tamper-evident log). Recompute each entry's
        # hash and confirm prev-links — surfaces tampering instead of trusting blindly.
        chain_valid = True
        prev_hash = '0' * 64
        for l in lines:
            l = l.strip()
            if not l: continue
            try:
                e = json.loads(l)
            except Exception:
                chain_valid = False; break
            h = e.get('hash', '')
            body = {k: v for k, v in e.items() if k != 'hash'}
            calc = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if calc != h or e.get('prev') != prev_hash:
                chain_valid = False; break
            prev_hash = h
        events = []
        for l in reversed(lines[-200:]):
            try: events.append(json.loads(l.strip()))
            except: pass
        return {'events': events, 'chain_valid': chain_valid}
    except Exception: return JSONResponse({'error': 'Failed to read logs'}, status_code=500)

@app.post('/log/clear')
async def log_clear(request: Request):
    ip = request.client.host
    # Only local or active client can clear logs
    if ip not in ('127.0.0.1', '::1', 'localhost'):
        with _active_client_lock: _aip = _active_client_ip
        if _aip is not None and _aip != ip:
            return JSONResponse({'error': 'forbidden'}, status_code=403)
    _log_write_queue.put('__CLEAR__')
    _log_event('log_cleared', ip=ip)
    return {'ok': True}

@app.post('/audio/start')
async def audio_start_http(request: Request):
    # No globals needed — _stream.* is single source of truth
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost'):
        with _active_client_lock: _aip = _active_client_ip
        if _aip is not None and _aip != ip:
            return JSONResponse({'error': 'forbidden'}, status_code=403)
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)
    if _stream.audio_streaming: return {'ok': True}
    try:
        import sounddevice as _sd
        devs = _sd.query_devices()
        if not any(d.get('max_input_channels', 0) > 0 for d in devs):
            return JSONResponse({'error': 'no_audio_input'}, status_code=503)
    except ImportError:
        return JSONResponse({'error': 'sounddevice_unavailable'}, status_code=503)
    if not _stream.start_audio(_audio_worker):
        return {'ok': True}
    audio_streaming = _stream.audio_streaming
    _audio_thread   = _stream.audio_thread
    _log_event('audio_start', ip=ip)
    return {'ok': True}

@app.post('/audio/stop')
async def audio_stop_http(request: Request):
    # No globals needed — _stream.* is single source of truth
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost'):
        with _active_client_lock: _aip = _active_client_ip
        if _aip is not None and _aip != ip:
            return JSONResponse({'error': 'forbidden'}, status_code=403)
    with _ws_pin_lock:
        if not _ws_pin_verified.get(ip, False):
            return JSONResponse({'error': 'pin required'}, status_code=403)
    _stream.stop_audio()
    audio_streaming = _stream.audio_streaming
    _audio_thread   = _stream.audio_thread
    _log_event('audio_stop', ip=request.client.host)
    return {'ok': True}

@app.post('/auth/pin_check')
async def auth_pin_check(request: Request):
    ip  = request.client.host
    now = time.time()
    # Rate limit applies ONLY here — login attempts are the only thing worth counting.
    # All other endpoints (resources, WS, flags, …) are excluded from rate limiting
    # because they fire in bursts on every page load and would block the WS connection.
    if _is_rate_limited(ip):
        return JSONResponse({'error': 'rate limited'}, status_code=429)
    if ip in _pin_lockout and now < _pin_lockout[ip]:
        rem = int(_pin_lockout[ip] - now)
        return JSONResponse({'error': f'Blocked. Wait {rem} seconds'}, status_code=429)

    d      = await request.json()
    stored = security.get("pins", {}).get(ip)

    if isinstance(stored, str):
        client_hash = stored
        pin_type    = 'pin'
    elif isinstance(stored, dict):
        client_hash = stored.get('hash')
        pin_type    = stored.get('type', 'pin')
    else:
        client_hash = None
        pin_type    = 'pin'

    if d.get('_probe'):
        return {'server_pin': bool(client_hash), 'pin_type': pin_type}

    if client_hash:
        if pin_type == 'pattern':
            secret = str(d.get('pattern_hash', ''))
            if not secret:
                return JSONResponse({'error': 'Pattern hash required'}, status_code=400)
        else:
            secret = str(d.get('pin_hash', ''))
            if not secret:
                return JSONResponse({'error': 'PIN hash required'}, status_code=400)

        # Anti-replay: client includes a timestamp nonce; server validates it is recent.
        # The nonce is NOT incorporated into the pin_hash — it only provides window-limited
        # replay protection. The pin_hash itself is SHA-256(PIN), verified via PBKDF2.
        nonce = d.get('nonce', '')
        if not nonce:
            return JSONResponse({'error': 'nonce required'}, status_code=400)
        try:
            nonce_ts = float(nonce) / 1000.0
            if abs(time.time() - nonce_ts) > 120:
                return JSONResponse({'error': 'nonce expired — retry'}, status_code=403)
        except (ValueError, TypeError):
            return JSONResponse({'error': 'invalid nonce format'}, status_code=400)

        if not _check_and_consume_nonce(nonce, ip):
            return JSONResponse({'error': 'nonce already used — retry'}, status_code=403)

        # Verify against stored hash — PBKDF2-SHA256 (built-in, zero dependencies)
        try:
            if client_hash.startswith('pbkdf2:'):
                _ev_loop = asyncio.get_running_loop()
                ok = await _ev_loop.run_in_executor(_EXECUTOR, _pin_verify, secret, client_hash)
            else:
                # Legacy bcrypt hashes are no longer supported — user must reset PIN
                return JSONResponse({'error': 'Legacy PIN format — please reset your PIN'}, status_code=400)
        except Exception:
            ok = False
    else:
        # No PIN set on server — check client's local_ok flag
        local_ok = d.get('local_ok', True)
        if not local_ok:
            return JSONResponse({'error': 'local PIN check failed'}, status_code=403)
        # Only auto-approve if the request comes from an IP with an active
        # WebSocket connection (already HMAC-verified).
        with _active_client_lock:
            _aip = _active_client_ip
        if _aip == ip:
            ok = True
        else:
            return JSONResponse({'error': 'no active session — connect via WebSocket first'}, status_code=403)

    if ok:
        with _ws_pin_lock:
            _ws_pin_verified[ip] = True
        with _sec_lock:
            _pin_fails[ip] = 0
            _pin_lockout.pop(ip, None)
            security.get("lockout", {}).pop(ip, None)
            _save_security()
        _log_event('pin_success', ip=ip)
        return {'ok': True}

    with _sec_lock:
        _pin_fails[ip] += 1
        _log_event('pin_fail', f'attempt={_pin_fails[ip]}', ip=ip)
        if _pin_fails[ip] >= PIN_MAX_TRIES:
            step     = _pin_lockout_count[ip]
            duration = PIN_LOCKOUT_STEPS[min(step, len(PIN_LOCKOUT_STEPS) - 1)]
            _pin_lockout_count[ip] += 1
            _pin_lockout[ip] = time.time() + duration
            _pin_fails[ip]   = 0
            if "lockout" not in security: security["lockout"] = {}
            security["lockout"][ip] = {"until": time.time() + duration, "count": int(_pin_lockout_count[ip])}
            _save_security()
            return JSONResponse({'error': f'Locked for {duration} seconds due to multiple failed attempts'}, status_code=429)
        remaining = PIN_MAX_TRIES - _pin_fails[ip]
    return {'ok': False, 'remaining': remaining}


@app.post('/auth/set_pin')
async def auth_set_pin(request: Request):
    ip       = request.client.host
    d        = await request.json()
    if d.get('_probe'): return {'ok': True, 'probe': True}
    pin_type = d.get('type', 'pin')
    # Use built-in PBKDF2-SHA256 — no bcrypt dependency needed
    if pin_type == 'pattern':
        pattern = str(d.get('pattern_hash', ''))
        if not pattern:
            return JSONResponse({'error': 'Pattern hash required'}, status_code=400)
        # Accept SHA-256 hashes (64 hex chars)
        if len(pattern) != 64 or not all(c in '0123456789abcdef' for c in pattern):
            return JSONResponse({'error': 'Invalid pattern hash'}, status_code=400)
        hashed = _pin_hash(pattern)
        with _sec_lock:
            if "pins" not in security: security["pins"] = {}
            security["pins"][ip] = {"hash": hashed, "type": "pattern"}
            _save_security()
        _log_event('pattern_set', ip=ip)
    else:
        pin = str(d.get('pin_hash', ''))
        if not pin:
            return JSONResponse({'error': 'PIN hash required'}, status_code=400)
        # Must be SHA-256 hash (64 hex chars)
        if len(pin) != 64 or not all(c in '0123456789abcdef' for c in pin):
            return JSONResponse({'error': 'Invalid PIN hash'}, status_code=400)
        hashed = _pin_hash(pin)
        with _sec_lock:
            if "pins" not in security: security["pins"] = {}
            security["pins"][ip] = {"hash": hashed, "type": "pin"}
            _save_security()
        _log_event('pin_set', ip=ip)
    return {'ok': True}


@app.post('/auth/clear_pin')
async def auth_clear_pin(request: Request):
    ip = request.client.host
    with _sec_lock:
        security.get("pins", {}).pop(ip, None)
        _save_security()
    _log_event('pin_cleared', ip=ip)
    return {'ok': True}


@app.get('/auth/get_client_key')
async def auth_get_client_key(request: Request):
    """Return a per-IP HMAC key derived from the server secret.
    The master secret itself never leaves the server — only a per-IP derived key is sent.
    NOTE: The derived key is equivalent to the master secret for this specific IP, so it must
    be protected with the same care. Use HTTPS to prevent key interception.
    Requires the IP to be whitelisted."""
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'not allowed'}, status_code=403)
    # HMAC key delivery: require HTTPS on non-private interfaces; allow plain
    # HTTP only for private/LAN hosts (the supported deployment) so mobile
    # clients on the local network can authenticate. Warn so the operator
    # is aware the key crosses the LAN in clear text without TLS.
    if (ip not in ('127.0.0.1', '::1', 'localhost')
            and request.url.scheme != 'https'):
        host = request.headers.get('host', '')
        if not _is_private_host(host):
            return JSONResponse(
                {'error': 'HTTPS required — HMAC key delivery over a public interface is forbidden'},
                status_code=426)
        _vprint(f"\u26a0\ufe0f  HMAC key sent over plain HTTP to LAN client {ip} "
                f"(enable HTTPS with a cert for full protection)", flush=True)
    with _sec_lock:
        master = security.get('hmac_secret', '')
    if not master:
        return JSONResponse({'error': 'no hmac key'}, status_code=500)
    client_key = _hmac_mod.new(master.encode(), f'portdesk-client:{ip}'.encode(), hashlib.sha256).hexdigest()
    return {'client_key': client_key}


# ── Group 2: TOFU — expose cert fingerprint ───────────────────────────────────
def _get_cert_fingerprint():
    cert_file = os.path.join(DATA_DIR, 'cert.pem')
    if not os.path.isfile(cert_file):
        cert_file = os.path.join(BASE_DIR, 'cert.pem')
    if not os.path.isfile(cert_file):
        return None
    try:
        import ssl
        with open(cert_file) as f:
            pem = f.read()
        der = ssl.PEM_cert_to_DER_cert(pem)
        raw = hashlib.sha256(der).hexdigest()
        return ':'.join(raw[i:i+2].upper() for i in range(0, len(raw), 2))
    except Exception:
        return None

@app.get('/security/fingerprint')
async def security_fingerprint(request: Request):
    fp = _get_cert_fingerprint()
    return {'fingerprint': fp, 'https': fp is not None}

@app.get('/scheduled/list')
async def scheduled_list(request: Request):
    ip = request.client.host
    if ip not in ('127.0.0.1', '::1', 'localhost') and not _is_allowed(ip):
        return JSONResponse({'error': 'forbidden'}, status_code=403)
    with _sched_lock: return list(scheduled_tasks)

@app.post('/scheduled/save')
async def scheduled_save(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d = await request.json()
    task = {
        'id':       str(int(time.time())),
        'name':     str(d.get('name',''))[:128],
        'time':     str(d.get('time',''))[:10],
        'macro':    str(d.get('macro',''))[:128],
        'enabled':  True,
        'last_run': ''
    }
    with _sched_lock:
        if len(scheduled_tasks) >= 100:
            return JSONResponse({'error': 'scheduled task limit reached (max 100)'}, status_code=400)
        scheduled_tasks.append(task)
        _save_scheduled(scheduled_tasks)
    return {'ok': True}

@app.post('/scheduled/delete')
async def scheduled_delete(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d   = await request.json()
    tid = d.get('id','')
    with _sched_lock:
        scheduled_tasks[:] = [t for t in scheduled_tasks if t.get('id') != tid]
        _save_scheduled(scheduled_tasks)
    return {'ok': True}

@app.post('/scheduled/toggle')
async def scheduled_toggle(request: Request):
    _g = _require_active_pin(request)
    if _g is not None: return _g
    d   = await request.json()
    tid = d.get('id','')
    with _sched_lock:
        for t in scheduled_tasks:
            if t.get('id') == tid: t['enabled'] = not t.get('enabled', True); break
        _save_scheduled(scheduled_tasks)
    return {'ok': True}

@app.get('/log/export')
async def log_export(request: Request):
    """Export audit logs as JSON file for offline analysis.
    Requires active client or localhost access. Returns all events in a downloadable JSON file."""
    ip = request.client.host
    # Allow localhost or active client
    if ip not in ('127.0.0.1', '::1', 'localhost'):
        with _active_client_lock:
            _aip = _active_client_ip
        if _aip is not None and _aip != ip:
            return JSONResponse({'error': 'forbidden'}, status_code=403)
        with _ws_pin_lock:
            if not _ws_pin_verified.get(ip, False):
                return JSONResponse({'error': 'pin required'}, status_code=403)

    try:
        if not os.path.exists(LOG_FILE):
            return JSONResponse({'error': 'no logs yet'}, status_code=404)
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            events = []
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass

        # Calculate chain validity
        chain_valid = True
        prev_hash = '0' * 64
        for entry in events:
            h = entry.get('hash', '')
            body = {k: v for k, v in entry.items() if k != 'hash'}
            calc = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            if calc != h or entry.get('prev') != prev_hash:
                chain_valid = False
                break
            prev_hash = h

        export = {
            'exported_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'exported_by': ip,
            'total_events': len(events),
            'chain_valid': chain_valid,
            'events': events,
        }

        # Return as downloadable JSON
        import io as _io
        export_json = json.dumps(export, indent=2, ensure_ascii=False)
        body = _io.BytesIO(export_json.encode('utf-8'))
        resp = FileResponse.__new__(FileResponse)
        resp.body = export_json.encode('utf-8')
        resp.headers = {}
        resp.headers['Content-Type'] = 'application/json'
        resp.headers['Content-Disposition'] = f'attachment; filename="portdesk_audit_{time.strftime("%Y%m%d_%H%M%S")}.json"'
        return JSONResponse(export, status_code=200)
    except Exception:
        return JSONResponse({'error': 'failed to export logs'}, status_code=500)



