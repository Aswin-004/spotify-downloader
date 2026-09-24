#!/usr/bin/env python3
"""
library_resort.py
=================
Find every track sitting in the wrong genre folder — across the WHOLE library, not just
Library/Electronic — and move it to the right one, on your say-so. Fully undoable.

  1) scan   READ-ONLY. Classifies every track, writes a report + a spreadsheet, moves nothing.

       python library_resort.py scan
       python library_resort.py scan --offline          (skip Last.fm; curated tables + hints only)
       python library_resort.py scan --no-recover       (do not look up missing artists on Spotify)

  2) apply  PREVIEW by default. --yes really moves. Reads the report, or the spreadsheet you edited.

       python library_resort.py apply --report reports/resort_<time>.json
       python library_resort.py apply --report ... --yes
       python library_resort.py apply --report ... --csv reports/resort_<time>.csv --yes
       python library_resort.py apply --report ... --include-review --skip 12,40-44 --fix-tags --yes

  3) undo   Puts every file (and its tags) back exactly where the manifest says it was.

       python library_resort.py undo --manifest reports/resort_undo_<time>.json --yes

  4) learn  Turns your corrected folders into routing rules for FUTURE songs: every artist with >= 3
            tracks and >= 80% of them in one crate is remembered, so new songs by them land there.

       python library_resort.py learn --report reports/resort_<time>.json          (preview)
       python library_resort.py learn --report reports/resort_<time>.json --yes

What the scan does, in order
  * Recovers missing artists. Tracks whose artist tag is "Unknown", empty, or just a genre word
    ("Electronic") cannot be judged by artist. If the file has a Spotify id, the real artist is
    looked up (one paced, capped, cached call per track; stops at the first rate limit).
  * Classifies each track by evidence voting (services/genre_evidence.py): curated artist
    tables, Last.fm tags, remixer / script / title hints, BPM plausibility.
  * Sorts every track into one of:
       ok       already in the right folder                                 (left alone)
       MOVE     confident AND backed by a trusted source (curated table or   (moved by --yes)
                track-level evidence) and it points to a different folder
       review   a different folder is suggested, but only on weaker evidence  (never moved unless
                (artist-level tags alone), or it crosses Bollywood/Punjabi/    you include it)
                Tamil — a matter of taste, not fact
       ear      (scan --audio) nothing in the metadata could place it, but it SOUNDS   (moved only with
                like a different crate — judged by a model trained on YOUR confirmed    apply --include-audio)
                tracks and scored on tracks it never saw
       suspect  the tempo/length does not fit the folder, or it sounds like another crate (you decide)
       unknown  no confident answer                                           (left alone)

The spreadsheet has an `apply` column pre-filled `yes` for MOVE rows. Edit it in Excel — tick
review rows, un-tick MOVE rows, even type a different `proposed_folder` — then apply --csv.

Safety
  * Nothing moves without --yes. Files are never overwritten (collision-safe names).
  * Every move is recorded in reports/resort_undo_<time>.json BEFORE the run ends, including the
    old tags, so `undo` can reverse it.
  * The genre tag (TCON) is updated to the new folder so Rekordbox/Windows agree with the
    folder (other tools read it, and a stale tag would send the file back). Opt out with
    --no-genre-tag. Artist tags are only rewritten with --fix-tags.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

# Folders that are not genre crates (or that you curate by hand): never scanned.
SKIP_FOLDERS = ("NeedsReview", "Quarantine", "Manual", "Duplicates", "_TO_DELETE", "PSY")
INDIAN = frozenset({"Bollywood", "Punjabi", "Tamil", "Indian Hip Hop"})     # moving between these is taste, not fact
GLOBAL = frozenset({"Pop", "R&B", "International Hip Hop", "Latin"})       # ditto: The Weeknd is Pop AND R&B

MOVE_CONFIDENCE = 0.75
REVIEW_CONFIDENCE = 0.50

# folder -> the ID3 genre (TCON) text written to a file when it is filed in that folder
FOLDER_TO_TCON = {
    "Bollywood": "Bollywood", "Drum & Bass": "Drum and Bass", "Dubstep": "Dubstep",
    "Electronic": "Electronic", "Grime": "Grime", "House": "House",
    "Indian Hip Hop": "Indian Hip Hop", "International Hip Hop": "International Hip Hop",
    "Latin": "Latin", "Pop": "Pop", "Punjabi": "Punjabi", "R&B": "R&B", "Tamil": "Tamil",
    "Techno": "Techno", "Trance": "Trance", "UK Garage": "UK Garage",
}

_TRUTHY = {"y", "yes", "1", "true", "x", "ok", "move"}

# genre-tag text -> crate. A file whose tag names a DIFFERENT crate than the one it sits in was, in
# practice, moved by hand after the pipeline tagged it (measured: 124 such files on the real
# library, incl. 72 whose tag still says "Electronic" — moved out of the catch-all by the user).
_TCON_ALIASES = {"drum & bass": "Drum & Bass", "dnb": "Drum & Bass", "hiphop": "International Hip Hop",
                 "hip-hop": "International Hip Hop", "hip hop": "International Hip Hop",   # tags written before the split
                 "desi hip hop": "Indian Hip Hop",
                 "rnb": "R&B", "garage": "UK Garage", "psytrance": "Trance", "psy": "Trance"}
TCON_TO_FOLDER = {v.lower(): k for k, v in FOLDER_TO_TCON.items()} | _TCON_ALIASES

# A title that says it is an Indian-language version keeps a track in the Indian crates, whatever
# the (Western) artist's own genre is: "Ride It (Hindi Version)" by Jay Sean belongs in Bollywood.
_INDIAN_VERSION_RE = re.compile(r"\b(?:hindi|punjabi|tamil|telugu|desi|bollywood)\b", re.IGNORECASE)


def tag_folder_of(genre_tag: str) -> str:
    """The crate a genre tag names ('' when empty / unknown text)."""
    return TCON_TO_FOLDER.get((genre_tag or "").strip().lower(), "")


def read_genre_tag(path: str) -> str:
    try:
        from mutagen.id3 import ID3
        f = ID3(path).get("TCON")
        return str(f.text[0]).strip() if f is not None and getattr(f, "text", None) else ""
    except Exception:
        return ""



# ── the ledger of YOUR decisions ────────────────────────────────────────────────────────────────
# A file whose genre tag names another crate was moved by hand — but syncing that tag to the folder (which
# `apply --sync-genre-tags` does, so other tools stop undoing your work) ERASES that evidence, and the
# suggestion that you overrode would come straight back. So every file whose tag is synced is remembered
# here, keyed by the AUDIO (name + audio bytes: stable across moves and tag edits) -> the crate you chose.
USER_PLACED_PATH = REPORTS_DIR / "user_placed.json"


def placement_key(path: str) -> str:
    from services.audio_genre_features import file_key
    return file_key(path)


def load_user_placed(path: Optional[Path] = None) -> Dict[str, str]:
    try:
        return dict(json.loads(Path(path or USER_PLACED_PATH).read_text(encoding="utf-8")))
    except Exception:
        return {}


def save_user_placed(mapping: Dict[str, str], path: Optional[Path] = None) -> None:
    p = Path(path or USER_PLACED_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(mapping, indent=0, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def is_hand_placed(row) -> bool:
    """You put this file in its folder: its tag names a different crate, or the ledger says so."""
    get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
    return bool(get("hand_placed")) or bool(get("tag_folder") and get("tag_folder") != get("folder"))


def routable_labels() -> Set[str]:
    """Folders a track may be sent to: every taxonomy crate except the catch-all."""
    from services.genre_router import GENRE_TAXONOMY
    return {sub for (_family, sub) in GENRE_TAXONOMY.values()} - {"Electronic"}


# ═══════════════════════════════════════════════════════════════════════════
# data
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Row:
    idx: int = 0
    path: str = ""
    rel: str = ""
    folder: str = ""                       # the crate it sits in now
    artist: str = ""                       # artist TAG (may be a placeholder)
    title: str = ""
    bpm: Optional[float] = None
    spotify_id: str = ""
    recovered_artist: str = ""             # real artist found via the Spotify id
    artist_used: str = ""                  # what the classifier was actually given
    action: str = "unknown"                # ok | move | review | suspect | unknown
    proposed: str = ""                     # destination crate for move / review
    confidence: float = 0.0
    verified: bool = False
    sources: List[str] = field(default_factory=list)
    why: str = ""
    note: str = ""
    file_secs: Optional[float] = None      # length of the audio file
    spotify_title: str = ""                # what Spotify calls the track behind the id in the tag
    spotify_secs: Optional[float] = None
    id_check: str = ""                     # "ok" | why the id / recovered artist was NOT trusted | how it was matched
    genre_tag: str = ""                    # the file's TCON text
    tag_folder: str = ""                   # the crate that tag names ('' if empty/unknown)
    audio_genre: str = ""                  # what the audio model thinks it SOUNDS like
    audio_conf: float = 0.0
    hand_placed: bool = False              # the ledger says YOU chose this folder (see USER_PLACED_PATH)


LENGTH_SUSPECT_SECS = 30.0                 # a right-titled file this far off Spotify's length is suspect
PLAYLIST_MATCH_SECS = 3.0                  # title + length window for identifying a file from the playlist


def file_seconds(path: str) -> Optional[float]:
    try:
        from mutagen.mp3 import MP3
        return float(MP3(path).info.length)
    except Exception:
        return None


def load_playlist(loader: Optional[Callable[[], List[dict]]] = None,
                  out: Callable[[str], None] = print) -> List[dict]:
    """The ingest playlist's tracks (id/title/artist/duration_ms); [] when unavailable — the scan
    then simply runs without playlist help."""
    try:
        if loader is None:
            from config import config
            if not config.INGEST_PLAYLIST_ID:
                return []
            from services.spotify_service import get_spotify_service
            return list(get_spotify_service().get_playlist_tracks_by_id(config.INGEST_PLAYLIST_ID))
        return list(loader())
    except Exception as exc:
        out(f"  (playlist unavailable — continuing without it: {exc})")
        return []


def match_playlist(title: str, secs: Optional[float], tracks: List[dict]) -> Optional[dict]:
    """The playlist track that is unambiguously this file: same core title AND length within a few
    seconds. None when nothing matches or two DIFFERENT artists both do."""
    from services.strict_matcher import same_song_title

    if not secs or not title:
        return None
    hits = [t for t in tracks
            if t.get("duration_ms") and abs(secs - t["duration_ms"] / 1000.0) <= PLAYLIST_MATCH_SECS
            and same_song_title(title, t.get("title", ""))]
    if not hits or len({(t.get("artist") or "").lower() for t in hits}) != 1:
        return None
    return hits[0]


def _same_soft_family(a: str, b: str) -> bool:
    return any(a in fam and b in fam for fam in (INDIAN, GLOBAL))


def apply_audio(rows: List[Row], predictions: Dict[str, Tuple[str, float]], model) -> Dict[str, int]:
    """
    Fuse the audio model's opinion into rows already classified from metadata.
    `predictions`: path -> (crate, confidence); for training tracks these are OUT-OF-FOLD.

      unknown / tempo-suspect   confident and different  -> "ear" (audio alone; only moved with --include-audio)
                                confident and the same    -> ok
      review (weak evidence)    audio agrees              -> move (two independent signals)
      move  (curated evidence)  audio confidently differs -> review (a coarse artist table vs the sound)
      ok                        audio confidently differs -> suspect (a possible wrong folder)
    Files you placed by hand (genre tag names another crate) are never touched: your call stands.
    A boundary inside Bollywood/Punjabi/Tamil or Pop/R&B/Hip Hop/Latin is a matter of taste, so audio
    never turns it into a move.
    """
    from services import audio_genre_model as am

    stats: Dict[str, int] = {"ear": 0, "confirmed": 0, "upgraded": 0, "downgraded": 0, "flagged": 0, "scored": 0}
    for r in rows:
        pred = predictions.get(r.path)
        if not pred:
            continue
        crate, conf = pred
        r.audio_genre, r.audio_conf = crate, round(conf, 3)
        stats["scored"] += 1
        if is_hand_placed(r):
            continue                                                       # placed by hand: never overruled
        if not am.acts(model, crate, conf):
            continue
        movable = am.acts_to_move(model, crate, conf)           # the stricter bar: may it MOVE on sound alone?
        tempo_only_suspect = r.action == "suspect" and "BPM" in r.note and "Spotify's version" not in r.note
        if crate == r.folder:
            if r.action == "unknown" or tempo_only_suspect:
                r.action = "ok"
                r.note = (r.note + "; " if r.note else "") + f"audio agrees ({conf:.0%})"
                stats["confirmed"] += 1
            continue
        soft = _same_soft_family(r.folder, crate)
        by_ear = f"sounds like {crate} ({conf:.0%})"
        if r.action == "unknown" or tempo_only_suspect:
            if not movable:
                continue                                            # not sure enough to place it on sound alone
            if soft:
                r.action, r.proposed = "review", crate
                r.note = (r.note + "; " if r.note else "") + f"{by_ear} — but that is a taste boundary"
            else:
                r.action, r.proposed = "ear", crate
                r.note = (r.note + "; " if r.note else "") + f"by ear: {by_ear}; no metadata could place it"
                r.sources = list(r.sources) + ["audio_model"]
                stats["ear"] += 1
        elif r.action == "review" and r.proposed == crate and not soft and "boundary" not in r.note and conf >= 0.6:
            r.action, r.verified = "move", True
            r.sources = list(r.sources) + ["audio_model"]
            r.note = (r.note + "; " if r.note else "") + f"audio agrees ({conf:.0%}) — two independent signals"
            stats["upgraded"] += 1
        elif r.action == "move" and r.proposed != crate and movable:
            r.action = "review"
            r.note = (r.note + "; " if r.note else "") + f"but it {by_ear}, not {r.proposed}"
            stats["downgraded"] += 1
        elif r.action == "ok" and not soft and movable:
            r.action = "suspect"
            r.note = (r.note + "; " if r.note else "") + f"folder says {r.folder} but it {by_ear}"
            stats["flagged"] += 1
    return stats


def run_audio(rows: List[Row], *, jobs: int = 8, out: Callable[[str], None] = print,
              features=None, model_path=None) -> Tuple[Optional[dict], Dict[str, int]]:
    """Analyse (cached), train on the confirmed tracks, score everything, fuse into `rows`.
    Returns (model report or None, fusion stats)."""
    import numpy as np
    from dataclasses import asdict as _asdict
    from services import audio_genre_features as ag, audio_genre_model as am

    paths = [r.path for r in rows]
    if features is None:
        cache = ag.FeatureCache()
        missing = sum(1 for p in paths if not cache.has(p))
        if missing:
            out(f"Analysing the audio of {missing} track(s) ({jobs} workers, ~{missing * 1.9 / jobs / 60:.0f} min)...")
        features = ag.extract_many(paths, cache, n_jobs=jobs,
                                   progress=lambda d, n: out(f"  audio {d}/{n}") if d % 300 == 0 or d == n else None)
    ts = am.build_training_set([_asdict(r) for r in rows], lambda p: features.get(p))
    try:
        model, _ = am.train(ts, ag.FEATURE_NAMES)
    except ValueError as exc:
        out(f"  (audio model skipped: {exc})")
        return None, {}
    am.save(model, model_path)
    preds = dict(model.oof)
    rest = [p for p in paths if p not in preds and features.get(p) is not None]
    if rest:
        X = np.asarray([features[p] for p in rest], dtype=float)
        preds.update(dict(zip(rest, am.predict(model, X))))
    return model.report, apply_audio(rows, preds, model)


def effective_artist(tag_artist: str, recovered: str) -> Tuple[str, str]:
    """(artist to classify with, where it came from: 'tag' | 'recovered' | 'none')."""
    from services.artist_recovery import is_placeholder_artist
    if not is_placeholder_artist(tag_artist):
        return tag_artist.strip(), "tag"
    if recovered and not is_placeholder_artist(recovered):
        return recovered.strip(), "recovered"
    return "", "none"


# ═══════════════════════════════════════════════════════════════════════════
# decision (pure)
# ═══════════════════════════════════════════════════════════════════════════

def decide_action(
    current: str,
    decision,
    bpm: Optional[float] = None,
    *,
    move_confidence: float = MOVE_CONFIDENCE,
    review_confidence: float = REVIEW_CONFIDENCE,
    routable: Optional[Set[str]] = None,
    artist_was_placeholder: bool = False,
    title: str = "",
) -> Tuple[str, str, str]:
    """(action, proposed_folder, note) for one track. `decision` is a genre_evidence.Decision.

    artist_was_placeholder: the file's artist TAG was missing/"Unknown"/"Indian"/a genre word. Such
    a track was routed on a guess (the taxonomy sends the generic "Indian" label to the Punjabi
    crate — on the real library that made 263 of Punjabi's 506 tracks unidentified songs), so its
    current folder carries no human judgment and the Bollywood/Punjabi/Tamil boundary rule below
    does not apply to it."""
    from services.genre_evidence import BPM_FAR_FACTOR, bpm_factor

    routable = routable if routable is not None else routable_labels()
    answer = "" if (decision is None or decision.abstain) else decision.genre

    if not answer:
        if bpm and bpm_factor(current, bpm) <= BPM_FAR_FACTOR:
            return "suspect", "", f"{bpm:g} BPM does not fit {current}"
        return "unknown", "", ""
    if answer == current:
        return "ok", "", ""
    if answer not in routable:
        return "unknown", "", f"engine suggested '{answer}', which is not a crate"

    if current in INDIAN and answer not in INDIAN and _INDIAN_VERSION_RE.search(title or ""):
        return "review", answer, "the title says it is an Indian-language version — it stays in the Indian crates"
    if not artist_was_placeholder:
        for family, label in ((INDIAN, "Bollywood/Punjabi/Tamil/Indian Hip Hop"),
                              (GLOBAL, "Pop/R&B/International Hip Hop/Latin")):
            if current in family and answer in family:
                return "review", answer, f"{label} boundary — a matter of taste, your call"
    if decision.verified and decision.confidence >= move_confidence:
        return "move", answer, ""
    if decision.confidence >= review_confidence:
        note = ("artist-level evidence only" if not decision.verified
                else f"confidence {decision.confidence:.0%} is below the {move_confidence:.0%} move bar")
        return "review", answer, note
    return "unknown", "", ""


# ═══════════════════════════════════════════════════════════════════════════
# scan
# ═══════════════════════════════════════════════════════════════════════════

def classify_rows(
    rows: List[Row],
    classify: Callable[[str, str, Optional[float], str], object],
    *,
    move_confidence: float = MOVE_CONFIDENCE,
    review_confidence: float = REVIEW_CONFIDENCE,
    progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Fill in action/proposed/confidence/... on every row. `classify(artist, title, bpm, path)`
    returns a Decision; one that raises is treated as an abstention."""
    from services.artist_recovery import is_placeholder_artist
    from services.genre_evidence import Decision

    routable = routable_labels()
    for i, row in enumerate(rows, 1):
        row.artist_used, _origin = effective_artist(row.artist, row.recovered_artist)
        try:
            decision = classify(row.artist_used, row.title, row.bpm, row.path)
        except Exception:
            decision = Decision(reason="classifier error")
        row.action, row.proposed, row.note = decide_action(
            row.folder, decision, row.bpm, move_confidence=move_confidence,
            review_confidence=review_confidence, routable=routable,
            artist_was_placeholder=is_placeholder_artist(row.artist), title=row.title,
        )
        if not row.artist_used:
            row.note = (row.note + "; " if row.note else "") + "no identifiable artist"
        # Your hand-sorting outranks any suggestion: a file whose genre tag names a different crate
        # than the one it sits in was moved there by a person. Never move it back.
        if is_hand_placed(row):
            why = (f"genre tag says {row.tag_folder}" if row.tag_folder and row.tag_folder != row.folder
                   else "your earlier sorting is on record")
            if row.action == "move":
                row.action = "review"
                row.note = (row.note + "; " if row.note else "") + (
                    f"{why} — you placed this in {row.folder} by hand, so it is left there")
            elif row.tag_folder and row.tag_folder != row.folder:
                row.note = (row.note + "; " if row.note else "") + (
                    f"genre tag says {row.tag_folder} but it sits in {row.folder} (moved by hand?)")
        row.confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
        row.verified = bool(getattr(decision, "verified", False))
        row.sources = list(getattr(decision, "sources", []) or [])
        row.why = decision.explain() if hasattr(decision, "explain") else ""
        if progress:
            progress(i, len(rows))


def scan(
    root: Path,
    *,
    folder: str = "Library",
    include_electronic: bool = False,
    skip_folders: Iterable[str] = SKIP_FOLDERS,
    limit: Optional[int] = None,
    recover: bool = True,
    fetch: Optional[Callable[[str], Optional[dict]]] = None,
    recovery_cache=None,
    max_calls: int = 300,
    pace_s: float = 1.0,
    playlist_tracks: Optional[List[dict]] = None,
    use_playlist: bool = True,
    spotify_calls: bool = True,
    audio: bool = False,
    audio_jobs: int = 8,
    itunes: bool = False,
    classify: Optional[Callable] = None,
    online: bool = True,
    use_musicbrainz: bool = False,
    move_confidence: float = MOVE_CONFIDENCE,
    out: Callable[[str], None] = print,
) -> Tuple[List[Row], dict]:
    """READ-ONLY. Returns (rows, summary)."""
    import eval_genre_classifier as ev
    from services import artist_recovery as ar

    exclude = list(skip_folders) + ([] if include_electronic else ["Electronic"])
    samples = ev.collect_samples(root, folder=folder, exclude=exclude, limit=limit)
    rows: List[Row] = []
    for s in samples:
        rows.append(Row(path=s.path, rel=s.rel, folder=s.label, artist=s.artist, title=s.title,
                        bpm=s.bpm, spotify_id=s.spotify_id))

    summary = {"placeholder_tracks": 0, "with_spotify_id": 0, "recovered": 0, "calls": 0,
               "from_cache": 0, "stopped": "", "playlist_tracks": 0, "ids_ok": 0, "ids_wrong": 0,
               "ids_unchecked_len": 0, "playlist_matched": 0, "length_suspects": 0}
    placeholders = [r for r in rows if ar.is_placeholder_artist(r.artist)]
    summary["placeholder_tracks"] = len(placeholders)
    summary["with_spotify_id"] = sum(1 for r in placeholders if r.spotify_id)

    # A Spotify 429 blocks the whole app for ~23 h and is remembered on disk: while it lasts (or with
    # --no-spotify-calls) nothing here may call Spotify — cached records and the cached playlist only.
    block_left = ar.block_seconds_left()
    spotify_off = (not spotify_calls) or block_left > 0
    off_reason = ""
    if spotify_off:
        off_reason = (f"Spotify is rate-limited for another {block_left / 3600:.1f} h" if block_left > 0
                      else "Spotify calls are switched off (--no-spotify-calls)")
        out(f"{off_reason}: using cached Spotify data only — no Spotify calls will be made.")
        try:
            from services.spotify_service import set_global_rate_limit
            set_global_rate_limit(max(block_left, 3600))       # the app's own guard: cache-only playlist read
        except Exception:
            pass

    # The playlist is free identity data: id -> artist/title/length, and a title+length index.
    tracks = playlist_tracks if playlist_tracks is not None else (load_playlist(out=out) if use_playlist else [])
    by_id = {t["id"]: t for t in tracks if t.get("id")}
    summary["playlist_tracks"] = len(by_id)
    placed = load_user_placed()
    for r in rows:
        r.file_secs = file_seconds(r.path)
        r.genre_tag = read_genre_tag(r.path)
        r.tag_folder = tag_folder_of(r.genre_tag)
        try:
            r.hand_placed = placed.get(placement_key(r.path)) == r.folder
        except OSError:
            r.hand_placed = False

    if recover and placeholders:
        if fetch is None:
            fetch = (lambda sid: None) if spotify_off else ar.spotify_fetcher()
        if recovery_cache is None:
            from services.genre_evidence import TagCache
            recovery_cache = TagCache(ar.DEFAULT_CACHE_PATH, ttl_days=3650)
        out(f"Recovering artists for {len(placeholders)} track(s) with no usable artist tag "
            f"({summary['with_spotify_id']} have a Spotify id)...")
        res = ar.recover_artists((r.spotify_id for r in placeholders), fetch, recovery_cache,
                                 known=ar.known_from_playlist(tracks), want_duration=True,
                                 max_calls=0 if spotify_off else max_calls, pace_s=pace_s, out=out)
        summary.update(calls=res.calls, from_cache=res.from_cache,
                       stopped=(off_reason + " — used cached data only") if spotify_off else res.stopped)
        for r in placeholders:
            hit = res.found.get(r.spotify_id) if r.spotify_id else None
            if not hit:
                continue
            r.spotify_title = hit.get("title", "")
            r.spotify_secs = (hit["duration_ms"] / 1000.0) if hit.get("duration_ms") else None
            ok, why = ar.validate_recovery(r.title, hit, r.file_secs)
            if ok:
                partial = r.spotify_id in res.partial
                r.recovered_artist = ar.join_artists(hit["artists"])
                r.id_check = "ok (title matches; length not checked)" if partial else "ok"
                summary["ids_ok"] += 1
                summary["ids_unchecked_len"] += 1 if partial else 0
            else:
                r.id_check = why                          # this id is junk: its artist is NOT used
                summary["ids_wrong"] += 1

    # Artist-less files that still have no trustworthy artist: try the playlist by title + length.
    if tracks:
        for r in placeholders:
            if r.recovered_artist:
                continue
            m = match_playlist(r.title, r.file_secs, tracks)
            if m and m.get("artist") and not ar.is_placeholder_artist(m["artist"]):
                r.recovered_artist, r.spotify_title = m["artist"], m.get("title", "")
                r.spotify_secs = m["duration_ms"] / 1000.0 if m.get("duration_ms") else None
                r.id_check = ("matched to your playlist by title + length"
                              + ("" if not r.spotify_id else " (the id in the file was wrong)"))
                summary["playlist_matched"] += 1
    summary["recovered"] = sum(1 for r in placeholders if r.recovered_artist)

    default_classify = classify is None
    if classify is None:
        from services.genre_evidence import classify_track

        def classify(artist, title, bpm, path):
            return classify_track(artist, title, path=path, bpm=bpm, online=online,
                                  use_musicbrainz=use_musicbrainz, min_confidence=0.3)

    out(f"Classifying {len(rows)} track(s)" + (" (Last.fm online)..." if online else " (offline)..."))
    classify_rows(rows, classify, move_confidence=move_confidence,
                  progress=lambda i, n: out(f"  {i}/{n}") if i % 250 == 0 else None)
    # Tracks classified with a recovered artist need the same fetcher cache flushed.
    try:
        from services.genre_evidence import default_fetchers
        if online:
            default_fetchers().cache.flush()
    except Exception:
        pass

    if itunes and default_classify:
        unresolved = [r for r in rows if r.action in ("unknown", "review", "suspect") and (r.artist_used or r.title)]
        if unresolved:
            from services.genre_evidence import classify_track as _ct, default_fetchers as _df
            out(f"Asking iTunes about the {len(unresolved)} track(s) nothing else could settle "
                f"(~{len(unresolved) * 3 / 60:.0f} min the first time; every answer is cached)...")
            secs = {r.path: r.file_secs for r in unresolved}

            def classify_itunes(artist, title, bpm, path):
                return _ct(artist, title, path=path, bpm=bpm, online=True, use_musicbrainz=False, use_itunes=True,
                           duration_s=secs.get(path), min_confidence=0.3)

            classify_rows(unresolved, classify_itunes, move_confidence=move_confidence,
                          progress=lambda i, n: out(f"  iTunes {i}/{n}") if i % 100 == 0 or i == n else None)
            try:
                _df().cache.flush()
            except Exception:
                pass

    # Wrong-version check for every file whose Spotify record is known and is the SAME song: a file
    # far from Spotify's length is a different edit or wrong audio (karaoke, another take).
    if by_id:
        from services.strict_matcher import same_song_title
        for r in rows:
            t = by_id.get(r.spotify_id) if r.spotify_id else None
            if not (t and r.file_secs and t.get("duration_ms") and same_song_title(r.title, t.get("title", ""))):
                continue
            delta = r.file_secs - t["duration_ms"] / 1000.0
            if abs(delta) > LENGTH_SUSPECT_SECS:
                msg = (f"file is {abs(delta):.0f}s {'longer' if delta > 0 else 'shorter'} than Spotify's "
                       f"version (a different edit, or the wrong audio)")
                r.note = (r.note + "; " if r.note else "") + msg
                summary["length_suspects"] += 1
                if r.action in ("ok", "unknown"):
                    r.action = "suspect"

    if audio:
        summary["audio"], summary["audio_fusion"] = run_audio(rows, jobs=audio_jobs, out=out)

    order = {"move": 0, "ear": 1, "review": 2, "suspect": 3, "unknown": 4, "ok": 5}
    rows.sort(key=lambda r: (order.get(r.action, 9), r.folder, r.proposed, r.artist_used.lower(), r.title.lower()))
    for n, r in enumerate(rows, 1):
        r.idx = n
    return rows, summary


# ═══════════════════════════════════════════════════════════════════════════
# reports
# ═══════════════════════════════════════════════════════════════════════════

CSV_FIELDS = ["idx", "apply", "action", "current_folder", "proposed_folder", "confidence", "verified",
              "artist", "recovered_artist", "id_check", "genre_tag", "audio_genre", "audio_conf", "title", "bpm",
              "note", "why", "path"]


def write_reports(rows: List[Row], root: Path, summary: dict, out_dir: Optional[Path] = None) -> Tuple[Path, Path, Path]:
    out_dir = out_dir or REPORTS_DIR              # looked up at call time so tests can redirect it
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    jp, cp, tp = (out_dir / f"resort_{stamp}.{ext}" for ext in ("json", "csv", "txt"))
    jp.write_text(json.dumps({"root": str(root), "created": stamp, "recovery": summary,
                              "rows": [asdict(r) for r in rows]}, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(cp, "w", newline="", encoding="utf-8-sig") as fh:          # BOM: Excel reads non-Latin titles
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            if r.action == "ok":
                continue                                                # the spreadsheet is the to-do list
            w.writerow({"idx": r.idx, "apply": "yes" if r.action == "move" else "", "action": r.action,
                        "current_folder": r.folder, "proposed_folder": r.proposed,
                        "confidence": f"{r.confidence:.2f}", "verified": "yes" if r.verified else "",
                        "artist": r.artist, "recovered_artist": r.recovered_artist, "id_check": r.id_check,
                        "genre_tag": r.genre_tag, "audio_genre": r.audio_genre,
                        "audio_conf": f"{r.audio_conf:.2f}" if r.audio_genre else "", "title": r.title,
                        "bpm": "" if r.bpm is None else f"{r.bpm:g}", "note": r.note, "why": r.why, "path": r.path})
    tp.write_text(render_summary(rows, summary), encoding="utf-8")
    return jp, cp, tp


def render_audio_report(report: dict, fusion: dict) -> str:
    """The audio model's honest accuracy, in plain words."""
    L = [f"Audio model ({report['chosen']}, trained on {report['n_train']} confirmed tracks; accuracy measured on tracks it "
         f"had NOT seen):"]
    for name, v in report["candidates"].items():
        L.append(f"    {name:<9} out-of-fold accuracy {v['accuracy']:.0%}")
    hp = report.get("hand_placed_test")
    if hp:
        L.append(f"  Tracks YOU moved by hand ({hp['n']}), scored by a model trained without them: right {hp['accuracy_all']:.0%} "
                 f"of the time overall"
                 + (f"; on the {hp['acted']} it was confident about: {hp['acted_precision']:.0%}." if hp["acted"] else "."))
    mv = report.get("move_thresholds", {})
    L.append(f"  {'crate':<12}{'tracks':>7}{'confirms at':>13}{'right then':>12}{'may MOVE at':>14}")
    for crate, r in sorted(report["per_class"].items(), key=lambda kv: -kv[1]["support"]):
        prec = "-" if r["acted_precision"] is None else f"{r['acted_precision']:.0%}"
        thr = "never" if r["threshold"] > 1.0 else f">= {r['threshold']:.2f}"
        mvs = "never" if mv.get(crate) is None else f">= {mv[crate]:.2f}"
        L.append(f"  {crate:<12}{r['support']:>7}{thr:>13}{prec:>12}{mvs:>14}")
    if fusion:
        L.append(f"  Effect: {fusion.get('ear', 0)} placed by ear, {fusion.get('confirmed', 0)} unknowns confirmed, "
                 f"{fusion.get('upgraded', 0)} weak suggestions confirmed, {fusion.get('downgraded', 0)} curated moves "
                 f"questioned, {fusion.get('flagged', 0)} 'ok' tracks flagged as sounding wrong.")
    return "\n".join(L)


def render_summary(rows: List[Row], summary: dict, max_lines: int = 40) -> str:
    c = Counter(r.action for r in rows)
    L = [f"{len(rows)} tracks scanned",
         f"  ok       already in the right folder ............ {c['ok']}",
         f"  MOVE     confident + verified, different folder .. {c['move']}",
         f"  ear      placed by how it SOUNDS (no metadata) ... {c['ear']}",
         f"  review   weaker evidence / a taste boundary ...... {c['review']}",
         f"  suspect  tempo/length/sound does not fit ......... {c['suspect']}",
         f"  unknown  no confident answer (left alone) ......... {c['unknown']}"]
    audio = summary.get("audio")
    if audio:
        L += ["", render_audio_report(audio, summary.get("audio_fusion") or {})]
    if summary.get("placeholder_tracks"):
        L += ["", f"Artist tag missing/placeholder on {summary['placeholder_tracks']} tracks "
                  f"({summary['with_spotify_id']} have a Spotify id): recovered {summary['recovered']} "
                  f"({summary['calls']} Spotify calls, {summary['from_cache']} from cache)."]
        if summary.get("ids_wrong") or summary.get("ids_ok") or summary.get("playlist_matched"):
            L.append(f"  Identity check: {summary.get('ids_ok', 0)} Spotify ids match their file; "
                     f"{summary.get('ids_wrong', 0)} point at a DIFFERENT song (their artist is not used); "
                     f"{summary.get('playlist_matched', 0)} more identified from your playlist by title + length.")
            if summary.get("ids_unchecked_len"):
                L.append(f"  {summary['ids_unchecked_len']} of those matched by TITLE only — their length could not be "
                         f"checked because Spotify was unavailable.")
        if summary.get("stopped"):
            L.append(f"  Recovery stopped early: {summary['stopped']}")
    if summary.get("length_suspects"):
        L += ["", f"{summary['length_suspects']} file(s) are >{LENGTH_SUSPECT_SECS:g}s off Spotify's length for the same "
                  f"song (different edit, or wrong audio) — marked 'suspect'; see the note column."]
    moves = [r for r in rows if r.action == "move"]
    if moves:
        L += ["", "MOVE (verified):"]
        for r in moves[:max_lines]:
            L.append(f"  [{r.idx:>4}] {r.folder:>11} -> {r.proposed:<11} {r.confidence:.0%}  "
                     f"{(r.artist_used or '?')[:26]:<26} | {r.title[:36]}")
        if len(moves) > max_lines:
            L.append(f"  ... and {len(moves) - max_lines} more (see the spreadsheet)")
    flows = Counter((r.folder, r.proposed) for r in rows if r.action in ("move", "review"))
    if flows:
        L += ["", "Where tracks would go (move + review), current -> suggested:"]
        L += [f"  {a:>11} -> {b:<11} {n}" for (a, b), n in flows.most_common(15)]
    return "\n".join(L) + "\n"


def load_report(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# selection
# ═══════════════════════════════════════════════════════════════════════════

def parse_index_list(text: Optional[str]) -> Set[int]:
    """'1,4,7-9' -> {1,4,7,8,9}"""
    out: Set[int] = set()
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def read_csv_choices(path: Path) -> Dict[int, str]:
    """{idx: destination} for every spreadsheet row whose `apply` cell is ticked."""
    chosen: Dict[int, str] = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            if (rec.get("apply") or "").strip().lower() in _TRUTHY:
                try:
                    chosen[int(rec["idx"])] = (rec.get("proposed_folder") or "").strip()
                except (KeyError, ValueError):
                    continue
    return chosen


def select_rows(rows: List[dict], *, csv_path: Optional[Path] = None, include_review: bool = False,
                include_audio: bool = False, only: Optional[str] = None, skip: Optional[str] = None) -> List[dict]:
    """
    Which rows to move, each with a `dest` folder. Default: MOVE rows. --include-review adds the
    review rows; --only adds indexes; --skip removes them. With --csv the spreadsheet decides
    (its `proposed_folder` wins over the report's, so you can correct a destination).
    """
    by_idx = {r["idx"]: r for r in rows}
    if csv_path:
        dest = {i: (d or by_idx[i]["proposed"]) for i, d in read_csv_choices(csv_path).items() if i in by_idx}
    else:
        wanted = {"move"} | ({"review"} if include_review else set()) | ({"ear"} if include_audio else set())
        dest = {r["idx"]: r["proposed"] for r in rows if r["action"] in wanted}
        for i in parse_index_list(only):
            if i in by_idx:
                dest[i] = by_idx[i]["proposed"]
    for i in parse_index_list(skip):
        dest.pop(i, None)
    return [dict(by_idx[i], dest=d) for i, d in sorted(dest.items())]


# ═══════════════════════════════════════════════════════════════════════════
# apply / undo
# ═══════════════════════════════════════════════════════════════════════════

def _within(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def write_tags(path: str, *, artist: Optional[str] = None, genre: Optional[str] = None) -> dict:
    """Set TPE1 and/or TCON; returns the OLD values ({'artist':..., 'genre':...}) for the undo record.
    Passing "" REMOVES the frame (that is how an undo restores a tag that did not exist)."""
    from mutagen.id3 import ID3, ID3NoHeaderError, TCON, TPE1

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    def first(key):
        f = tags.get(key)
        return str(f.text[0]) if f is not None and getattr(f, "text", None) else ""

    old = {"artist": first("TPE1"), "genre": first("TCON")}
    for key, frame, value in (("TPE1", TPE1, artist), ("TCON", TCON, genre)):
        if value is None:
            continue
        if value == "":
            tags.delall(key)
        else:
            tags[key] = frame(encoding=3, text=[value])
    # Keep the file's own ID3v2 version (2 of the library's files are v2.3): only the requested
    # frames should change, not the tag format.
    tags.save(path, v2_version=3 if getattr(tags, "version", (2, 4, 0))[1] == 3 else 4)
    return old


def update_library_index(old_path: str, new_path: str, genre_folder: str) -> int:
    """Point the Mongo library_index record at the file's new location."""
    from database import get_library_index_collection
    col = get_library_index_collection()
    if col is None:
        return 0
    variants = list({old_path, old_path.replace("\\", "/"), old_path.replace("/", "\\")})
    res = col.update_many({"final_path": {"$in": variants}},
                          {"$set": {"final_path": new_path, "genre_folder": genre_folder}})
    return res.modified_count


def apply_rows(
    rows: List[dict],
    root: Path,
    *,
    dry_run: bool = True,
    fix_tags: bool = False,
    write_genre_tag: bool = True,
    fix_artist_rows: Optional[List[dict]] = None,
    sync_genre_rows: Optional[List[dict]] = None,
    leave_alone: Optional[Set[str]] = None,
    placed_path: Optional[Path] = None,
    move_fn: Optional[Callable] = None,
    index_fn: Optional[Callable[[str, str, str], int]] = None,
    tag_fn: Optional[Callable] = None,
    manifest_path: Optional[Path] = None,
    out: Callable[[str], None] = print,
) -> dict:
    """
    Move the selected rows (each has `dest`). With dry_run=True nothing changes. The manifest is
    rewritten after EVERY move, so an interrupted run can still be undone. The *_fn hooks let
    tests run without a real database.

    fix_artist_rows  (with fix_tags)  rows whose recovered artist is written into the artist tag
    sync_genre_rows  rows (not being moved) whose genre tag names a different crate than the folder
                     they sit in — set the tag to the folder, so the hand-sorting sticks
    leave_alone      file NAMES that are never moved nor re-tagged (e.g. files flagged corrupt)
    """
    if move_fn is None:
        from services.organizer_service import safe_move as move_fn
    if index_fn is None:
        index_fn = update_library_index
    if tag_fn is None:
        tag_fn = write_tags

    leave_alone = {n.lower() for n in (leave_alone or set())}
    protected = lambda row: Path(row["path"]).name.lower() in leave_alone             # noqa: E731
    left = [r for r in rows if protected(r)]
    for row in left:
        out(f"  leave  {row['rel']}  (on your leave-alone list)")
    rows = [r for r in rows if not protected(r)]

    valid = routable_labels()
    result = {"selected": len(rows), "moved": [], "skipped": [], "tagged": 0, "dry_run": dry_run,
              "manifest": None}
    records: List[dict] = []

    def save_manifest():
        if manifest_path and not dry_run:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps({"root": str(root), "records": records}, indent=2,
                                                ensure_ascii=False), encoding="utf-8")
            result["manifest"] = str(manifest_path)

    for row in rows:
        src, dest_label = Path(row["path"]), row["dest"]
        problem = None
        if src.suffix.lower() != ".mp3":
            problem = "not an .mp3"
        elif not _within(root, src):
            problem = "outside the library root"
        elif src.is_symlink():
            problem = "is a symlink"
        elif not src.is_file():
            problem = "file not found (moved already?)"
        elif dest_label not in valid:
            problem = f"'{dest_label}' is not a valid crate"
        elif row["folder"] not in src.parts:
            problem = f"no longer in '{row['folder']}'"
        if problem:
            result["skipped"].append((row["path"], problem))
            out(f"  skip   {row['rel']}  ({problem})")
            continue

        if dry_run:
            out(f"  would move  {row['folder']:>11} -> {dest_label:<11} {row['rel']}")
            result["moved"].append((row["path"], "dry-run"))
            continue

        dest_dir = root / "Library" / dest_label
        try:
            new_path = str(move_fn(src, dest_dir, artist_name=row.get("artist_used", "")))
        except Exception as exc:                       # e.g. file locked by a player: skip it, keep going
            result["skipped"].append((row["path"], f"move failed: {exc}"))
            out(f"  FAILED {row['rel']}: {exc}")
            continue
        rec = {"old_path": str(src), "new_path": new_path, "old_folder": row["folder"], "new_folder": dest_label,
               "old_artist": None, "old_genre": None}
        try:
            tag_new_artist = row["recovered_artist"] if (fix_tags and row.get("recovered_artist")) else None
            if write_genre_tag or tag_new_artist:
                old = tag_fn(new_path, artist=tag_new_artist,
                             genre=FOLDER_TO_TCON.get(dest_label) if write_genre_tag else None)
                rec["old_genre"] = old.get("genre") if write_genre_tag else None
                if tag_new_artist:
                    rec["old_artist"] = old.get("artist")
                    result["tagged"] += 1
        except Exception as exc:
            out(f"    (tags not updated: {exc})")
        try:
            index_fn(str(src), new_path, f"Library/{dest_label}")
        except Exception as exc:
            out(f"    (library index not updated: {exc})")
        records.append(rec)
        save_manifest()
        result["moved"].append((row["path"], new_path))
        out(f"  moved  {row['folder']:>11} -> {dest_label:<11} {row['rel']}")

    # Tag-only fixes for tracks that are NOT being moved (moved ones were handled above): the
    # recovered artist, and/or the genre tag brought in line with the folder the file sits in.
    # One write per file, one manifest record per file.
    moved_src = {r["path"] for r in rows}
    wanted: Dict[str, dict] = {}
    if fix_tags:
        for row in fix_artist_rows or []:
            if row.get("recovered_artist"):
                wanted.setdefault(row["path"], {"row": row})["artist"] = row["recovered_artist"]
    for row in sync_genre_rows or []:
        crate = FOLDER_TO_TCON.get(row["folder"])
        if crate and row.get("tag_folder") and row["tag_folder"] != row["folder"]:
            wanted.setdefault(row["path"], {"row": row})["genre"] = crate
    result["genre_synced"] = 0
    newly_placed: Dict[str, str] = {}
    for path, spec in wanted.items():
        row = spec["row"]
        p = Path(path)
        if path in moved_src or protected(row) or not p.is_file() or not _within(root, p):
            continue
        if dry_run:
            what = " + ".join(x for x in (f"artist -> {spec['artist'][:30]}" if "artist" in spec else "",
                                          f"genre {row.get('genre_tag') or '(none)'} -> {spec['genre']}" if "genre" in spec else "") if x)
            out(f"  would set  {what:<48} {row['rel']}")
            continue
        try:
            old = tag_fn(str(p), artist=spec.get("artist"), genre=spec.get("genre"))
            records.append({"old_path": str(p), "new_path": str(p), "old_folder": row["folder"],
                            "new_folder": row["folder"],
                            "old_artist": old.get("artist") if "artist" in spec else None,
                            "old_genre": old.get("genre") if "genre" in spec else None})
            result["tagged"] += 1 if "artist" in spec else 0
            if "genre" in spec:
                result["genre_synced"] += 1
                newly_placed[placement_key(str(p))] = row["folder"]           # remember: YOU chose this folder
            save_manifest()
        except Exception as exc:
            out(f"  tags not written for {row['rel']}: {exc}")
    if newly_placed and not dry_run:
        ledger = load_user_placed(placed_path)
        ledger.update(newly_placed)
        save_user_placed(ledger, placed_path)
        result["placements_recorded"] = len(newly_placed)
    return result


def trusted_artist_rows(rows: List[dict]) -> List[dict]:
    """Rows whose recovered artist is safe to WRITE into the file: the Spotify record matched the
    file by title AND length, or the playlist identified it. A title-only match (length could not
    be checked because Spotify was unavailable) is good enough to classify with, but a wrong
    artist written into a file is worse than 'Unknown', so those wait for the length check."""
    return [r for r in rows
            if r.get("recovered_artist")
            and (r.get("id_check") == "ok" or str(r.get("id_check", "")).startswith("matched to your playlist"))]


def undo_manifest(
    manifest: dict,
    *,
    dry_run: bool = True,
    move_fn: Optional[Callable[[str, str], None]] = None,
    index_fn: Optional[Callable[[str, str, str], int]] = None,
    tag_fn: Optional[Callable] = None,
    out: Callable[[str], None] = print,
) -> dict:
    """Reverse an apply: newest first, so chained moves unwind correctly."""
    if move_fn is None:
        move_fn = lambda a, b: shutil.move(a, b)          # noqa: E731
    if index_fn is None:
        index_fn = update_library_index
    if tag_fn is None:
        tag_fn = write_tags

    result = {"restored": 0, "skipped": [], "dry_run": dry_run}
    for rec in reversed(manifest.get("records", [])):
        cur, orig = Path(rec["new_path"]), Path(rec["old_path"])
        moved = cur != orig
        if not cur.is_file():
            result["skipped"].append((rec["new_path"], "file not found"))
            out(f"  skip   {cur.name}  (not found where it was moved to)")
            continue
        if moved and orig.exists():
            result["skipped"].append((rec["new_path"], "original location is occupied"))
            out(f"  skip   {cur.name}  (something already exists at {orig})")
            continue
        if dry_run:
            out(f"  would restore  {rec['new_folder']:>11} -> {rec['old_folder']:<11} {cur.name}")
            result["restored"] += 1
            continue
        try:
            if moved:
                orig.parent.mkdir(parents=True, exist_ok=True)
                move_fn(str(cur), str(orig))
            if rec.get("old_genre") is not None or rec.get("old_artist") is not None:
                tag_fn(str(orig), artist=rec.get("old_artist"), genre=rec.get("old_genre"))
            if moved:
                try:
                    index_fn(str(cur), str(orig), f"Library/{rec['old_folder']}")
                except Exception as exc:
                    out(f"    (library index not updated: {exc})")
            result["restored"] += 1
            out(f"  restored  {rec['new_folder']:>11} -> {rec['old_folder']:<11} {orig.name}")
        except Exception as exc:
            result["skipped"].append((rec["new_path"], str(exc)))
            out(f"  FAILED {cur.name}: {exc}")
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# learn — turn your (corrected) folders into standing routing rules for FUTURE songs
# ═══════════════════════════════════════════════════════════════════════════

# crate folder -> the canonical genre key the artist-memory service stores (a GENRE_TAXONOMY key)
CRATE_TO_GENRE = {"Bollywood": "Bollywood", "Punjabi": "Punjabi", "Tamil": "Tamil", "House": "House", "Techno": "Techno",
                  "Trance": "Trance", "Drum & Bass": "Drum and Bass", "UK Garage": "UK Garage", "Dubstep": "Dubstep",
                  "Grime": "Grime", "Indian Hip Hop": "Indian Hip Hop", "International Hip Hop": "Hip Hop",
                  "R&B": "R&B", "Pop": "Pop", "Latin": "Latin"}
LEARN_MIN_TRACKS = 3
LEARN_MIN_SHARE = 0.8


def library_consensus(rows: List[dict], *, min_tracks: int = LEARN_MIN_TRACKS, min_share: float = LEARN_MIN_SHARE) -> List[dict]:
    """
    Artists whose tracks overwhelmingly sit in ONE crate: [{"artist", "crate", "tracks", "share"}].

    Only folders that can be trusted count: tracks whose folder independent evidence CONFIRMED ("ok"),
    and files you placed by hand (your decision). Unknown / contested / pending rows are ignored, and
    so are placeholder artists
    ("Unknown", "Indian") never count; a credit "A, B feat. C" counts for its lead artist A.
    """
    from services.artist_recovery import is_placeholder_artist
    from services.genre_evidence import primary_artist
    from services.genre_router import normalize_artist_key

    by_artist: Dict[str, Dict] = {}
    for r in rows:
        if r["folder"] not in CRATE_TO_GENRE:
            continue
        hand = is_hand_placed(r)
        # ONLY confirmed folders teach: evidence agrees with the folder ("ok"), or you chose it. A track
        # nobody could verify ("unknown") may sit where an old AI guess put it — learning from it would turn
        # yesterday's mistake into a standing rule for tomorrow's downloads.
        if not hand and r.get("action") != "ok":
            continue
        name = primary_artist(r.get("artist_used") or "")
        if not name or is_placeholder_artist(name):
            continue
        rec = by_artist.setdefault(normalize_artist_key(name), {"artist": name, "crates": Counter()})
        rec["crates"][r["folder"]] += 1
    out = []
    for rec in by_artist.values():
        total = sum(rec["crates"].values())
        crate, n = rec["crates"].most_common(1)[0]
        if total >= min_tracks and n / total >= min_share:
            out.append({"artist": rec["artist"], "crate": crate, "tracks": total, "share": round(n / total, 2)})
    return sorted(out, key=lambda d: (-d["tracks"], d["artist"].lower()))


def learn_from_library(rows: List[dict], *, dry_run: bool = True,
                       record: Optional[Callable] = None, lookup: Optional[Callable] = None,
                       out: Callable[[str], None] = print) -> dict:
    """
    Record each consensus artist in the artist-memory service, so the router sends their FUTURE tracks
    to the crate you put them in. The router trusts a memory entry from confidence 0.5 (two confirmed
    moves), so artists with 3-5 tracks are recorded twice and artists with 6+ four times (confidence
    1.0). Idempotent: an artist already remembered with the same genre at that confidence is skipped.
    """
    if record is None or lookup is None:
        from services.artist_memory_service import lookup_artist, record_move
        record, lookup = record or record_move, lookup or lookup_artist
    res = {"considered": 0, "recorded": 0, "already_known": 0, "changed": 0, "dry_run": dry_run, "artists": []}
    for c in library_consensus(rows):
        res["considered"] += 1
        genre = CRATE_TO_GENRE[c["crate"]]
        want_conf = 1.0 if c["tracks"] >= 6 else 0.5
        known = lookup(c["artist"])
        if known and known.get("genre") == genre and float(known.get("confidence", 0)) >= want_conf:
            res["already_known"] += 1
            continue
        if known and known.get("genre") not in (None, genre):
            res["changed"] += 1
        calls = 4 if c["tracks"] >= 6 else 2
        res["artists"].append({**c, "genre": genre, "was": known.get("genre") if known else None})
        out(f"  {'would remember' if dry_run else 'remembered'}  {c['artist'][:34]:<34} -> {c['crate']:<11} "
            f"({c['tracks']} tracks, {c['share']:.0%})" + (f"   [was {known['genre']}]" if known and known.get("genre") != genre else ""))
        if not dry_run:
            for _ in range(calls):
                record(c["artist"], genre, source="library_consensus")
        res["recorded"] += 1
    return res


def _quiet_logs() -> None:
    try:
        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level="ERROR")
    except Exception:
        pass


def cmd_scan(args) -> int:
    from config import config
    _quiet_logs()
    root = Path(args.root or config.BASE_DOWNLOAD_DIR)
    if not (root / args.folder).is_dir():
        print(f"Not found: {root / args.folder}")
        return 2
    print(f"Scanning {root / args.folder}  (READ-ONLY — nothing will be moved or changed)\n")
    rows, summary = scan(
        root, folder=args.folder, include_electronic=args.include_electronic, limit=args.limit,
        recover=not args.no_recover, max_calls=args.max_spotify_calls, online=not args.offline,
        use_musicbrainz=args.musicbrainz, move_confidence=args.move_confidence,
        use_playlist=not args.no_playlist, spotify_calls=not args.no_spotify_calls,
        audio=args.audio, audio_jobs=args.audio_jobs, itunes=args.itunes,
    )
    jp, cp, tp = write_reports(rows, root, summary)
    print("\n" + render_summary(rows, summary))
    print(f"Report:       {jp}\nSpreadsheet:  {cp}   (edit the `apply` column, then apply --csv)\nSummary:      {tp}")
    print("\nNothing has been changed. To act on it:\n"
          f"  python library_resort.py apply --report \"{jp}\"            (preview)\n"
          f"  python library_resort.py apply --report \"{jp}\" --yes      (move the MOVE rows)")
    return 0


def cmd_apply(args) -> int:
    _quiet_logs()
    report = load_report(Path(args.report))
    root = Path(args.root or report["root"])
    rows = report["rows"]
    chosen = select_rows(rows, csv_path=Path(args.csv) if args.csv else None,
                         include_review=args.include_review, include_audio=args.include_audio,
                         only=args.only, skip=args.skip)
    if not chosen and not args.fix_tags and not args.sync_genre_tags:
        print("Nothing selected. (Only MOVE rows are included by default; add --include-review, "
              "--only N,M or tick rows in the spreadsheet and pass --csv.)")
        return 0
    leave_alone: Set[str] = set()
    if args.leave_alone:
        leave_alone = {ln.strip() for ln in Path(args.leave_alone).read_text(encoding="utf-8").splitlines()
                       if ln.strip() and not ln.startswith("#")}
    dry_run = not args.yes
    print("PREVIEW — nothing will change. Re-run with --yes to apply.\n" if dry_run
          else "APPLYING — a manifest is written so this can be undone.\n")
    print(f"{len(chosen)} file(s) selected to move:")
    manifest_path = REPORTS_DIR / f"resort_undo_{time.strftime('%Y%m%d_%H%M%S')}.json"
    result = apply_rows(chosen, root, dry_run=dry_run, fix_tags=args.fix_tags,
                        write_genre_tag=not args.no_genre_tag,
                        fix_artist_rows=trusted_artist_rows(rows) if args.fix_tags else None,
                        sync_genre_rows=([r for r in rows if r.get("tag_folder") and r["tag_folder"] != r["folder"]]
                                         if args.sync_genre_tags else None),
                        leave_alone=leave_alone, manifest_path=manifest_path)
    print()
    if dry_run:
        print(f"Preview only: {len(result['moved'])} would move, {len(result['skipped'])} skipped.")
        return 0
    print(f"Moved: {len(result['moved'])}   Skipped/failed: {len(result['skipped'])}   "
          f"Artist tags written: {result['tagged']}   Genre tags synced to your folders: {result['genre_synced']}")
    if result["manifest"]:
        print(f"\nTo reverse everything:\n  python library_resort.py undo --manifest \"{result['manifest']}\" --yes")
    return 0


def cmd_learn(args) -> int:
    _quiet_logs()
    report = load_report(Path(args.report))
    dry_run = not args.yes
    print("PREVIEW — nothing is recorded. Re-run with --yes.\n" if dry_run else "RECORDING...\n")
    res = learn_from_library(report["rows"], dry_run=dry_run)
    print(f"\n{res['considered']} artists have >= {LEARN_MIN_TRACKS} tracks with >= {LEARN_MIN_SHARE:.0%} in one crate: "
          f"{res['recorded']} {'would be ' if dry_run else ''}remembered, {res['already_known']} already known, "
          f"{res['changed']} change an earlier memory.")
    if not dry_run:
        print("Future downloads by these artists now route to the crate you put them in.")
    return 0


def cmd_undo(args) -> int:
    _quiet_logs()
    manifest = load_report(Path(args.manifest))
    dry_run = not args.yes
    print("PREVIEW — nothing will change. Re-run with --yes to undo.\n" if dry_run else "UNDOING...\n")
    result = undo_manifest(manifest, dry_run=dry_run)
    print(f"\n{'Would restore' if dry_run else 'Restored'}: {result['restored']}   Skipped: {len(result['skipped'])}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Find tracks in the wrong genre folder and move them (undoably).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="READ-ONLY: classify every track, write a report + spreadsheet")
    s.add_argument("--root", help="library root (default: BASE_DOWNLOAD_DIR)")
    s.add_argument("--folder", default="Library", help="folder under the root holding the genre crates")
    s.add_argument("--include-electronic", action="store_true", help="also re-sort the Electronic catch-all")
    s.add_argument("--limit", type=int, help="only the first N tracks (testing)")
    s.add_argument("--no-recover", action="store_true", help="do not look up missing artists on Spotify")
    s.add_argument("--max-spotify-calls", type=int, default=300, help="cap on Spotify lookups this run (default 300)")
    s.add_argument("--itunes", action="store_true",
                   help="also ask iTunes (free, no key) for the genre of tracks nothing else could settle "
                        "(title + artist + length are checked; ~3 s per track, cached)")
    s.add_argument("--audio", action="store_true",
                   help="also judge tracks by how they SOUND (trained on your confirmed tracks; slow the first time)")
    s.add_argument("--audio-jobs", type=int, default=max(1, (os.cpu_count() or 4) - 2), help="workers for the audio analysis")
    s.add_argument("--no-spotify-calls", action="store_true",
                   help="make NO Spotify calls (cached data only). Automatic while a Spotify rate-limit block lasts")
    s.add_argument("--no-playlist", action="store_true",
                   help="do not read your ingest playlist (it identifies files and checks their length)")
    s.add_argument("--offline", action="store_true", help="no Last.fm: curated tables + hints only (fast)")
    s.add_argument("--musicbrainz", action="store_true", help="also query MusicBrainz (slow, rarely adds anything)")
    s.add_argument("--move-confidence", type=float, default=MOVE_CONFIDENCE,
                   help=f"confidence needed for an automatic MOVE (default {MOVE_CONFIDENCE})")
    s.set_defaults(fn=cmd_scan)

    a = sub.add_parser("apply", help="move the selected files (PREVIEW unless --yes)")
    a.add_argument("--report", required=True, help="a resort_*.json produced by scan")
    a.add_argument("--csv", help="the spreadsheet you edited: rows with `apply` ticked are moved")
    a.add_argument("--root", help="override the library root stored in the report")
    a.add_argument("--include-review", action="store_true", help="also move the 'review' rows")
    a.add_argument("--include-audio", action="store_true",
                   help="also move the 'ear' rows: tracks no metadata could place, moved on how they sound")
    a.add_argument("--only", help="also include these report indexes, e.g. 3,7,10-12")
    a.add_argument("--skip", help="exclude these report indexes")
    a.add_argument("--fix-tags", action="store_true", help="also write recovered artist names into the files' artist tag")
    a.add_argument("--no-genre-tag", action="store_true", help="do not update the genre (TCON) tag of moved files")
    a.add_argument("--sync-genre-tags", action="store_true",
                   help="also set the genre tag of files you moved by hand (tag names another crate) to the folder they sit in")
    a.add_argument("--leave-alone", help="text file of file NAMES (one per line) that are never moved or re-tagged")
    a.add_argument("--yes", action="store_true", help="actually move (default is a preview)")
    a.set_defaults(fn=cmd_apply)

    u = sub.add_parser("undo", help="reverse an apply using its manifest (PREVIEW unless --yes)")
    u.add_argument("--manifest", required=True, help="a resort_undo_*.json written by apply")
    u.add_argument("--yes", action="store_true", help="actually undo (default is a preview)")
    u.set_defaults(fn=cmd_undo)

    l = sub.add_parser("learn", help="remember your folders as routing rules for FUTURE songs (PREVIEW unless --yes)")
    l.add_argument("--report", required=True, help="a resort_*.json produced by scan (run it AFTER you have fixed the folders)")
    l.add_argument("--yes", action="store_true", help="actually record (default is a preview)")
    l.set_defaults(fn=cmd_learn)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
