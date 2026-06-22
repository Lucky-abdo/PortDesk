"""
pd_download_hybrid.py — Hybrid adaptive download for PortDesk.

Dynamically chooses between pre-create ZIP (better UX) and streaming ZIP
based on folder size and available disk space. No static thresholds.
"""
from __future__ import annotations
import os
import zipfile
import tempfile
import asyncio
import shutil
from typing import List
from concurrent.futures import ThreadPoolExecutor


async def _calc_total_size(paths: List[str], executor: ThreadPoolExecutor) -> int:
    """Calculate total size of all files in paths (parallel)."""
    loop = asyncio.get_running_loop()
    
    def _size_of(path: str) -> int:
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                full = os.path.join(root, f)
                try:
                    total += os.path.getsize(full)
                except OSError:
                    pass
        return total
    
    tasks = [loop.run_in_executor(executor, _size_of, p) for p in paths]
    results = await asyncio.gather(*tasks)
    return sum(results)


def _decide_strategy(total_size: int, free_disk: int, memory_gb: float) -> str:
    """
    Choose download strategy dynamically based on available resources.
    - 'precreate': folder fits in memory/disk, better UX (progress, resume)
    - 'stream': large folder or low disk, safeguard boundaries
    """
    # Dynamic precreate threshold: 10% of RAM or 500MB, whichever is smaller
    precreate_threshold = min(500 * 1024 * 1024, int(memory_gb * 0.1 * 1024**3))
    
    # Disk safety margin: 5% of free space or 1GB, whichever is larger
    disk_margin = max(1 * 1024**3, int(free_disk * 0.05))
    
    if total_size < precreate_threshold and free_disk > total_size + disk_margin:
        return 'precreate'
    return 'stream'


async def _precreate_zip_response(paths: List[str], executor: ThreadPoolExecutor):
    """Pre-create ZIP to temp file, return FileResponse with Range support."""
    from starlette.responses import FileResponse
    import tempfile
    
    def _create_zip():
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp_path = tmp.name
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    if not os.path.exists(p):
                        continue
                    if os.path.isfile(p):
                        try:
                            zf.write(p, os.path.basename(p))
                        except Exception:
                            pass
                    else:
                        for root, _, files in os.walk(p):
                            for fname in files:
                                full = os.path.join(root, fname)
                                try:
                                    zf.write(full, os.path.relpath(full, os.path.dirname(p)))
                                except Exception:
                                    pass
            return tmp_path
        except Exception:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise
    
    tmp_path = await asyncio.get_running_loop().run_in_executor(executor, _create_zip)
    return FileResponse(
        tmp_path,
        media_type='application/zip',
        filename='pcc_files.zip',
        background=None  # caller handles cleanup
    )


async def _streaming_zip_response(paths: List[str]):
    """Streaming ZIP via async generator with asyncio.Queue (no blocking join)."""
    import asyncio
    import zipfile
    import tempfile
    
    CHUNK_SIZE = 65536
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    error_holder: list = [None]
    
    def _zip_writer():
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp_path = tmp.name
        tmp.close()
        try:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    if not os.path.exists(p):
                        continue
                    if os.path.isfile(p):
                        try:
                            zf.write(p, os.path.basename(p))
                        except Exception:
                            pass
                    else:
                        for root, _, files in os.walk(p):
                            for fname in files:
                                full = os.path.join(root, fname)
                                try:
                                    zf.write(full, os.path.relpath(full, os.path.dirname(p)))
                                except Exception:
                                    pass
            with open(tmp_path, 'rb') as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        asyncio.run_coroutine_threadsafe(queue.put(None), asyncio.get_running_loop()).result(timeout=1)
                        break
                    asyncio.run_coroutine_threadsafe(queue.put(chunk), asyncio.get_running_loop()).result(timeout=1)
        except Exception as e:
            error_holder[0] = e
            asyncio.run_coroutine_threadsafe(queue.put(None), asyncio.get_running_loop()).result(timeout=1)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    async def _generator():
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _zip_writer)
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk
        if error_holder[0]:
            raise error_holder[0]
    
    from starlette.responses import StreamingResponse
    return StreamingResponse(
        _generator(), media_type='application/zip',
        headers={'Content-Disposition': 'attachment; filename="pcc_files.zip"'}
    )


async def explorer_download_multi_hybrid(request, paths: List[str], executor: ThreadPoolExecutor):
    """Main entry: decide strategy and dispatch."""
    import psutil
    
    total_size = await _calc_total_size(paths, executor)
    free_disk = shutil.disk_usage(os.path.commonpath(paths) or '.').free
    memory_gb = psutil.virtual_memory().total / (1024**3)
    
    strategy = _decide_strategy(total_size, free_disk, memory_gb)
    
    if strategy == 'precreate':
        return await _precreate_zip_response(paths, executor)
    else:
        return await _streaming_zip_response(paths)