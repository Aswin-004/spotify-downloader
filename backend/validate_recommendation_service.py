"""
backend/validate_recommendation_service.py

Phase 0 — real-library validation script for services/recommendation_service.py.

WHY THIS SCRIPT EXISTS
-----------------------
recommendation_service.py was built and unit-tested (52 tests, all passing —
see tests/test_recommendation_service.py) in a sandbox that has no access to
this project's real MongoDB instance or its real Python virtual environment
(no pymongo, etc). That sandbox could fully verify the algorithm's logic
against hand-built data, but Phase 0's Step 8 (validate against the real
library) and Step 10 (real-world performance) require actually connecting
to your MongoDB and reading your real library_index collection — something
only runnable from your own machine, with your own .env populated.

This script closes that gap. Run it locally:

    cd backend
    python validate_recommendation_service.py

Requirements: your normal backend virtual environment (the one with
pymongo installed) and a working backend/.env with MONGODB_URI set, same
as running the app itself.

SAFETY
------
This script is READ-ONLY. It only calls get_library_index_collection().find()
— it never writes, updates, or deletes anything in the database. It never
prints MONGODB_URI, GROQ_API_KEY, or any other secret; it does not read
backend/.env directly at all — database.py does that internally.
"""
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from database import get_library_index_collection  # noqa: E402
from services.recommendation_service import (  # noqa: E402
    recommend_next,
    generate_playlist_sequence,
    _is_scoreable,
)


def _fetch_all_docs():
    col = get_library_index_collection()
    return list(col.find(
        {},
        {"identity_key": 1, "title": 1, "artist": 1, "genre_folder": 1,
         "audio_features": 1, "missing": 1, "_id": 0},
    ))


def main():
    print("=" * 70)
    print("Phase 0 -- recommendation_service.py real-library validation")
    print("=" * 70)

    t0 = time.perf_counter()
    docs = _fetch_all_docs()
    fetch_ms = (time.perf_counter() - t0) * 1000
    print(f"\nFetched {len(docs)} library_index documents in {fetch_ms:.1f} ms")

    with_af = [d for d in docs if d.get("audio_features")]
    scoreable = [d for d in with_af if _is_scoreable(d)]
    missing_flagged = sum(1 for d in docs if d.get("missing") is True)
    low_conf = [
        d for d in scoreable
        if isinstance(d["audio_features"].get("confidence"), (int, float))
        and d["audio_features"]["confidence"] < 0.5
    ]

    print(f"  with audio_features:         {len(with_af)}")
    print(f"  scoreable (bpm + camelot):   {len(scoreable)}")
    print(f"  missing=True (soft-deleted): {missing_flagged}")
    if scoreable:
        pct = len(low_conf) / len(scoreable) * 100
        print(f"  scoreable w/ confidence<0.5: {len(low_conf)} ({pct:.1f}%)")

    if not scoreable:
        print("\nNo scoreable tracks found (need both bpm and camelot) -- "
              "nothing further to validate. Run backfill_audio_features "
              "first if you expect audio analysis to be present.")
        return

    # -- recommend_next() correctness spot-check on real data --------------
    print("\n" + "-" * 70)
    print("recommend_next() spot-check (first 5 scoreable tracks)")
    print("-" * 70)
    sample = scoreable[:5]
    for d in sample:
        ik = d["identity_key"]
        t0 = time.perf_counter()
        result = recommend_next(ik, top_n=5, docs=docs)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        print(f"\n  current: {d.get('artist', '')} - {d.get('title', '')} [{ik}]")
        print(f"  considered={result['candidates_considered']}  "
              f"excluded_missing={result['candidates_excluded_missing_flag']}  "
              f"excluded_incomplete={result['candidates_excluded_incomplete_metadata']}  "
              f"({elapsed_ms:.1f} ms)")
        if result["error"]:
            print(f"  ERROR: {result['error']}")
            continue
        for rec in result["recommendations"]:
            weak = " [WEAK]" if rec["is_weak_transition"] else ""
            lowc = " [LOW-CONF-KEY]" if rec["key_confidence_low"] else ""
            print(f"    -> {rec['artist']} - {rec['title']}  "
                  f"score={rec['score']}  camelot={rec['camelot']}  "
                  f"bpm={rec['bpm']}  jump={rec['bpm_jump']}{weak}{lowc}")

    # -- performance timing over a larger sample ----------------------------
    print("\n" + "-" * 70)
    print("Performance timing")
    print("-" * 70)
    perf_sample = scoreable[:50] if len(scoreable) >= 50 else scoreable
    timings_ms = []
    for d in perf_sample:
        t0 = time.perf_counter()
        recommend_next(d["identity_key"], top_n=5, docs=docs)
        timings_ms.append((time.perf_counter() - t0) * 1000)
    if timings_ms:
        print(f"  recommend_next() over {len(timings_ms)} calls "
              f"(library pool size {len(docs)}):")
        print(f"    mean={statistics.mean(timings_ms):.2f} ms  "
              f"median={statistics.median(timings_ms):.2f} ms  "
              f"max={max(timings_ms):.2f} ms")

    t0 = time.perf_counter()
    seq = generate_playlist_sequence(sample[0]["identity_key"], length=20,
                                      flow="mixed", _docs=docs)
    seq_ms = (time.perf_counter() - t0) * 1000
    print(f"\n  generate_playlist_sequence(length=20) over full pool: "
          f"{seq_ms:.1f} ms -> {seq.get('length_achieved', '?')} tracks")

    print("\n" + "=" * 70)
    print("Done. No writes were made to the database.")
    print("=" * 70)


if __name__ == "__main__":
    main()
