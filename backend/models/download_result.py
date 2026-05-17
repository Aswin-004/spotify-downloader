"""
DownloadResult — canonical pipeline state object.

Flows through all three ingest passes:
  PASS 1: download  → fills spotify_*, download_* fields
  PASS 2: genre     → fills genre_* fields
  PASS 3: move      → fills final_path, move_ok

Replaces the ad-hoc result["key"] dict pattern in auto_downloader.py and
downloader_service.py.  Old callers continue to work — this is additive.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GenreDecision:
    """Result of a single genre-routing decision."""
    folder: str             # e.g. "UK Garage/Sammy Virji"
    confidence: float       # 0.0–1.0
    source: str             # "artist_override" | "mb_genre" | "spotify_genre"
                            # | "raw_spotify" | "devanagari" | "uncategorized"
    matched_tag: str = ""   # the Spotify/MB genre tag that fired


@dataclass
class TaggingOutcome:
    """Result of MusicBrainz/Spotify tagging for a single file."""
    success: bool
    tags_written: list[str] = field(default_factory=list)
    mb_id: str = ""
    isrc: str = ""
    bpm: Optional[float] = None
    key: str = ""
    camelot: str = ""
    energy: Optional[float] = None
    danceability: Optional[float] = None
    genre: str = ""
    error: str = ""


@dataclass
class DownloadResult:
    """
    Canonical state object for one ingest pipeline run.

    Populated incrementally across the three passes so any stage can
    inspect what earlier stages produced without fishing through dicts.
    """
    # ── Spotify identity (set before PASS 1) ──────────────────────
    spotify_id: str = ""
    title: str = ""
    artist: str = ""
    artist_id: str = ""
    album: str = ""
    duration_ms: int = 0
    isrc: str = ""

    # ── Dedup identity (set by dedup_service, used by PASS 1) ──────
    identity_key: str = ""      # primary dedup key (spotify_id or content hash)

    # ── PASS 1 — download ─────────────────────────────────────────
    staged_path: str = ""       # absolute path in Staging/
    filename: str = ""          # e.g. "Sammy Virji - Freak.mp3"
    download_ok: bool = False
    download_source: str = ""   # "youtube" | "soundcloud"
    download_stage: int = 0     # 1–5 (which search stage succeeded)
    quality_report: dict = field(default_factory=dict)

    # ── PASS 2 — tagging + genre ──────────────────────────────────
    tagging: Optional[TaggingOutcome] = None
    genre: Optional[GenreDecision] = None

    # ── PASS 3 — atomic move ──────────────────────────────────────
    final_path: str = ""        # absolute path after successful move
    move_ok: bool = False
    move_error: str = ""
    retry_manifest_written: bool = False

    # ── Pipeline outcome ──────────────────────────────────────────
    skipped: bool = False
    skip_reason: str = ""       # "already_downloaded" | "in_progress" | "permanent_failure"
    failed: bool = False
    failure_stage: str = ""     # "download" | "tagging" | "move"
    failure_error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.download_ok and self.move_ok and not self.failed

    def to_log_line(self) -> str:
        if self.skipped:
            return f"[SKIP] {self.title} — {self.skip_reason}"
        if self.failed:
            return f"[FAIL:{self.failure_stage}] {self.title} — {self.failure_error[:80]}"
        genre_str = self.genre.folder if self.genre else "?"
        camelot = self.tagging.camelot if self.tagging else ""
        bpm = f"{self.tagging.bpm:.0f}" if self.tagging and self.tagging.bpm else ""
        meta = " | ".join(filter(None, [camelot, f"{bpm} BPM" if bpm else ""]))
        return f"[OK] {self.title} → {genre_str}" + (f" [{meta}]" if meta else "")
