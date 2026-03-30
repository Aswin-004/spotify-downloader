"""
Spotify Track Metadata Analysis
================================
Demonstrates all 6 pandas data-cleaning categories from the reference guide
using real Spotify track data cached by this project.

Run:
    python backend/metadata_analysis.py
Output:
    Console report + backend/cache/cleaned_tracks.csv
"""

import json
import os
import pandas as pd

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE = os.path.dirname(__file__)
SPOTIFY_CACHE   = os.path.join(BASE, "cache", "spotify_cache.json")
PLAYLIST_CACHE  = os.path.join(BASE, "cache", "playlist_snapshots.json")
INGEST_TRACKS   = os.path.join(BASE, "ingest_tracks.json")
OUTPUT_CSV      = os.path.join(BASE, "cache", "cleaned_tracks.csv")

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}\n  {title}\n{SEPARATOR}")


# ---------------------------------------------------------------------------
# PHASE 1 — Load raw data
# ---------------------------------------------------------------------------
section("PHASE 1 — Loading raw data")

# --- spotify_cache.json: {track_id: {data: {...}, fetched_at: float}} ---
with open(SPOTIFY_CACHE, "r", encoding="utf-8") as f:
    raw_cache: dict = json.load(f)

cache_records = []
for track_id, entry in raw_cache.items():
    record = dict(entry.get("data", {}))
    record["fetched_at"] = entry.get("fetched_at")
    cache_records.append(record)

print(f"  Loaded {len(cache_records)} track(s) from spotify_cache.json")

# --- ingest_tracks.json: {track_ids: [...]} ---
with open(INGEST_TRACKS, "r", encoding="utf-8") as f:
    raw_ingest: dict = json.load(f)

downloaded_ids: set = set(raw_ingest.get("track_ids", []))
print(f"  Loaded {len(downloaded_ids)} downloaded track ID(s) from ingest_tracks.json")

# --- playlist_snapshots.json: {playlist_id: {tracks: [...], fetched_at}} ---
with open(PLAYLIST_CACHE, "r", encoding="utf-8") as f:
    raw_playlists: dict = json.load(f)

playlist_records = []
for playlist_id, snapshot in raw_playlists.items():
    for track in snapshot.get("tracks", []):
        record = dict(track)
        record["playlist_id"] = playlist_id
        playlist_records.append(record)

print(f"  Loaded {len(playlist_records)} track(s) from playlist_snapshots.json")

# Build primary DataFrame from cache
df_tracks = pd.DataFrame(cache_records)

# Build secondary DataFrame from playlist snapshots
df_playlists = pd.DataFrame(playlist_records)

# Build downloaded-status DataFrame for merging later
df_downloaded = pd.DataFrame(
    [{"id": tid, "downloaded": True} for tid in downloaded_ids]
)


# ---------------------------------------------------------------------------
# SECTION 1 — Data Inspection
# ---------------------------------------------------------------------------
section("SECTION 1 — Data Inspection")

print("\n--- df.head() ---")
print(df_tracks.head())

print("\n--- df.info() ---")
df_tracks.info()

print("\n--- df.describe() ---")
print(df_tracks.describe(include="all"))


# ---------------------------------------------------------------------------
# SECTION 2 — Missing Data Handling
# ---------------------------------------------------------------------------
section("SECTION 2 — Missing Data Handling")

print("\n--- df.isnull().sum() ---")
print(df_tracks.isnull().sum())

# Drop rows that are completely missing critical identity fields
before = len(df_tracks)
df_tracks = df_tracks.dropna(subset=["id", "title", "artist"])
after = len(df_tracks)
print(f"\n--- df.dropna(subset=['id','title','artist']) ---")
print(f"  Rows before: {before}  |  Rows after: {after}  |  Dropped: {before - after}")

# Fill optional fields with sensible defaults
df_tracks = df_tracks.fillna({
    "release_date": "Unknown",
    "album":        "Unknown Album",
    "external_url": "",
    "fetched_at":   0.0,
})
print("\n--- df.fillna() applied ---")
print(f"  Remaining nulls:\n{df_tracks.isnull().sum()}")


# ---------------------------------------------------------------------------
# SECTION 3 — Data Cleaning & Transformation
# ---------------------------------------------------------------------------
section("SECTION 3 — Data Cleaning & Transformation")

# Remove duplicate Spotify IDs
before = len(df_tracks)
df_tracks = df_tracks.drop_duplicates(subset=["id"])
print(f"--- df.drop_duplicates(subset=['id']) ---")
print(f"  Rows before: {before}  |  Rows after: {len(df_tracks)}")

# Rename columns to cleaner names
df_tracks = df_tracks.rename(columns={
    "title":  "track_name",
    "artist": "primary_artist",
})
print("\n--- df.rename() applied ---")
print(f"  Columns: {list(df_tracks.columns)}")

# Ensure duration_ms is numeric int (fillna with 0 to avoid float cast issues)
df_tracks["duration_ms"] = pd.to_numeric(df_tracks["duration_ms"], errors="coerce").fillna(0).astype(int)
print("\n--- df.astype({'duration_ms': int}) applied ---")
print(df_tracks["duration_ms"].dtype)

# Derived column: duration in seconds via df.apply
df_tracks["duration_sec"] = df_tracks["duration_ms"].apply(lambda ms: ms // 1000)
print("\n--- df.apply() — added 'duration_sec' column ---")
print(df_tracks[["track_name", "duration_ms", "duration_sec"]].head())

# Reset index after all drops
df_tracks = df_tracks.reset_index(drop=True)
print("\n--- df.reset_index(drop=True) applied ---")

# Drop columns not needed in the cleaned report
df_tracks = df_tracks.drop(["fetched_at"], axis=1)
print("\n--- df.drop(['fetched_at'], axis=1) applied ---")
print(f"  Final columns: {list(df_tracks.columns)}")


# ---------------------------------------------------------------------------
# SECTION 4 — Data Selection & Filtering
# ---------------------------------------------------------------------------
section("SECTION 4 — Data Selection & Filtering")

# df.loc — label-based filtering: tracks longer than 4 minutes
long_tracks = df_tracks.loc[df_tracks["duration_sec"] > 240, ["track_name", "primary_artist", "duration_sec"]]
print(f"\n--- df.loc[duration_sec > 240] — tracks longer than 4 min ---")
print(long_tracks.to_string(index=False) if not long_tracks.empty else "  No tracks found.")

# df.iloc — positional access: first 5 rows, first 3 columns
print("\n--- df.iloc[0:5, 0:3] ---")
print(df_tracks.iloc[0:5, 0:3])

# Conditional filter — tracks where artist name starts with a capital letter in A-M range
filtered = df_tracks[df_tracks["primary_artist"].str[0].str.upper() <= "M"]
print(f"\n--- df[primary_artist starts A-M] --- ({len(filtered)} tracks)")
print(filtered[["track_name", "primary_artist"]].head())


# ---------------------------------------------------------------------------
# SECTION 5 — Data Aggregation & Analysis
# ---------------------------------------------------------------------------
section("SECTION 5 — Data Aggregation & Analysis")

# groupby + agg: count tracks per artist
tracks_per_artist = (
    df_tracks.groupby("primary_artist")
    .agg(track_count=("track_name", "count"), avg_duration_sec=("duration_sec", "mean"))
    .reset_index()
)
print("\n--- df.groupby('primary_artist').agg() ---")
print(tracks_per_artist.to_string(index=False))

# sort_values: longest tracks first
sorted_by_duration = df_tracks.sort_values("duration_sec", ascending=False)
print("\n--- df.sort_values('duration_sec', ascending=False) ---")
print(sorted_by_duration[["track_name", "primary_artist", "duration_sec"]].head().to_string(index=False))

# value_counts: most represented artists
print("\n--- df['primary_artist'].value_counts() ---")
print(df_tracks["primary_artist"].value_counts().head(10))

# apply: classify track length
def classify_length(sec: int) -> str:
    if sec < 180:
        return "Short"
    elif sec < 300:
        return "Medium"
    return "Long"

df_tracks["length_category"] = df_tracks["duration_sec"].apply(classify_length)
print("\n--- df.apply() — added 'length_category' column ---")
print(df_tracks["length_category"].value_counts())

# pivot_table: avg duration per length category (after merge with download status)
# First do the merge so we have a 'downloaded' column
df_tracks = pd.merge(df_tracks, df_downloaded, on="id", how="left")
df_tracks["downloaded"] = df_tracks["downloaded"].fillna(False)

if df_tracks["downloaded"].any():
    pivot = df_tracks.pivot_table(
        values="duration_sec",
        index="length_category",
        columns="downloaded",
        aggfunc="mean",
    )
    print("\n--- df.pivot_table(duration_sec, index=length_category, columns=downloaded) ---")
    print(pivot)
else:
    print("\n--- pivot_table skipped: no downloaded tracks overlap with cache ---")
    print("  (ingest_tracks IDs may differ from cached track IDs)")


# ---------------------------------------------------------------------------
# SECTION 6 — Data Combining / Merging
# ---------------------------------------------------------------------------
section("SECTION 6 — Data Combining / Merging")

# Normalize df_playlists to match df_tracks columns where possible
if not df_playlists.empty:
    df_playlists = df_playlists.rename(columns={"title": "track_name", "artist": "primary_artist"})
    df_playlists["duration_sec"] = (
        pd.to_numeric(df_playlists.get("duration_ms", pd.Series(dtype=float)), errors="coerce")
        .fillna(0)
        .astype(int) // 1000
    )
    df_playlists["source"] = "playlist_snapshot"
    df_tracks["source"] = df_tracks.get("source", "spotify_cache")

    # Keep only common columns for concat
    common_cols = list(set(df_tracks.columns) & set(df_playlists.columns))
    combined = pd.concat(
        [df_tracks[common_cols], df_playlists[common_cols]],
        ignore_index=True,
    )
    print(f"\n--- pd.concat([df_tracks, df_playlists]) ---")
    print(f"  df_tracks rows  : {len(df_tracks)}")
    print(f"  df_playlists rows: {len(df_playlists)}")
    print(f"  Combined rows   : {len(combined)}")

    # pd.merge: join combined with downloaded status
    merged = pd.merge(combined, df_downloaded, on="id", how="left")
    merged["downloaded"] = merged["downloaded"].fillna(False)
    print(f"\n--- pd.merge(combined, df_downloaded, on='id', how='left') ---")
    print(f"  Merged shape: {merged.shape}")
    print(merged[["id", "track_name", "primary_artist", "downloaded"]].head().to_string(index=False))

    # df1.join: join on index (demo only — aligning on index after reset)
    left  = df_tracks[["id", "track_name"]].reset_index(drop=True)
    right = df_downloaded.set_index("id").rename(columns={"downloaded": "in_ingest"})
    joined = left.join(right, on="id", how="left")
    joined["in_ingest"] = joined["in_ingest"].fillna(False)
    print(f"\n--- df1.join(df2, on='id') ---")
    print(joined.head().to_string(index=False))
else:
    print("  df_playlists is empty — skipping combine/merge demo.")


# ---------------------------------------------------------------------------
# Export cleaned DataFrame
# ---------------------------------------------------------------------------
section("EXPORT — cleaned_tracks.csv")

export_cols = [c for c in ["id", "track_name", "primary_artist", "album",
                            "duration_ms", "duration_sec", "length_category",
                            "release_date", "downloaded", "external_url"]
               if c in df_tracks.columns]

df_tracks[export_cols].to_csv(OUTPUT_CSV, index=False)
print(f"\n  Saved {len(df_tracks)} rows to:\n  {OUTPUT_CSV}")
print(f"\n  Columns exported: {export_cols}")

section("DONE")
