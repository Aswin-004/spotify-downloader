#!/usr/bin/env python3
"""
Read-only check: how many .mp3 files in Library/Electronic carry the
TXXX:routing_source=catchall ID3 tag (the tag _task_retag_catchall in
maintenance_worker.py requires before it will touch a file) versus how
many don't (and would need a different path to get reclassified).

Touches nothing — just reads ID3 tags and prints counts.

Usage:
    python check_catchall_tag_coverage.py
    python check_catchall_tag_coverage.py --library "D:/DJ Music"
"""
import argparse
import os
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError

DEFAULT_LIBRARY_ROOT = r"C:\Users\Aswin-pc\Desktop\DJ music"


def main():
    parser = argparse.ArgumentParser(description="Check TXXX:routing_source=catchall tag coverage")
    parser.add_argument("--library", default=DEFAULT_LIBRARY_ROOT, help="DJ music root directory")
    args = parser.parse_args()

    electronic_dir = Path(args.library) / "Library" / "Electronic"
    if not electronic_dir.is_dir():
        print(f"Not found: {electronic_dir}")
        return

    tagged = []
    untagged = []
    unreadable = []

    for f in sorted(electronic_dir.glob("*.mp3")):
        try:
            tags = ID3(str(f))
            txxx = tags.get("TXXX:routing_source")
            if txxx and "catchall" in (txxx.text or []):
                tagged.append(f.name)
            else:
                untagged.append(f.name)
        except ID3NoHeaderError:
            untagged.append(f.name)
        except Exception as e:
            unreadable.append((f.name, str(e)))

    total = len(tagged) + len(untagged) + len(unreadable)
    print("=" * 70)
    print(f"Library/Electronic tag coverage — {electronic_dir}")
    print("=" * 70)
    print(f"Total .mp3 files scanned:                    {total}")
    print(f"  Tagged routing_source=catchall (auto-retag eligible): {len(tagged)}")
    print(f"  NOT tagged (won't be touched by maintenance worker):  {len(untagged)}")
    print(f"  Unreadable ID3 (error):                               {len(unreadable)}")
    print()
    if tagged:
        print(f"Sample tagged files (up to 10):")
        for name in tagged[:10]:
            print(f"  - {name}")
    if unreadable:
        print(f"\nUnreadable files (up to 10):")
        for name, err in unreadable[:10]:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
