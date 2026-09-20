"""
stream_download.py
==================
The selection + verification loop behind the browser "Download" button
(`/api/download-stream`), kept separate from Flask so it can be unit-tested.

Flow: search YouTube -> score candidates (strict_matcher) -> download the best ->
fingerprint the AUDIO (audio_verifier) -> if it is provably karaoke / the wrong
song, discard it and retry with the next-best candidate.

Before this existed the route took `ytsearch1` -> entries[0] with no validation at
all: whatever YouTube ranked first was delivered and named after the Spotify
metadata, so a karaoke file looked exactly like the right one.

All I/O is injected (`score_stage`, `fetch_audio`, `verify`), so tests need no network.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

# (label, query template) — same two stages the old route effectively used, but scored.
STAGES = (
    ("Stage 1 (Official)", "ytsearch10:{artist} - {title} Official Audio"),
    ("Stage 2 (Audio)",    "ytsearch5:{artist} - {title} Audio"),
)
CONFIDENT_SCORE = 0.75      # same "confident accept" bar as the background pipeline
MAX_VERIFY_REJECTS = 3


class StreamDownloadError(Exception):
    """Base class; `http_status` is what the route should answer with."""
    http_status = 502

    def __init__(self, message: str = "", reason: str = ""):
        super().__init__(message or reason)
        self.reason = reason


class SearchFailed(StreamDownloadError):
    http_status = 502


class NoConfidentMatch(StreamDownloadError):
    """Search worked, but nothing cleared the strict matcher (only karaoke/cover/wrong-song hits)."""
    http_status = 404


class AllCandidatesRejected(StreamDownloadError):
    """Candidates were downloaded, but every one failed the audio-fingerprint check."""
    http_status = 422


@dataclass
class VerifiedAudio:
    audio_bytes: bytes
    verdict: object          # audio_verifier.VerifyResult
    candidate: dict
    score: float


def _pick_candidate(title, artist, duration_ms, score_stage, exclude_urls):
    """Best-scoring candidate across the stages, stopping early on a confident one."""
    best, best_score, last_reason, search_err = None, 0.0, "", None
    for label, template in STAGES:
        try:
            cand, score, reason = score_stage(
                template.format(artist=artist, title=title), label,
                duration_ms=duration_ms, spotify_title=title, artist=artist,
                exclude_urls=exclude_urls,
            )
        except Exception as exc:                       # network / extractor failure for this stage
            search_err = exc
            continue
        last_reason = reason
        if cand is not None and score > best_score:
            best, best_score = cand, score
        if best is not None and best_score >= CONFIDENT_SCORE:
            break
    return best, best_score, last_reason, search_err


def download_verified_audio(
    *,
    title: str,
    artist: str,
    duration_ms: Optional[int],
    score_stage: Callable,
    fetch_audio: Callable[[str], Tuple[str, str]],
    verify: Callable,
    should_reject_fn: Callable,
    max_rejects: int = MAX_VERIFY_REJECTS,
) -> VerifiedAudio:
    """
    Return audio bytes that passed (or could not fail) the audio check.

    score_stage(query, label, *, duration_ms, spotify_title, artist, exclude_urls)
        -> (candidate | None, score, reason)
    fetch_audio(video_id) -> (tmp_dir, tmp_mp3_path); the caller-provided function
        downloads the video. This function always removes `tmp_dir`.
    verify(path, title, artist, expected_secs) -> VerifyResult
    should_reject_fn(VerifyResult) -> bool
    """
    rejected_urls: set = set()
    last_reject_reason = ""

    for _ in range(max_rejects):
        best, best_score, last_reason, search_err = _pick_candidate(
            title, artist, duration_ms, score_stage, rejected_urls,
        )

        if best is None:
            if last_reject_reason:
                break                                   # everything we found failed the audio check
            if search_err is not None and not last_reason:
                raise SearchFailed(f"YouTube search failed: {str(search_err)[:80]}")
            raise NoConfidentMatch(
                "No confident YouTube match for this track — only karaoke / instrumental / "
                "cover / wrong-song results were found.",
                reason=last_reason[:200],
            )

        video_id = (best.get("entry") or {}).get("id", "")
        if not video_id:
            raise StreamDownloadError("Could not extract video ID")

        tmp_dir, tmp_mp3 = fetch_audio(video_id)
        try:
            verdict = verify(tmp_mp3, title, artist, (duration_ms / 1000.0) if duration_ms else None)
            if should_reject_fn(verdict):
                rejected_urls.add(best.get("url") or video_id)
                last_reject_reason = verdict.reason
                continue                                # `finally` removes tmp_dir; next-best is tried
            with open(tmp_mp3, "rb") as fh:
                data = fh.read()
            return VerifiedAudio(data, verdict, best, best_score)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    raise AllCandidatesRejected(
        "Every YouTube match for this track was identified as the wrong audio "
        "(karaoke / instrumental / a different song), so nothing was downloaded.",
        reason=last_reject_reason[:200],
    )
