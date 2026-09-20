"""
sync_tags_to_mongo.py
=====================
After using Mp3tag (or any external tagger) to write TXXX:SPOTIFY_ID to
Library files, run this to sync the tag back into the MongoDB library_index.

What it does:
  - Reads TXXX:SPOTIFY_ID from each Library/*.mp3 ID3 tag
  - Finds the matching library_index document by filename (stable key)
  - Updates ONLY spotify_id + last_seen — nothing else is touched
  - identity_key, content_hash, final_path, genre_folder are all left alone
  - PHASE 1G: before overwriting an existing spotify_id, resolves what the
    incoming ID actually represents on Spotify and checks it against the
    document's own title/artist. A material disagreement BLOCKS the write
    and is reported for manual review instead of silently overwriting a
    correct identity with a wrong one (docs/PHASE_1F_SPOTIFY_IDENTITY_ROOT_CAUSE.md
    §4a/§7 — this exact blind-trust path is how one wrong ID3 tag reached
    MongoDB for "Pepas.mp3").

What it does NOT do:
  - Create new documents (uses update_one, not upsert)
  - Change identity_key (would violate the unique index)
  - Touch audio_features, fingerprints, or any other field

Run:
    python sync_tags_to_mongo.py --dry-run          (preview — nothing written)
    python sync_tags_to_mongo.py                    (execute all Library folders)
    python sync_tags_to_mongo.py --folder Bollywood (single folder only)
"""
from __future__ import annotations
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))
import os
os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("REDIRECT_URI", "http://127.0.0.1:8888/callback")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:5173")

from config import config
from database import get_library_index_collection
from mutagen.id3 import ID3, ID3NoHeaderError

BASE = Path(config.BASE_DOWNLOAD_DIR)
LIB  = BASE / "Library"
DRY  = "--dry-run" in sys.argv

# Minimum title/artist similarity (SequenceMatcher ratio) for an incoming
# Spotify identity to be considered compatible with an existing Mongo
# document's title/artist. Deliberately lower than the stricter matching
# bars used elsewhere (e.g. backfill_gemini's CONF_ACCEPT_WARN=0.75) —
# this check only needs to catch clear disagreement (different songs), not
# rank candidates, so a looser bar avoids false-blocking legitimate minor
# title variants (e.g. "Chaleya" vs "Chaleya (From \"Jawan\")").
IDENTITY_COMPAT_THRESHOLD = 0.55

# Optional single-folder filter
FOLDER: str | None = None
_args = sys.argv[1:]
if "--folder" in _args:
    _i = _args.index("--folder")
    if _i + 1 < len(_args):
        FOLDER = _args[_i + 1]


def _read_spotify_id(path: Path) -> str:
    try:
        tags  = ID3(str(path))
        frame = tags.get("TXXX:SPOTIFY_ID")
        return str(frame.text[0]).strip() if frame and frame.text else ""
    except (ID3NoHeaderError, Exception):
        return ""


def _identity_compatible(
    existing_title: str,
    existing_artist: str,
    candidate_title: str,
    candidate_artist: str,
    threshold: float = IDENTITY_COMPAT_THRESHOLD,
) -> tuple[bool, str]:
    """
    Pure compatibility check: does a candidate Spotify track's title/artist
    reasonably agree with what Mongo already believes this record is?

    No prior identity on record (existing title AND artist both blank) is
    always compatible — there is nothing to conflict with, so a fresh tag
    is let through.

    When an existing title IS on record, it must agree with the candidate
    title (by similarity ratio or substring containment, to tolerate
    suffixes like "Chaleya" vs "Chaleya (From \"Jawan\")") — a strong
    artist match alone is deliberately NOT sufficient to override a title
    disagreement. This mirrors strict_matcher.py's ARTIST_GATE/TITLE_GATE
    rationale (see that file's comments): an artist releases many
    different songs, so matching artist says nothing about which song
    this is. This is exactly what must catch the real Phase 1F case —
    existing "Beba"/"Farruko" vs a candidate resolving to "Pepas"/"Farruko"
    shares the artist but is a different song, and must still block.

    Only when there's no existing title to compare (title unknown) does
    this fall back to artist agreement alone.
    """
    from services.legacy_identification_service import normalize_for_search

    ex_title  = normalize_for_search(existing_title)
    ex_artist = normalize_for_search(existing_artist)
    if not ex_title and not ex_artist:
        return True, "no prior identity on record — nothing to conflict with"

    cand_title  = normalize_for_search(candidate_title)
    cand_artist = normalize_for_search(candidate_artist)

    title_sim  = SequenceMatcher(None, ex_title, cand_title).ratio() if ex_title and cand_title else 0.0
    artist_sim = SequenceMatcher(None, ex_artist, cand_artist).ratio() if ex_artist and cand_artist else 0.0
    title_contains = bool(ex_title and cand_title and (ex_title in cand_title or cand_title in ex_title))

    if not ex_title:
        # Nothing to compare the title against — fall back to artist alone.
        if artist_sim >= threshold:
            return True, f"artist_sim={artist_sim:.2f} (no existing title on record)"
        return False, (
            f"material disagreement: no existing title on record, and artist "
            f"'{existing_artist}' vs candidate artist '{candidate_artist}' "
            f"(artist_sim={artist_sim:.2f})"
        )

    if title_sim >= threshold or title_contains:
        return True, f"title_sim={title_sim:.2f} artist_sim={artist_sim:.2f} title_contains={title_contains}"

    return False, (
        f"material disagreement: existing='{existing_title}'/'{existing_artist}' vs "
        f"candidate='{candidate_title}'/'{candidate_artist}' "
        f"(title_sim={title_sim:.2f} artist_sim={artist_sim:.2f})"
    )


def _resolve_spotify_identity(spotify_id: str) -> tuple[str, str]:
    """Look up what a Spotify track ID actually represents. Returns
    ("", "") on any failure — callers must treat that as "could not verify"
    and fail safe (block), never as "compatible."""
    try:
        from services.spotify_service import get_spotify_service
        sp = get_spotify_service()
        track = sp._call_with_backoff(sp.sp.track, spotify_id)
        artists = (track or {}).get("artists", [])
        return (track or {}).get("name", ""), (artists[0]["name"] if artists else "")
    except Exception as e:
        print(f"    [spotify-warn] could not resolve {spotify_id}: {e}")
        return "", ""


def main() -> None:
    col  = get_library_index_collection()
    scan = LIB / FOLDER if FOLDER else LIB

    print(f"Syncing TXXX:SPOTIFY_ID → MongoDB  DRY_RUN={DRY}")
    print(f"Scanning: {scan.relative_to(BASE)}\n")

    updated = already_ok = no_tag = no_doc = blocked = 0
    blocked_detail: list[dict] = []

    for f in sorted(scan.rglob("*.mp3")):
        new_sid = _read_spotify_id(f)

        if not new_sid:
            no_tag += 1
            continue

        doc = col.find_one({"filename": f.name}, {"_id": 1, "spotify_id": 1, "title": 1, "artist": 1})
        if not doc:
            no_doc += 1
            continue

        old_sid = doc.get("spotify_id", "")
        if old_sid == new_sid:
            already_ok += 1
            continue

        # PHASE 1G: verify the incoming identity before overwriting.
        cand_title, cand_artist = _resolve_spotify_identity(new_sid)
        if not cand_title and not cand_artist:
            compatible, reason = False, "could not verify candidate identity (Spotify lookup failed)"
        else:
            compatible, reason = _identity_compatible(
                doc.get("title", ""), doc.get("artist", ""), cand_title, cand_artist,
            )

        if not compatible:
            blocked += 1
            print(f"  [BLOCKED] {f.name}")
            print(f"    {old_sid!r} → {new_sid!r}  REJECTED: {reason}")
            blocked_detail.append({
                "filename": f.name, "path": str(f),
                "old_spotify_id": old_sid, "new_spotify_id": new_sid,
                "existing_title": doc.get("title", ""), "existing_artist": doc.get("artist", ""),
                "candidate_title": cand_title, "candidate_artist": cand_artist,
                "reason": reason,
            })
            continue

        print(f"  {f.name}")
        print(f"    {old_sid!r} → {new_sid!r}   ({reason})")

        if not DRY:
            col.update_one(
                {"_id": doc["_id"]},
                {"$set": {
                    "spotify_id": new_sid,
                    "last_seen":  datetime.now(timezone.utc),
                }}
            )
        updated += 1

    print(f"\nResults:")
    print(f"  Updated in MongoDB : {updated}")
    print(f"  Already correct    : {already_ok}")
    print(f"  Blocked (conflict) : {blocked}")
    print(f"  No ID3 tag (skip)  : {no_tag}")
    print(f"  Not in DB (skip)   : {no_doc}")
    if DRY:
        print("\n(DRY RUN — nothing was written)")

    if blocked_detail:
        reports_dir = Path(__file__).parent / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        out_path = reports_dir / "sync_tags_conflicts.json"
        out_path.write_text(json.dumps(blocked_detail, indent=2), encoding="utf-8")
        print(f"\n{len(blocked_detail)} blocked record(s) flagged for review -> {out_path}")


if __name__ == "__main__":
    main()
