"""Tests for services/library_migrator.py"""
import json
import pytest
from pathlib import Path


# ── Task 1: Config ───────────────────────────────────────────────

def test_load_config_returns_categories_and_mappings(tmp_path):
    cfg = tmp_path / "artist_categories.json"
    cfg.write_text(json.dumps({
        "categories": ["Punjabi", "English"],
        "mappings": {"Drake": "English", "Diljit Dosanjh": "Punjabi"}
    }))
    from services.library_migrator import load_config
    data = load_config(cfg)
    assert data["categories"] == ["Punjabi", "English"]
    assert data["mappings"]["Drake"] == "English"


def test_load_config_missing_file_raises(tmp_path):
    from services.library_migrator import load_config
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nonexistent.json")


def test_save_config_writes_json(tmp_path):
    cfg = tmp_path / "cfg.json"
    data = {"categories": ["Punjabi"], "mappings": {"Diljit Dosanjh": "Punjabi"}}
    from services.library_migrator import save_config
    save_config(cfg, data)
    loaded = json.loads(cfg.read_text())
    assert loaded["mappings"]["Diljit Dosanjh"] == "Punjabi"


def test_save_config_roundtrip(tmp_path):
    cfg = tmp_path / "cfg.json"
    original = {"categories": ["Hindi"], "mappings": {"Pritam": "Hindi", "hugel": None}}
    from services.library_migrator import save_config, load_config
    save_config(cfg, original)
    loaded = load_config(cfg)
    assert loaded["mappings"]["hugel"] is None
    assert loaded["mappings"]["Pritam"] == "Hindi"


# ── Task 2: File ops ─────────────────────────────────────────────

def test_md5_file_returns_hex_string(tmp_path):
    f = tmp_path / "a.mp3"
    f.write_bytes(b"hello")
    from services.library_migrator import md5_file
    result = md5_file(f)
    assert isinstance(result, str)
    assert len(result) == 32


def test_md5_file_same_content_same_hash(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"same content")
    b.write_bytes(b"same content")
    from services.library_migrator import md5_file
    assert md5_file(a) == md5_file(b)


def test_md5_file_different_content_different_hash(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"aaa")
    b.write_bytes(b"bbb")
    from services.library_migrator import md5_file
    assert md5_file(a) != md5_file(b)


def test_resolve_dest_path_no_collision(tmp_path):
    dest_root = tmp_path / "dest"
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result == dest_root / "Punjabi" / "song.mp3"
    assert (dest_root / "Punjabi").is_dir()


def test_resolve_dest_path_collision_appends_suffix(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "Punjabi").mkdir(parents=True)
    (dest_root / "Punjabi" / "song.mp3").write_bytes(b"existing")
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result.name == "song_1.mp3"


def test_resolve_dest_path_double_collision(tmp_path):
    dest_root = tmp_path / "dest"
    (dest_root / "Punjabi").mkdir(parents=True)
    (dest_root / "Punjabi" / "song.mp3").write_bytes(b"x")
    (dest_root / "Punjabi" / "song_1.mp3").write_bytes(b"y")
    from services.library_migrator import resolve_dest_path
    result = resolve_dest_path(dest_root, "Punjabi", "song.mp3")
    assert result.name == "song_2.mp3"


def test_copy_verify_delete_success(tmp_path):
    src = tmp_path / "src" / "song.mp3"
    src.parent.mkdir()
    src.write_bytes(b"audio data")
    dest = tmp_path / "dest" / "Punjabi" / "song.mp3"
    dest.parent.mkdir(parents=True)
    from services.library_migrator import copy_verify_delete
    ok = copy_verify_delete(src, dest)
    assert ok is True
    assert dest.exists()
    assert not src.exists()


def test_copy_verify_delete_mismatch_deletes_dest(tmp_path, monkeypatch):
    src = tmp_path / "song.mp3"
    src.write_bytes(b"real content")
    dest = tmp_path / "dest.mp3"
    from services import library_migrator
    call_count = {"n": 0}
    original_md5 = library_migrator.md5_file
    def fake_md5(path):
        call_count["n"] += 1
        if call_count["n"] == 2:
            return "badhash000000000000000000000000000"
        return original_md5(path)
    monkeypatch.setattr(library_migrator, "md5_file", fake_md5)
    ok = library_migrator.copy_verify_delete(src, dest)
    assert ok is False
    assert not dest.exists()
    assert src.exists()


def test_fmt_bytes(tmp_path):
    from services.library_migrator import _fmt_bytes
    assert _fmt_bytes(500) == "500.0 B"
    assert _fmt_bytes(1024) == "1.0 KB"
    assert _fmt_bytes(1024 * 1024) == "1.0 MB"
    assert _fmt_bytes(1024 ** 3) == "1.0 GB"
