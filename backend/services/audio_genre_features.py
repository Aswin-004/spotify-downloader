"""
audio_genre_features.py
=======================
Describe how a track SOUNDS, as a fixed-length vector, so a model can learn "what this library's
Techno / Bollywood / House / ... sound like" from the tracks that are already confirmed.

Why: on the real library 641 of 2,024 tracks had no genre evidence at all — obscure artists, no
Last.fm tags, no curated entry — and 571 had no usable artist tag. Metadata cannot place those;
the audio can (a Bollywood song filed under Trance is obvious to the ear: vocals, harmonium/tabla
timbre, tempo feel — and to MFCC / spectral / rhythm statistics).

Cheap and local: no API, no quota. A 45 s slice from the MIDDLE of each track (skipping the intro)
is decoded at 22.05 kHz mono; ~130 statistics are computed. Results are cached by
(file name, size, mtime) so a moved file is not re-analysed, and the run is resumable.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

SR = 22050
CLIP_SECONDS = 45.0
N_MFCC = 20
FEATURE_VERSION = 3                       # bump when the feature definition changes (invalidates the cache)
DEFAULT_CACHE_PATH = Path(__file__).resolve().parent.parent / "reports" / "audio_features_cache.json"

# frequency bands (Hz) whose share of the energy is a feature: bass weight separates
# D&B / dubstep / house / techno from Bollywood / pop at a glance
BANDS = [(20, 60), (60, 150), (150, 400), (400, 1500), (1500, 4000), (4000, 8000), (8000, 11000)]
# tempo bands (BPM) for the "rhythm fingerprint": how much of the pulse energy sits near ~87 / ~128 / ~174 ...
# (a single tempo estimate is fragile — it often returns 2/3 or 1/2 of the true tempo; the spread is not)
TEMPO_BANDS = [(60, 75), (75, 90), (90, 105), (105, 120), (120, 135), (135, 150), (150, 165), (165, 180), (180, 200)]

FEATURE_NAMES: List[str] = (
    [f"mfcc{i}_mean" for i in range(N_MFCC)] + [f"mfcc{i}_std" for i in range(N_MFCC)]
    + [f"{n}_{s}" for n in ("centroid", "bandwidth", "rolloff", "flatness", "zcr", "rms") for s in ("mean", "std")]
    + [f"contrast{i}" for i in range(7)] + [f"chroma{i}" for i in range(12)]
    + [f"band{i}" for i in range(len(BANDS))]
    + [f"tg{i}" for i in range(len(TEMPO_BANDS))]
    + ["onset_mean", "onset_std", "tempo", "tempo_folded", "pulse_clarity", "rms_db_p10", "rms_db_p90", "rms_db_range",
       "flux_mean", "percussive_ratio"]
)


KEY_FORMAT = 2        # 1 = name|size|mtime (invalidated by ANY tag edit); 2 = name|audio-bytes (tag-independent)


def _id3v2_size(path: str) -> int:
    """Bytes taken by a leading ID3v2 tag (header + body [+ footer]); 0 when there is none."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(10)
    except OSError:
        return 0
    if len(head) < 10 or head[:3] != b"ID3":
        return 0
    body = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]        # synchsafe integer
    return 10 + body + (10 if head[5] & 0x10 else 0)


def file_key(path: str) -> str:
    """Identity of the AUDIO in a file: name + audio bytes (file size minus the ID3v2 tag).
    Stable across a move within the library AND across tag edits (genre / artist / artwork) — the
    resort tool rewrites tags, and that must not throw away minutes of audio analysis per file."""
    st = os.stat(path)
    return f"{Path(path).name}|{st.st_size - _id3v2_size(path)}"


def legacy_key(path: str) -> str:
    """The v1 key (name|size|mtime), only to migrate caches written before KEY_FORMAT 2."""
    st = os.stat(path)
    return f"{Path(path).name}|{st.st_size}|{int(st.st_mtime)}"


def _fold_tempo(bpm: float) -> float:
    """Fold a tempo into [80, 160): halving/doubling errors then give the same value."""
    if bpm <= 0:
        return 0.0
    while bpm < 80:
        bpm *= 2
    while bpm >= 160:
        bpm /= 2
    return float(bpm)


def _slice_offset(duration: float) -> float:
    if duration <= CLIP_SECONDS:
        return 0.0
    return float(min(max(duration * 0.35, 0.0), duration - CLIP_SECONDS))


def extract_features(path: str) -> Optional[List[float]]:
    """The feature vector for one file (len == len(FEATURE_NAMES)), or None if it cannot be decoded."""
    import librosa
    import soundfile as sf

    try:
        try:
            duration = float(sf.info(path).duration)
        except Exception:
            duration = 0.0
        y, sr = librosa.load(path, sr=SR, mono=True, offset=_slice_offset(duration), duration=CLIP_SECONDS)
        if y.size < SR * 5 or not np.isfinite(y).all():
            return None
        if float(np.max(np.abs(y))) < 1e-4:                   # silence
            return None

        n_fft, hop = 2048, 512
        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
        power = S ** 2
        mel = librosa.feature.melspectrogram(S=power, sr=sr, n_mels=64)
        logmel = librosa.power_to_db(mel)
        mfcc = librosa.feature.mfcc(S=logmel, n_mfcc=N_MFCC)

        f: List[float] = []
        f += list(mfcc.mean(axis=1)) + list(mfcc.std(axis=1))
        for arr in (
            librosa.feature.spectral_centroid(S=S, sr=sr),
            librosa.feature.spectral_bandwidth(S=S, sr=sr),
            librosa.feature.spectral_rolloff(S=S, sr=sr),
            librosa.feature.spectral_flatness(S=S),
            librosa.feature.zero_crossing_rate(y, frame_length=n_fft, hop_length=hop),
            librosa.feature.rms(S=S),
        ):
            f += [float(arr.mean()), float(arr.std())]
        f += list(librosa.feature.spectral_contrast(S=S, sr=sr, n_bands=6).mean(axis=1))          # 7 values
        f += list(librosa.feature.chroma_stft(S=power, sr=sr).mean(axis=1))                       # 12 values

        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        total = float(power.sum()) + 1e-12
        for lo, hi in BANDS:
            f.append(float(power[(freqs >= lo) & (freqs < hi)].sum() / total))

        oenv = librosa.onset.onset_strength(S=logmel, sr=sr, hop_length=hop)
        try:
            from librosa.feature import rhythm as _rhythm
            tempo_fn = _rhythm.tempo
        except ImportError:                                                                        # older librosa
            tempo_fn = librosa.beat.tempo
        tempo = float(np.atleast_1d(tempo_fn(onset_envelope=oenv, sr=sr, hop_length=hop))[0])
        # rhythm fingerprint: mean tempogram energy per BPM band, normalised to sum to 1
        tg = librosa.feature.tempogram(onset_envelope=oenv, sr=sr, hop_length=hop, win_length=384).mean(axis=1)
        tg_bpm = librosa.tempo_frequencies(384, sr=sr, hop_length=hop)
        band_e = [float(tg[(tg_bpm >= lo) & (tg_bpm < hi)].sum()) for lo, hi in TEMPO_BANDS]
        band_total = sum(band_e) + 1e-12
        tempo_bands = [e / band_total for e in band_e]
        ac = librosa.autocorrelate(oenv - oenv.mean())
        clarity = float(ac[1:].max() / (ac[0] + 1e-12)) if ac.size > 1 else 0.0

        rms_db = librosa.amplitude_to_db(librosa.feature.rms(S=S)[0] + 1e-9)
        p10, p90 = float(np.percentile(rms_db, 10)), float(np.percentile(rms_db, 90))
        flux = float(np.mean(np.maximum(0.0, np.diff(logmel, axis=1))))
        # harmonic/percussive balance from the spectrogram (median filtering is cheap on 45 s)
        harm, perc = librosa.decompose.hpss(S, margin=1.0)
        perc_ratio = float(perc.sum() / (harm.sum() + perc.sum() + 1e-12))

        f += tempo_bands
        f += [float(oenv.mean()), float(oenv.std()), tempo, _fold_tempo(tempo), clarity, p10, p90, p90 - p10, flux, perc_ratio]
        if len(f) != len(FEATURE_NAMES) or not np.isfinite(f).all():
            return None
        return [float(v) for v in f]
    except Exception:
        return None


def _extract_one(path: str):
    return path, extract_features(path)


class FeatureCache:
    """{key: [features]} on disk, versioned. Load once, write atomically."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_CACHE_PATH
        self.data: Dict[str, Optional[List[float]]] = {}
        self.legacy: Dict[str, Optional[List[float]]] = {}          # v1-keyed entries, promoted on first use
        if self.path.is_file():
            try:
                blob = json.loads(self.path.read_text(encoding="utf-8"))
                if blob.get("version") == FEATURE_VERSION:
                    if blob.get("key_format") == KEY_FORMAT:
                        self.data = blob.get("features", {})
                    else:
                        self.legacy = blob.get("features", {})
            except Exception:
                self.data, self.legacy = {}, {}

    def lookup(self, path: str):
        """(found, vector). Promotes a legacy-keyed entry to the tag-independent key."""
        key = file_key(path)
        if key in self.data:
            return True, self.data[key]
        old = legacy_key(path)
        if old in self.legacy:
            self.data[key] = self.legacy[old]
            return True, self.data[key]
        return False, None

    def has(self, path: str) -> bool:
        return self.lookup(path)[0]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"version": FEATURE_VERSION, "key_format": KEY_FORMAT, "names": FEATURE_NAMES,
                       "features": self.data}, fh)
        os.replace(tmp, self.path)


def extract_many(
    paths: Sequence[str],
    cache: FeatureCache,
    *,
    n_jobs: int = 8,
    chunk: int = 60,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Optional[List[float]]]:
    """Features for every path (cache first; the rest in parallel, saved every `chunk` files).
    Returns {path: vector-or-None}. A file that cannot be decoded is cached as None (not retried)."""
    from joblib import Parallel, delayed

    keys = {p: file_key(p) for p in paths}
    todo = [p for p in paths if not cache.has(p)]
    done = 0
    for i in range(0, len(todo), chunk):
        batch = todo[i:i + chunk]
        for path, vec in Parallel(n_jobs=n_jobs, backend="loky")(delayed(_extract_one)(p) for p in batch):
            cache.data[keys[path]] = vec
        cache.save()
        done += len(batch)
        if progress:
            progress(done, len(todo))
    return {p: cache.data.get(keys[p]) for p in paths}
