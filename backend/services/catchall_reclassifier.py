"""
catchall_reclassifier.py
=========================
Shared classification+move logic for re-routing a single "catch-all"
track (one that landed in Library/Electronic without a confident genre)
into its correct genre subfolder.

This is the same fallback chain used by the web UI's "retag catch-all"
button (backend/app.py::retag_catchall_track) — extracted here so the
hourly maintenance job and the button share one implementation.

Fallback chain, cheapest/most-certain first:
  1. ARTIST_GENRE_OVERRIDE (instant, no API)
  2. Spotify artist search (skipped while Spotify returns no artist genres)
  3. Spotify title-only search (same recording only; the artist tag is never rewritten)
  4. Evidence vote — services/genre_evidence.py: Last.fm track+artist tags, identity-checked
     MusicBrainz tags, remixer / script / title hints, BPM plausibility, and (only if those
     cannot settle it) an AcoustID fingerprint. Answers only when the votes are strong and
     clearly ahead; otherwise abstains.
  7. Groq LLM genre classification from title+artist (last resort). Lives in
     services/groq_service.py, whose module/function/field names ("gemini_*")
     are stale from before that service was migrated to Groq — it calls the
     Groq API (config.GROQ_API_KEY / GROQ_MODEL) exclusively, not Gemini.
     remaining_quota() is hardcoded to 9999 and GroqQuotaExceeded is never
     actually raised, since Groq has no equivalent daily-quota concept.
"""
import threading
from pathlib import Path

from loguru import logger

from config import config
from services.organizer_service import safe_move

BASE_DOWNLOAD_DIR = Path(config.BASE_DOWNLOAD_DIR)


def _with_timeout(fn, seconds=8):
    """Run fn() in a daemon thread with a wall-clock timeout. Raises TimeoutError on expiry."""
    result = [None]
    exc = [None]

    def _run():
        try:
            result[0] = fn()
        except Exception as e:
            exc[0] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"External API call timed out after {seconds}s")
    if exc[0]:
        raise exc[0]
    return result[0]


def _read_bpm(id3) -> "float | None":
    """BPM from an ID3 object's TBPM frame, or None when absent / not a plausible tempo."""
    try:
        bpm = float(str(id3.get("TBPM", "")).strip())
    except Exception:
        return None
    return bpm if 40 <= bpm <= 300 else None


def _acoustid_configured() -> bool:
    try:
        from services.musicbrainz_service import _acoustid_key
        return bool(_acoustid_key())
    except Exception:
        return False


def classify_and_route_catchall_track(
    full_path: str, *, dry_run: bool = False, skip_ai_step: bool = False, hold_ai_matches: bool = False
) -> dict:
    """
    Run one file through the full catch-all classification fallback chain.

    Args:
        full_path: Absolute path to the .mp3 file. Caller is responsible for
                    validating this path is safely within BASE_DOWNLOAD_DIR —
                    this function does no path-containment checking itself.
        dry_run:    When True, never writes/moves anything — just reports
                    what would happen.
        skip_ai_step: When True, never calls step 7 (Groq LLM classification)
                    at all — a track that steps 1-6 can't resolve just comes
                    back unresolved, NOT as quota_exhausted (that flag is kept
                    for the rare case a caller does hit a real rate/quota
                    limit and wants to react to it differently, e.g. stopping
                    a whole batch run — see the module docstring).
        hold_ai_matches: When True, an UNVERIFIED match — step 7 (a Groq text
                    guess), step 3 (title-search), or step 4 resting only on
                    artist-level tags/hints — is reported exactly like dry_run
                    for that track: nothing is moved/written, out["ai_guess"]
                    is True, and the caller can decide to review it separately
                    rather than auto-executing it alongside verified matches.
                    Has no effect when dry_run is already True (everything is
                    already held).

    Returns a dict:
        {
            "moved": bool,
            "dry_run": bool,
            "old_path": str,
            "new_path": str | None,
            "new_folder": str | None,
            "source": str,      # e.g. "artist_override", "evidence:lastfm_track+musicbrainz"
            "reason": str | None,
            "quota_exhausted": bool,
            "ai_guess": bool,   # True = UNVERIFIED (see hold_ai_matches above)
            "confidence": float,  # step 4's vote confidence (0.0 when step 4 did not run)
            "evidence": str,      # step 4's one-line explanation, incl. the reason it abstained
        }
    """
    from services.groq_service import identify_audio, GroqQuotaExceeded, remaining_quota as _remaining_quota
    from services.genre_router import (
        normalize_genre, _library_path, resolve_genre_folder_with_confidence,
        normalize_artist_key as _nak, spotify_genres_available,
    )
    from mutagen.id3 import ID3, TPE1, TXXX

    out = {
        "moved": False, "dry_run": dry_run, "old_path": full_path, "new_path": None,
        "new_folder": None, "source": "unknown", "reason": None, "quota_exhausted": False,
        "ai_guess": False, "confidence": 0.0, "evidence": "",
    }

    try:
        # Read artist from ID3
        artist_name = ""
        _id3 = None
        try:
            _id3 = ID3(full_path)
            artist_name = str(_id3.get("TPE1", "")).strip()
        except Exception as e:
            logger.debug(f"[catchall-reclassify] Could not read ID3 tags from {full_path}: {e}")

        genre_path = None
        route_source = "unknown"
        evidence_unverified = False

        # ── 1. ARTIST_GENRE_OVERRIDE (instant, no API) ──────────────────────
        if artist_name:
            override = config.ARTIST_GENRE_OVERRIDE.get(_nak(artist_name))
            if override:
                canonical = normalize_genre(override)
                genre_path = _library_path(canonical) if canonical else None
                route_source = "artist_override"

        # Read title from ID3 once (used by multiple fallback steps)
        title_tag = ""
        if _id3 is not None:
            try:
                title_tag = str(_id3.get("TIT2", "")).strip()
            except Exception as e:
                logger.debug(f"[catchall-reclassify] Could not read title tag: {e}")

        # ── 2. Spotify artist search ─────────────────────────────────────────
        # Its only output is a Spotify artist id, used to read that artist's
        # `genres` — a field Spotify no longer returns for this app (see
        # genre_router step 5). Skipped once that's been observed, instead of
        # spending API calls (and rate-limit budget) on a lookup that can't help.
        if (not genre_path and artist_name
                and artist_name.lower() not in ("unknown", "electronic", "")
                and spotify_genres_available()):
            try:
                from services.spotify_service import get_spotify_service
                spotify_service = get_spotify_service()
                def _sp2_search():
                    s = spotify_service.sp.search(q=artist_name, type="artist", limit=1)
                    its = s.get("artists", {}).get("items", [])
                    aid = its[0]["id"] if its else ""
                    return resolve_genre_folder_with_confidence(aid, artist_name, spotify_service.sp)
                folder, conf, src = _with_timeout(_sp2_search)
                if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                    genre_path = folder
                    route_source = src
                    logger.info(f"[catchall-reclassify] {Path(full_path).name} → {genre_path} via {src} ({conf:.0%})")
            except Exception as e:
                logger.debug(f"[catchall-reclassify] Spotify artist chain failed for '{artist_name}': {e}")

        # ── 3. Spotify title-only search ─────────────────────────────────────
        # Looks a track up by TITLE and routes on the genre/override of whichever
        # artist Spotify returns. That is only safe if the hit is provably the
        # SAME recording, so it now requires BOTH a near-exact title match AND a
        # duration within 3 s of this file. Before, any of the top-5 hits by ANY
        # artist could win — generic titles ("Takes", "scared", "Ravers", "Mana")
        # matched unrelated tracks, filed real techno under Pop/Bollywood/Punjabi,
        # and (non-dry-run) OVERWROTE the file's artist tag with the stranger's
        # name. The tag is no longer touched automatically, and a match found
        # this way is reported as UNVERIFIED (out["ai_guess"]) so batch callers
        # hold it for review.
        if not genre_path and title_tag and spotify_genres_available():
            try:
                from services.spotify_service import get_spotify_service
                from services import strict_matcher as _sm
                spotify_service = get_spotify_service()
                file_secs = None
                try:
                    from mutagen.mp3 import MP3
                    file_secs = float(MP3(full_path).info.length)
                except Exception:
                    pass
                def _sp3_search():
                    return spotify_service.sp.search(q=f"track:{title_tag}", type="track", limit=5)
                results = _with_timeout(_sp3_search)
                tracks = results.get("tracks", {}).get("items", [])
                clean_file_title = _sm.clean_title(title_tag)
                for t in tracks:
                    sp_artist = t.get("artists", [{}])[0].get("name", "")
                    if not sp_artist:
                        continue
                    title_sim = _sm._fuzzy_ratio(clean_file_title, _sm.clean_title(t.get("name", "")))
                    cand_ms = t.get("duration_ms") or 0
                    same_length = file_secs is not None and cand_ms and abs(file_secs - cand_ms / 1000.0) <= 3.0
                    if title_sim < 0.90 or not same_length:
                        continue
                    artist_id = t.get("artists", [{}])[0].get("id", "")
                    folder, conf, src = resolve_genre_folder_with_confidence(
                        artist_id, sp_artist, spotify_service.sp
                    )
                    if folder.startswith("Library/") and folder != "Library/Electronic" and conf >= 0.5:
                        genre_path = folder
                        route_source = f"spotify_title/{src}"
                        logger.info(
                            f"[catchall-reclassify] {Path(full_path).name} → {genre_path} via title search "
                            f"(same title+length as '{sp_artist}' — UNVERIFIED, file artist tag left untouched)"
                        )
                        break
            except Exception as e:
                logger.debug(f"[catchall-reclassify] Spotify title search failed: {e}")

        # ── 4. Evidence vote (Last.fm + MusicBrainz + remixer/script/title hints + BPM) ──
        # Replaces three single-source steps (Last.fm / MusicBrainz / AcoustID), each of which
        # accepted the FIRST genre it saw. Here every signal votes, tempo vetoes implausible
        # genres, and the engine abstains unless the evidence is strong and clearly ahead —
        # so a track it cannot place stays put instead of being filed under a guess.
        # See services/genre_evidence.py. The AcoustID fingerprint (slow) is only spent on
        # tracks the cheap signals could not settle.
        if not genre_path and (title_tag or artist_name):
            try:
                from services import genre_evidence
                bpm = _read_bpm(_id3)

                def _vote(fingerprint):
                    return genre_evidence.classify_track(
                        artist_name, title_tag, path=full_path, bpm=bpm, online=True,
                        use_fingerprint=fingerprint,
                    )

                decision = _with_timeout(lambda: _vote(False), seconds=45)
                if decision.abstain and _acoustid_configured():
                    decision = _with_timeout(lambda: _vote(True), seconds=60)
                out["confidence"] = decision.confidence
                out["evidence"] = decision.explain()
                if not decision.abstain and decision.genre not in ("", "Electronic"):
                    genre_path = f"Library/{decision.genre}"
                    route_source = "evidence:" + "+".join(decision.sources)
                    # Resting on artist-level tags / hints alone (no curated table, no
                    # track-level evidence) = a plausible inference, not a verified match.
                    evidence_unverified = not decision.verified
                    logger.info(
                        f"[catchall-reclassify] {Path(full_path).name} → {genre_path} via evidence "
                        f"({decision.confidence:.0%}{'' if decision.verified else ', UNVERIFIED'}: {decision.explain()})"
                    )
                else:
                    logger.debug(f"[catchall-reclassify] {Path(full_path).name}: {decision.explain()}")
            except Exception as e:
                logger.debug(f"[catchall-reclassify] Evidence vote failed: {e}")

        # ── 7. Groq LLM classification from title+artist (last resort) ───────
        if not genre_path and skip_ai_step:
            out["source"] = route_source
            out["reason"] = f"Could not classify via steps 1-6 (AI step skipped, source={route_source})"
            return out
        if not genre_path:
            if _remaining_quota() == 0:
                out["reason"] = "Could not classify via steps 1-6 and the AI step reports no quota remaining"
                out["quota_exhausted"] = True
                return out
            gemini = identify_audio(full_path)
            raw = gemini.get("gemini_genre", "")
            if raw:
                canonical = normalize_genre(raw)
                genre_path = _library_path(canonical) if canonical else None
                route_source = "gemini"

        out["source"] = route_source
        # "ai_guess" now means UNVERIFIED: a Groq text guess (step 7), a title-search
        # match (step 3), or an evidence vote resting only on artist-level tags/hints
        # (step 4) — none of them is a confirmed match for THIS track.
        out["ai_guess"] = (route_source == "gemini" or route_source.startswith("spotify_title/")
                           or evidence_unverified)
        effective_dry_run = dry_run or (out["ai_guess"] and hold_ai_matches)

        if not genre_path:
            out["reason"] = f"Could not classify (source={route_source})"
            return out

        out["new_folder"] = genre_path
        dest_dir = BASE_DOWNLOAD_DIR / genre_path
        src_path = Path(full_path)
        dest = dest_dir / src_path.name

        # Already in the correct folder — mark as reviewed so it drops from the catchall
        if src_path.resolve() == dest.resolve():
            out["reason"] = "already in correct folder"
            out["new_path"] = str(src_path)
            if not effective_dry_run:
                try:
                    _tags = ID3(str(src_path))
                    _tags.add(TXXX(encoding=3, desc="catchall_reviewed", text=["1"]))
                    _tags.save()
                except Exception:
                    pass
            return out

        if effective_dry_run:
            out["new_path"] = str(dest)
            out["moved"] = True  # "would move" — dry_run/held flag distinguishes from an actual move
            if out["ai_guess"] and hold_ai_matches and not dry_run:
                out["reason"] = "AI guess held for manual review (hold_ai_matches=True)"
            return out

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = safe_move(src_path, dest_dir, artist_name=artist_name)
        out["new_path"] = str(dest)
        out["moved"] = True

        try:
            tags = ID3(str(dest))
            tags.delall("TXXX:routing_source")
            tags.save()
        except Exception:
            pass
        try:
            from database import get_library_index_collection
            col = get_library_index_collection()
            if col is not None:
                col.update_one(
                    {"final_path": full_path},
                    {"$set": {"final_path": str(dest), "genre_folder": genre_path}},
                )
        except Exception:
            pass
        return out

    except GroqQuotaExceeded as e:
        logger.warning(f"[catchall-reclassify] quota exhausted: {e}")
        out["reason"] = str(e)
        out["quota_exhausted"] = True
        return out
    except Exception as e:
        logger.error(f"[catchall-reclassify] {e}")
        out["reason"] = str(e)
        return out
