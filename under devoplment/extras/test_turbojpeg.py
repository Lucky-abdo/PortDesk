"""
test_turbojpeg.py
Developer benchmark script for comparing TurboJPEG vs OpenCV JPEG encoding performance.

This script is designed to give reliable and repeatable results.
It supports the modular structure of PortDesk (can be run from extras/ or root).

Usage:
    python extras/test_turbojpeg.py
"""

from turbojpeg import TurboJPEG, TJPF_BGR, TJSAMP_444
import numpy as np
import time
import cv2

# ── Try to use pd_config for flexibility (if available) ─────────
try:
    import pd_config as _pd_config
    BASE_DIR = _pd_config.BASE_DIR
except ImportError:
    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Benchmark Settings ─────────────────────────────────────────
WARMUP_RUNS  = 4
BENCH_RUNS   = 25
TEST_RES     = [(1280, 720), (1920, 1080)]
TEST_QUALITY = [45, 65, 85]


def bench(fn, runs=BENCH_RUNS):
    """Run function multiple times and return (avg, min, max) in ms."""
    for _ in range(WARMUP_RUNS):
        fn()
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return sum(times) / len(times), min(times), max(times)


def main():
    print("🔍 TurboJPEG vs OpenCV Benchmark\n")

    try:
        tj = TurboJPEG()
        print("✅ TurboJPEG initialized successfully\n")
    except Exception as e:
        print("❌ TurboJPEG not available.")
        print("   Install: pip install PyTurboJPEG")
        print("   Then install system library:")
        print("     Linux:   sudo apt install libturbojpeg-dev")
        print("     macOS:   brew install jpeg-turbo")
        print("     Windows: Download from libjpeg-turbo GitHub releases")
        return

    print(f"{'Resolution':<14} {'Q':<4} {'Library':<12} {'Avg ms':<8} {'Min ms':<8} {'Max ms':<8}")
    print("-" * 60)

    for w, h in TEST_RES:
        # Use the SAME frame for fair comparison
        frame = np.random.randint(0, 256, (h, w, 3), dtype=np.uint8)

        for q in TEST_QUALITY:
            # ── TurboJPEG ─────────────────────────────────────
            avg_tj, min_tj, max_tj = bench(
                lambda: tj.encode(frame, quality=q, jpeg_subsample=TJSAMP_444, pixel_format=TJPF_BGR)
            )
            print(f"{w}x{h:<6} {q:<4} {'TurboJPEG':<12} {avg_tj:>6.2f}  {min_tj:>6.2f}  {max_tj:>6.2f}")

            # ── OpenCV ────────────────────────────────────────
            avg_cv, min_cv, max_cv = bench(
                lambda: cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1].tobytes()
            )
            print(f"{w}x{h:<6} {q:<4} {'OpenCV':<12} {avg_cv:>6.2f}  {min_cv:>6.2f}  {max_cv:>6.2f}")

            speedup = avg_cv / avg_tj if avg_tj > 0 else 0
            print(f"  → TurboJPEG is {speedup:.1f}x faster at {w}x{h} Q{q}\n")

    print("🎉 Benchmark completed. TurboJPEG is recommended for streaming.")


if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")   
