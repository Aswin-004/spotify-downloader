#!/usr/bin/env python3
"""Compute (and cache) the audio feature vector of every track in the library. Read-only, resumable.

    python analyze_audio.py                # whole library, 10 workers
    python analyze_audio.py --jobs 6       # gentler on the CPU
"""
import argparse
import os
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="library root (default: BASE_DOWNLOAD_DIR)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    args = ap.parse_args()

    from config import config
    from services import audio_genre_features as ag

    root = Path(args.root or config.BASE_DOWNLOAD_DIR)
    paths = sorted(str(p) for p in (root / "Library").rglob("*.mp3") if p.is_file() and "_TO_DELETE" not in p.parts)
    cache = ag.FeatureCache()
    pending = sum(1 for p in paths if not cache.has(p))
    print(f"{len(paths)} tracks under {root / 'Library'}; {len(paths) - pending} already cached, {pending} to analyse "
          f"with {args.jobs} workers (~{pending * 1.9 / args.jobs / 60:.0f} min)", flush=True)
    t0 = time.time()
    out = ag.extract_many(paths, cache, n_jobs=args.jobs,
                          progress=lambda d, n: print(f"  {d}/{n}  ({time.time() - t0:.0f}s)", flush=True))
    bad = [p for p, v in out.items() if v is None]
    print(f"done in {time.time() - t0:.0f}s — features for {len(paths) - len(bad)} tracks; {len(bad)} could not be decoded", flush=True)
    for p in bad[:15]:
        print("  undecodable:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
