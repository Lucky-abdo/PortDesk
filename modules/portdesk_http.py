"""
PortDesk Custom HTTP/WebSocket Server
Built from scratch on asyncio — replaces fastapi + starlette + uvicorn.

Features:
  - HTTP/1.1 request/response with keep-alive (correct HTTP/1.0 handling)
  - WebSocket (RFC 6455) with full frame handling + fragmentation reassembly
  - JSON/Query/Form/Multipart body parsing
  - File response + streaming response
  - Middleware chain (before/after handler)
  - SSL/TLS support
  - CORS preflight handling
  - Header/body size limits (OOM protection)
  - HTTP gzip compression (Accept-Encoding: gzip)
  - Connection concurrency limit (Semaphore)
  - Zero external dependencies (stdlib only)

Designed specifically for PortDesk — no bloat, no unused features.
"""
from __future__ import annotations

import asyncio
import json
import os
import ssl
import struct
import hashlib
import base64
import io
import re
import time
import zlib
import mimetypes
from typing import Any, Callable, Generator, Iterator
from urllib.parse import urlparse, parse_qs, unquote, quote

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_HEADERS       = 100           # max number of header lines
MAX_HEADER_SIZE   = 64 * 1024     # 64 KB total header size
MAX_BODY_SIZE     = 100 * 1024 * 1024  # 100 MB — override via set_max_body_size()

def set_max_body_size(size: int) -> None:
    global MAX_BODY_SIZE
    MAX_BODY_SIZE = max(size, 1 * 1024 * 1024)
MAX_WS_FRAME      = 10 * 1024 * 1024   # 10 MB per frame
MAX_MULTIPART_PARTS = 200              # max parts in multipart/form-data
WS_IDLE_TIMEOUT   = 300           # 5 min idle timeout on WebSocket reads

# ── WebSocket Constants ────────────────────────────────────────────────────────
_WS_MAGIC = b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_WS_OPCODE_TEXT       = 0x1
_WS_OPCODE_BINARY     = 0x2
_WS_OPCODE_CLOSE      = 0x8
_WS_OPCODE_PING       = 0x9
_WS_OPCODE_PONG       = 0xA
_WS_OPCODE_CONTINUATION = 0x0

_HTTP_STATUS_TEXT = {
    200: 'OK', 201: 'Created', 204: 'No Content', 206: 'Partial Content',
    301: 'Moved Permanently', 302: 'Found', 304: 'Not Modified',
    400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
    404: 'Not Found', 405: 'Method Not Allowed', 408: 'Request Timeout',
    409: 'Conflict', 413: 'Payload Too Large', 415: 'Unsupported Media Type',
    416: 'Range Not Satisfiable', 422: 'Unprocessable Entity', 429: 'Too Many Requests',
    431: 'Request Header Fields Too Large',
    500: 'Internal Server Error', 501: 'Not Implemented', 503: 'Service Unavailable',
}


# ══════════════════════════════════════════════════════════════════════════════
#  Request & Response Objects
# ══════════════════════════════════════════════════════════════════════════════

class _ClientInfo:
    __slots__ = ('host',)
    def __init__(self, host: str) -> None:
        self.host: str = host


class _URLInfo:
    __slots__ = ('path', 'scheme', 'query')
    def __init__(self, path: str, scheme: str = 'http', query: str = '') -> None:
        self.path:   str = path
        self.scheme: str = scheme
        self.query:  str = query


class Request:
    """Minimal HTTP request object — compatible with FastAPI's Request interface
    for the properties that PortDesk actually uses."""
    __slots__ = ('method', 'url', 'headers', '_body', 'client', '_query',
                 '_json_cache', '_form_cache', '_files_cache', 'http_version')

    def __init__(self, method: str, path: str, headers: dict[str, str],
                 body: bytes, client_host: str,
                 scheme: str = 'http', http_version: str = 'HTTP/1.1') -> None:
        self.method: str = method
        if '?' in path:
            base, qs = path.split('?', 1)
        else:
            base, qs = path, ''
        self.url    = _URLInfo(base, scheme, qs)
        self.headers: dict[str, str] = headers
        self._body:   bytes          = body
        self.client  = _ClientInfo(client_host)
        self._query  = parse_qs(qs, keep_blank_values=True)
        self._json_cache  = None
        self._form_cache  = None
        self._files_cache = None
        self.http_version: str = http_version

    def query_param(self, name: str, default: str = '') -> str:
        """Get a single query parameter value."""
        vals = self._query.get(name, [])
        return vals[0] if vals else default

    def query_int(self, name: str, default: int = 0) -> int:
        """Get a query parameter as int."""
        v = self.query_param(name, '')
        try: return int(v)
        except (ValueError, TypeError): return default

    async def json(self):
        """Parse JSON body. Cached after first call."""
        if self._json_cache is None:
            if isinstance(self._body, bytes):
                self._json_cache = json.loads(self._body.decode('utf-8'))
            elif isinstance(self._body, str):
                self._json_cache = json.loads(self._body)
            else:
                self._json_cache = self._body
        return self._json_cache

    async def _parse_multipart(self):
        """Parse multipart/form-data body. Returns (fields_dict, files_list)."""
        if self._form_cache is not None:
            return self._form_cache, self._files_cache

        ct = self.headers.get('content-type', '')
        if 'multipart/form-data' not in ct:
            self._form_cache = {}
            self._files_cache = []
            return self._form_cache, self._files_cache

        # Extract boundary
        boundary = None
        for part in ct.split(';'):
            part = part.strip()
            if part.startswith('boundary='):
                boundary = part[9:].strip('"')
                break
        if not boundary:
            self._form_cache = {}
            self._files_cache = []
            return self._form_cache, self._files_cache

        boundary_bytes = b'--' + boundary.encode()
        end_boundary = boundary_bytes + b'--'
        body = self._body if isinstance(self._body, bytes) else self._body.encode()

        fields = {}
        files = []
        parts = body.split(boundary_bytes)

        for part in parts[1:MAX_MULTIPART_PARTS + 1]:  # Skip preamble, enforce limit
            if part.startswith(b'--'):
                break  # End boundary
            # Remove leading \r\n
            if part.startswith(b'\r\n'):
                part = part[2:]
            # Remove trailing \r\n
            if part.endswith(b'\r\n'):
                part = part[:-2]

            # Split headers from body
            if b'\r\n\r\n' not in part:
                continue
            header_section, content = part.split(b'\r\n\r\n', 1)
            # Remove trailing \r\n from content
            if content.endswith(b'\r\n'):
                content = content[:-2]

            # Parse headers
            part_headers = {}
            for line in header_section.decode('utf-8', errors='replace').split('\r\n'):
                if ':' in line:
                    k, v = line.split(':', 1)
                    part_headers[k.strip().lower()] = v.strip()

            cd = part_headers.get('content-disposition', '')
            name = None
            filename = None
            # Parse Content-Disposition
            for seg in cd.split(';'):
                seg = seg.strip()
                if seg.startswith('name='):
                    name = seg[5:].strip('"')
                elif seg.startswith('filename='):
                    filename = seg[9:].strip('"')
                elif seg.startswith('filename*='):
                    # RFC 5987 encoded filename: charset'language'encoded_value
                    raw = seg.split('=', 1)[1]
                    if "'" in raw:
                        encoded_val = raw.split("'", 2)[-1].strip('"')
                        filename = unquote(encoded_val)
                    else:
                        filename = raw.strip('"')

            if name is None:
                continue

            if filename is not None:
                # This is a file upload
                files.append(_UploadFile(filename, content, part_headers.get('content-type', '')))
            else:
                # This is a form field
                fields[name] = content.decode('utf-8', errors='replace')

        self._form_cache = fields
        self._files_cache = files
        return fields, files

    async def form(self):
        """Get form fields dict from multipart body."""
        fields, _ = await self._parse_multipart()
        return fields

    async def files(self):
        """Get list of uploaded files from multipart body."""
        _, files = await self._parse_multipart()
        return files


class _UploadFile:
    """Represents an uploaded file from a multipart form."""
    __slots__ = ('filename', '_content', 'content_type', '_pos')

    def __init__(self, filename, content, content_type=''):
        self.filename = filename
        self._content = content
        self.content_type = content_type
        self._pos = 0

    async def read(self, size=-1):
        if size < 0:
            data = self._content[self._pos:]
            self._pos = len(self._content)
            return data
        data = self._content[self._pos:self._pos + size]
        self._pos += len(data)
        return data


# ── Response Types ────────────────────────────────────────────────────────────

class Response:
    """Base HTTP response."""
    __slots__ = ('status', 'headers', 'body')

    _GZIP_MIN_SIZE = 1024
    _GZIP_TYPES   = {'application/json', 'text/plain', 'text/html', 'text/css',
                     'application/javascript', 'text/javascript'}

    def __init__(self, body: bytes | str | dict | list = b'',
                 status: int = 200,
                 headers: dict[str, str] | None = None) -> None:
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode('utf-8')
            h = headers or {}
            if 'content-type' not in {k.lower() for k in h}:
                h['Content-Type'] = 'application/json'
            headers = h
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.status = status
        self.headers = headers or {}
        self.body = body if isinstance(body, (bytes, bytearray)) else b''

    def _maybe_gzip(self, accept_encoding: str) -> bool:
        """Compress self.body in-place if conditions met. Returns True if compressed."""
        if 'gzip' not in accept_encoding: return False
        if len(self.body) < self._GZIP_MIN_SIZE: return False
        ct = ''
        for k, v in self.headers.items():
            if k.lower() == 'content-type':
                ct = v.split(';')[0].strip().lower(); break
        if ct not in self._GZIP_TYPES: return False
        compressed = zlib.compress(self.body, level=6, wbits=31)  # wbits=31 → gzip
        self.body = compressed
        self.headers['Content-Encoding'] = 'gzip'
        return True

    def _encode_headers(self, accept_encoding: str = ''):
        """Return HTTP response bytes including status line and headers."""
        if accept_encoding:
            self._maybe_gzip(accept_encoding)
        status_text = _HTTP_STATUS_TEXT.get(self.status, 'Unknown')
        lines = [f'HTTP/1.1 {self.status} {status_text}\r\n']
        has_ct = False
        has_cl = False
        for k, v in self.headers.items():
            lines.append(f'{k}: {v}\r\n')
            if k.lower() == 'content-type': has_ct = True
            if k.lower() == 'content-length': has_cl = True
        if not has_ct:
            lines.append('Content-Type: text/plain\r\n')
        if not has_cl and isinstance(self.body, (bytes, bytearray)):
            lines.append(f'Content-Length: {len(self.body)}\r\n')
        lines.append('\r\n')
        return ''.join(lines).encode('utf-8')


class JSONResponse(Response):
    """JSON HTTP response."""
    def __init__(self, data: Any, status_code: int = 200,
                 headers: dict[str, str] | None = None) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        h = headers or {}
        if 'content-type' not in {k.lower() for k in h}:
            h['Content-Type'] = 'application/json; charset=utf-8'
        super().__init__(body, status=status_code, headers=h)


class FileResponse(Response):
    """Serve a file from disk, with HTTP Range (resumable download) support."""
    def __init__(self, path: str, filename: str | None = None,
                 chunk_size: int = 65536, range_header: str | None = None) -> None:
        self._file_path  = path
        self._chunk_size = chunk_size
        self._filename   = filename or os.path.basename(path)
        self._is_download = filename is not None
        self.status  = 200
        self.headers: dict[str, str] = {}
        self.body    = None
        self._start  = 0
        self._end    = None   # inclusive end byte; None = to EOF

        ct, _ = mimetypes.guess_type(self._filename)
        self.headers['Content-Type'] = ct or 'application/octet-stream'
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        # Advertise range support so clients/browsers can resume.
        self.headers['Accept-Ranges'] = 'bytes'
        if size is not None and range_header:
            rng = self._parse_range(range_header, size)
            if rng is None:
                # Unsatisfiable range → 416
                self.status = 416
                self.headers['Content-Range'] = f'bytes */{size}'
                self.headers['Content-Length'] = '0'
            else:
                self._start, self._end = rng
                self.status = 206
                self.headers['Content-Range'] = f'bytes {self._start}-{self._end}/{size}'
                self.headers['Content-Length'] = str(self._end - self._start + 1)
        elif size is not None:
            self.headers['Content-Length'] = str(size)

    @staticmethod
    def _parse_range(range_header, size):
        """Parse 'bytes=START-END' (single range only). Returns (start,end inclusive) or None."""
        try:
            unit, _, spec = range_header.partition('=')
            if unit.strip().lower() != 'bytes' or ',' in spec:
                return None  # multipart ranges not supported — caller serves full
            start_s, _, end_s = spec.strip().partition('-')
            if start_s == '':
                # suffix range: last N bytes
                n = int(end_s)
                if n <= 0: return None
                start = max(0, size - n); end = size - 1
            else:
                start = int(start_s)
                end = int(end_s) if end_s else size - 1
            if start > end or start >= size:
                return None
            end = min(end, size - 1)
            return (start, end)
        except (ValueError, TypeError):
            return None
        if self._is_download:
            try:
                self._filename.encode('ascii')
                self.headers['Content-Disposition'] = f'attachment; filename="{self._filename}"'
            except UnicodeEncodeError:
                ascii_fallback = self._filename.encode('ascii', errors='replace').decode('ascii')
                encoded = quote(self._filename, safe='')
                self.headers['Content-Disposition'] = (
                    f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"
                )

    async def send(self, writer: asyncio.StreamWriter) -> None:
        """Stream file to client, honoring a Range (206) when set."""
        writer.write(self._encode_headers())
        if self.status == 416:
            await writer.drain(); return
        loop = asyncio.get_running_loop()
        try:
            with open(self._file_path, 'rb') as f:
                if self._start:
                    await loop.run_in_executor(None, f.seek, self._start)
                remaining = (self._end - self._start + 1) if self._end is not None else None
                while True:
                    to_read = self._chunk_size if remaining is None else min(self._chunk_size, remaining)
                    if to_read <= 0:
                        break
                    chunk = await loop.run_in_executor(None, f.read, to_read)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    if remaining is not None:
                        remaining -= len(chunk)
                        if remaining <= 0:
                            break
        except Exception:
            pass


class StreamingResponse(Response):
    """Stream data from an async generator or sync iterator."""
    def __init__(self, generator: Any,
                 media_type: str = 'application/octet-stream',
                 headers: dict[str, str] | None = None) -> None:
        self._generator = generator
        self.status  = 200
        self.headers = headers or {}
        self.body    = None
        self.headers['Content-Type'] = media_type
        self.headers['Transfer-Encoding'] = 'chunked'

    async def send(self, writer: asyncio.StreamWriter) -> None:
        """Stream data using chunked transfer encoding."""
        writer.write(self._encode_headers())
        try:
            if hasattr(self._generator, '__aiter__'):
                async for chunk in self._generator:
                    if chunk:
                        writer.write(f'{len(chunk):X}\r\n'.encode())
                        writer.write(chunk)
                        writer.write(b'\r\n')
                        await writer.drain()
            else:
                for chunk in self._generator:
                    if chunk:
                        writer.write(f'{len(chunk):X}\r\n'.encode())
                        writer.write(chunk)
                        writer.write(b'\r\n')
                        await writer.drain()
        except Exception:
            pass
        # Final chunk
        writer.write(b'0\r\n\r\n')
        await writer.drain()


# ── Exception for oversized requests ──────────────────────────────────────────
class _RequestTooLarge(Exception):
    pass

class _BadRequest(Exception):
    pass


# ══════════════════════════════════════════════════════════════════════════════
#  WebSocket Implementation (RFC 6455)
# ══════════════════════════════════════════════════════════════════════════════

class WebSocketDisconnect(Exception):
    def __init__(self, code: int = 1000) -> None:
        self.code: int = code


class WebSocket:
    """WebSocket connection — compatible with FastAPI's WebSocket for PortDesk usage."""
    __slots__ = ('_reader', '_writer', 'client', 'headers', '_closed',
                 '_close_code', '_lock')

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 client_host: str, headers: dict[str, str]) -> None:
        self._reader = reader
        self._writer = writer
        self.client  = _ClientInfo(client_host)
        self.headers: dict[str, str] = headers
        self._closed:     bool     = False
        self._close_code: int|None = None
        self._lock = asyncio.Lock()

    @staticmethod
    async def handshake(reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter,
                        headers: dict[str, str]) -> bool:
        """Perform WebSocket handshake. Returns True on success."""
        key = headers.get('sec-websocket-key', '')
        if not key:
            return False
        accept = base64.b64encode(
            hashlib.sha1(key.encode() + _WS_MAGIC).digest()
        ).decode()
        response = (
            'HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Accept: {accept}\r\n'
            '\r\n'
        )
        writer.write(response.encode())
        await writer.drain()
        return True

    async def accept(self) -> None:
        """Accept WebSocket connection (handshake already done by server)."""
        pass

    async def send_json(self, data: Any) -> None:
        """Send a JSON message as text frame."""
        text = json.dumps(data, ensure_ascii=False)
        await self._send_frame(_WS_OPCODE_TEXT, text.encode('utf-8'))

    async def send_bytes(self, data: bytes) -> None:
        """Send binary data as binary frame."""
        await self._send_frame(_WS_OPCODE_BINARY, data if isinstance(data, bytes) else bytes(data))

    async def receive_text(self) -> str:
        """Receive a text message. Raises WebSocketDisconnect on close."""
        opcode, payload = await self._recv_message()
        if opcode == _WS_OPCODE_CLOSE:
            raise WebSocketDisconnect(self._close_code or 1000)
        if opcode in (_WS_OPCODE_TEXT, _WS_OPCODE_BINARY):
            return payload.decode('utf-8')
        return ''

    async def receive(self):
        """Receive a message, preserving type. Returns ('text', str), ('bytes', bytes),
        or raises WebSocketDisconnect on close. Lets the app handle binary input
        frames (compact control protocol) vs JSON text frames distinctly."""
        opcode, payload = await self._recv_message()
        if opcode == _WS_OPCODE_CLOSE:
            raise WebSocketDisconnect(self._close_code or 1000)
        if opcode == _WS_OPCODE_BINARY:
            return ('bytes', payload)
        if opcode == _WS_OPCODE_TEXT:
            return ('text', payload.decode('utf-8'))
        return ('text', '')

    async def close(self, code: int = 1000, reason: str = '') -> None:
        """Send close frame and close connection."""
        if self._closed:
            return
        self._closed = True
        self._close_code = code
        try:
            # Ensure close reason doesn't split a UTF-8 character
            reason_bytes = reason.encode('utf-8')[:123]
            try:
                reason_bytes.decode('utf-8')
            except UnicodeDecodeError:
                while reason_bytes:
                    try:
                        reason_bytes.decode('utf-8')
                        break
                    except UnicodeDecodeError:
                        reason_bytes = reason_bytes[:-1]
            payload = struct.pack('>H', code) + reason_bytes
            await self._send_frame(_WS_OPCODE_CLOSE, payload)
        except Exception:
            pass
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    async def _send_frame(self, opcode, payload):
        """Send a WebSocket frame (server→client, unmasked)."""
        async with self._lock:
            frame = bytearray()
            frame.append(0x80 | opcode)  # FIN=1
            length = len(payload)
            if length < 126:
                frame.append(length)
            elif length < 65536:
                frame.append(126)
                frame.extend(struct.pack('>H', length))
            else:
                frame.append(127)
                frame.extend(struct.pack('>Q', length))
            frame.extend(payload)
            try:
                self._writer.write(bytes(frame))
                await self._writer.drain()
            except (ConnectionError, OSError):
                self._closed = True

    async def _recv_message(self):
        """Receive a complete WebSocket message, reassembling fragments per RFC 6455 §5.4."""
        opcode = None
        fragments = []
        total_size = 0
        while True:
            try:
                header = await asyncio.wait_for(self._reader.readexactly(2), timeout=WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                await self.close(1000)
                raise WebSocketDisconnect(1000)

            fin = bool(header[0] & 0x80)
            frame_opcode = header[0] & 0x0F

            # RSV bits check — RFC 6455 §5.2
            if header[0] & 0x70:
                await self.close(1002)
                raise WebSocketDisconnect(1002)

            masked = bool(header[1] & 0x80)
            length = header[1] & 0x7F

            # Unmasked client frames must be rejected — RFC 6455 §5.3
            if not masked:
                await self.close(1002)
                raise WebSocketDisconnect(1002)

            if length == 126:
                try:
                    data = await asyncio.wait_for(self._reader.readexactly(2), timeout=WS_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    raise WebSocketDisconnect(1000)
                length = struct.unpack('>H', data)[0]
            elif length == 127:
                try:
                    data = await asyncio.wait_for(self._reader.readexactly(8), timeout=WS_IDLE_TIMEOUT)
                except asyncio.TimeoutError:
                    raise WebSocketDisconnect(1000)
                length = struct.unpack('>Q', data)[0]

            # Sanity cap — per-frame and total accumulated
            if length > MAX_WS_FRAME:
                raise WebSocketDisconnect(1009)
            total_size += length
            if total_size > MAX_WS_FRAME:
                await self.close(1009)
                raise WebSocketDisconnect(1009)

            try:
                mask_key = await asyncio.wait_for(self._reader.readexactly(4), timeout=WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                raise WebSocketDisconnect(1000)

            try:
                payload = await asyncio.wait_for(self._reader.readexactly(length), timeout=WS_IDLE_TIMEOUT)
            except asyncio.TimeoutError:
                raise WebSocketDisconnect(1000)

            # Unmask (client→server frames are always masked)
            if mask_key:
                payload = bytearray(payload)
                for i in range(len(payload)):
                    payload[i] ^= mask_key[i % 4]
                payload = bytes(payload)

            # Handle control frames (may arrive between fragments)
            if frame_opcode == _WS_OPCODE_PING:
                await self._send_frame(_WS_OPCODE_PONG, payload)
                continue
            if frame_opcode == _WS_OPCODE_CLOSE:
                self._closed = True
                self._close_code = struct.unpack('>H', payload[:2])[0] if len(payload) >= 2 else 1000
                try:
                    await self._send_frame(_WS_OPCODE_CLOSE, payload[:2] if len(payload) >= 2 else b'\x03\xe8')
                except Exception:
                    pass
                return _WS_OPCODE_CLOSE, payload
            if frame_opcode == _WS_OPCODE_PONG:
                continue

            # Data frames — reassemble fragments
            if opcode is None:
                opcode = frame_opcode  # First frame sets the opcode
            elif frame_opcode != _WS_OPCODE_CONTINUATION:
                # Non-zero opcode on continuation → protocol error
                await self.close(1002)
                raise WebSocketDisconnect(1002)

            fragments.append(payload)
            if fin:
                return opcode, b''.join(fragments)


# ══════════════════════════════════════════════════════════════════════════════
#  Router & Server
# ══════════════════════════════════════════════════════════════════════════════

class _Route:
    __slots__ = ('method', 'path', 'handler')
    def __init__(self, method, path, handler):
        self.method = method
        self.path = path
        self.handler = handler


class Server:
    """Custom asyncio HTTP/WebSocket server — replaces FastAPI + Uvicorn."""

    MAX_CONNECTIONS = 64  # concurrent connection limit

    def __init__(self):
        self._routes = []
        self._ws_handler = None
        self._ws_path = '/ws'
        self._middleware = []
        self._exception_handler = None
        self._options_handler = None
        self._conn_sem: asyncio.Semaphore | None = None  # init in _serve (needs running loop)

    # ── Route Registration (decorator API, same style as FastAPI) ───────────

    def get(self, path: str) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self._routes.append(_Route('GET', path, handler))
            return handler
        return decorator

    def post(self, path: str) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self._routes.append(_Route('POST', path, handler))
            return handler
        return decorator

    def websocket(self, path: str) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self._ws_handler = handler
            self._ws_path = path
            return handler
        return decorator

    def options(self, path: str) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self._options_handler = handler
            return handler
        return decorator

    def exception_handler(self, exc_type: type) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self._exception_handler = handler
            return handler
        return decorator

    def add_middleware(self, middleware_factory: Callable) -> None:
        """Add middleware. Callable: async def(request, call_next) -> response"""
        self._middleware.append(middleware_factory)

    def _match_route(self, method: str, path: str) -> Callable | None:
        """Find handler for method+path. Returns handler or None."""
        for route in self._routes:
            if route.method == method and route.path == path:
                return route.handler
        return None

    # ── HTTP Request Parsing ───────────────────────────────────────────────

    async def _read_request(self, reader):
        """Read and parse an HTTP request. Returns Request or None on error.
        Raises _RequestTooLarge for oversized bodies, _BadRequest for bad headers."""
        # Read request line
        request_line = await asyncio.wait_for(reader.readline(), timeout=30)
        if not request_line:
            return None
        try:
            parts = request_line.decode('utf-8', errors='replace').strip().split(' ')
            if len(parts) < 2:
                return None
            method = parts[0].upper()
            path = parts[1]
            version = parts[2] if len(parts) >= 3 else 'HTTP/1.0'
        except Exception:
            return None

        # Read headers with limits
        headers = {}
        content_length = 0
        header_count = 0
        total_header_size = 0
        seen_content_length = None
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30)
            if not line or line == b'\r\n':
                break
            header_count += 1
            total_header_size += len(line)
            if header_count > MAX_HEADERS or total_header_size > MAX_HEADER_SIZE:
                raise _BadRequest("headers too large")
            try:
                decoded = line.decode('utf-8', errors='replace').strip()
                if ':' in decoded:
                    k, v = decoded.split(':', 1)
                    key_lower = k.strip().lower()
                    val = v.strip()
                    # Detect duplicate Content-Length (request smuggling vector)
                    if key_lower == 'content-length':
                        if seen_content_length is not None and seen_content_length != val:
                            raise _BadRequest("conflicting Content-Length")
                        seen_content_length = val
                        try:
                            content_length = int(val)
                        except ValueError:
                            pass  # Skip bad Content-Length, keep reading headers
                    else:
                        headers[key_lower] = val
                # Keep Content-Length in headers dict too
                if key_lower == 'content-length':
                    headers[key_lower] = val
            except _BadRequest:
                raise
            except Exception:
                continue  # Skip unparseable line, don't break the loop

        # Reject chunked transfer encoding (not supported)
        if headers.get('transfer-encoding', '').lower() == 'chunked':
            raise _BadRequest("chunked transfer encoding not supported")

        # Read body
        body = b''
        if content_length > 0:
            if content_length > MAX_BODY_SIZE:
                raise _RequestTooLarge()
            body = await asyncio.wait_for(reader.readexactly(content_length), timeout=60)

        # Determine scheme (SSL or not)
        scheme = 'https' if hasattr(reader, '_protocol') and hasattr(reader._protocol, '_ssl_context') else 'http'

        return Request(method, path, headers, body, '', scheme, version)

    # ── Response Sending ───────────────────────────────────────────────────

    async def _send_response(self, writer, response, accept_encoding: str = ''):
        """Send an HTTP response."""
        if isinstance(response, (FileResponse, StreamingResponse)):
            await response.send(writer)
        elif isinstance(response, Response):
            writer.write(response._encode_headers(accept_encoding))
            if response.body:
                writer.write(response.body)
            await writer.drain()
        elif isinstance(response, (dict, list)):
            jr = JSONResponse(response)
            writer.write(jr._encode_headers(accept_encoding))
            writer.write(jr.body)
            await writer.drain()
        else:
            writer.write(b'HTTP/1.1 500 Internal Server Error\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()

    # ── Connection Handler ─────────────────────────────────────────────────

    async def _handle_connection(self, reader, writer):
        """Handle a single client connection (HTTP keep-alive + WebSocket upgrade)."""
        if self._conn_sem is not None and self._conn_sem.locked():
            try:
                writer.write(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n')
                await writer.drain()
            except Exception:
                pass
            try: writer.close()
            except Exception: pass
            return
        if self._conn_sem is not None:
            await self._conn_sem.acquire()
        peer = writer.get_extra_info('peername')
        client_host = peer[0] if peer else '0.0.0.0'
        scheme = 'https' if writer.get_extra_info('sslcontext') else 'http'

        try:
            while True:
                # Read HTTP request
                try:
                    request = await self._read_request(reader)
                except asyncio.TimeoutError:
                    break
                except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                    break
                except _RequestTooLarge:
                    await self._send_response(writer, JSONResponse({'error': 'payload too large'}, status_code=413))
                    break
                except _BadRequest as e:
                    code = 431 if 'headers' in str(e) else 400
                    await self._send_response(writer, JSONResponse({'error': str(e)}, status_code=code))
                    break

                if request is None:
                    break

                # Set client host and scheme
                request.client = _ClientInfo(client_host)
                request.url = _URLInfo(request.url.path, scheme, request.url.query)

                # ── WebSocket upgrade? ────────────────────────────────────
                upgrade = request.headers.get('upgrade', '').lower()
                if upgrade == 'websocket' and self._ws_handler and request.url.path == self._ws_path:
                    ws_headers = dict(request.headers)
                    if not await WebSocket.handshake(reader, writer, request.headers):
                        break
                    ws = WebSocket(reader, writer, client_host, ws_headers)
                    try:
                        await self._ws_handler(ws)
                    except WebSocketDisconnect:
                        pass
                    except Exception:
                        pass
                    finally:
                        try: ws._closed = True
                        except: pass
                    break  # WebSocket connections don't do HTTP keep-alive

                # ── OPTIONS preflight ─────────────────────────────────────
                if request.method == 'OPTIONS' and self._options_handler:
                    response = await self._options_handler(request.url.path, request)
                    await self._send_response(writer, response)
                    continue

                # ── Find handler ──────────────────────────────────────────
                handler = self._match_route(request.method, request.url.path)

                if handler is None:
                    await self._send_response(writer, JSONResponse({'error': 'not found'}, status_code=404))
                    continue

                # ── Run middleware chain ───────────────────────────────────
                try:
                    response = await self._run_middleware(request, handler)
                except Exception as exc:
                    if self._exception_handler:
                        try:
                            response = await self._exception_handler(request, exc)
                        except Exception:
                            response = JSONResponse({'error': 'internal server error'}, status_code=500)
                    else:
                        response = JSONResponse({'error': 'internal server error'}, status_code=500)

                # ── Send response (with gzip if client supports it) ────────
                ae = request.headers.get('accept-encoding', '')
                await self._send_response(writer, response, ae)

                # ── Keep-alive check ──────────────────────────────────────
                connection = request.headers.get('connection', '').lower()
                if connection == 'close':
                    break
                # HTTP/1.0 defaults to close unless explicitly keep-alive
                if request.http_version == 'HTTP/1.0' and connection != 'keep-alive':
                    break

        except (ConnectionError, OSError, asyncio.CancelledError, asyncio.LimitOverrunError):
            pass
        finally:
            if self._conn_sem is not None:
                self._conn_sem.release()
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _run_middleware(self, request, handler):
        """Run the middleware chain around the handler."""
        async def _final(req):
            result = await handler(req)
            if isinstance(result, (dict, list)):
                return JSONResponse(result)
            return result

        chain = _final
        for mw in reversed(self._middleware):
            prev = chain
            def make_chain(mw_inst, nxt):
                async def chain_fn(req):
                    return await mw_inst(req, nxt)
                return chain_fn
            chain = make_chain(mw, prev)

        return await chain(request)

    # ── Server Startup ─────────────────────────────────────────────────────

    def run(self, host: str = '0.0.0.0', port: int = 5000,
            ssl_cert: str | None = None, ssl_key: str | None = None,
            log_level: str = 'warning',
            on_startup: Callable | None = None) -> None:
        """Run the server (blocking). Compatible with uvicorn's run() interface."""
        ssl_context = None
        if ssl_cert and ssl_key:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            # Enforce TLS 1.2+ (reject legacy 1.0/1.1 with BEAST/POODLE issues).
            ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
            ssl_context.load_cert_chain(ssl_cert, ssl_key)

        async def _serve():
            self._conn_sem = asyncio.Semaphore(self.MAX_CONNECTIONS)
            if on_startup:
                on_startup(asyncio.get_running_loop())
            server = await asyncio.start_server(
                self._handle_connection, host, port,
                ssl=ssl_context
            )
            addrs = ', '.join(str(s.getsockname()) for s in server.sockets)
            if log_level != 'warning':
                print(f'  PortDesk HTTP server running on {addrs}', flush=True)
            async with server:
                await server.serve_forever()

        # uvloop: 2-4x faster event loop on Linux/macOS (libuv-based). Windows
        # is unsupported — silently falls back to the stdlib selector loop.
        try:
            import uvloop  # type: ignore
            uvloop.install()
        except Exception:
            pass
        try:
            asyncio.run(_serve())
        except KeyboardInterrupt:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  Middleware Helpers — replaces Starlette BaseHTTPMiddleware
# ══════════════════════════════════════════════════════════════════════════════

def make_middleware(middleware_class):
    """Convert a class with async dispatch(request, call_next) to a middleware callable."""
    instance = middleware_class()
    async def mw(request, call_next):
        return await instance.dispatch(request, call_next)
    return mw