#!/usr/bin/env python3
"""
manage_genre_playlists.py
=========================
Tell the app which of YOUR Spotify playlists means which genre folder. A song added to such a playlist is
filed in that crate automatically — no guessing (see services/genre_playlists.py).

  python manage_genre_playlists.py crates                         the crate names you can use
  python manage_genre_playlists.py add House <playlist link>      register a playlist (checks you own it)
  python manage_genre_playlists.py list                           what is registered
  python manage_genre_playlists.py check                          ask Spotify that each one is still readable
  python manage_genre_playlists.py remove <playlist link or id>

Spotify only lets this app read playlists the logged-in account OWNS. To use someone else's playlist, open it,
select all songs and add them to a playlist of your own, then register yours.

Each check costs one Spotify request per playlist.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services import genre_playlists as gp  # noqa: E402


def ownership_problem(meta: dict, me_id: str) -> str:
    """'' when the logged-in user owns the playlist, else a plain-language reason it cannot be used."""
    owner = (meta or {}).get("owner_id") or ""
    if not owner:
        return "Spotify did not say who owns this playlist."
    if me_id and owner != me_id:
        return (f"this playlist belongs to another Spotify account ({owner}). Spotify only lets the app read playlists "
                "you own. Open it, select all songs, add them to a playlist of your own, and register that one.")
    return ""


def _spotify():
    from services import auto_downloader as ad
    from services.spotify_service import get_spotify_service

    me = ""
    client = ad._get_user_sp()
    if client is not None:
        try:
            me = client.me().get("id") or ""
        except Exception:                                    # noqa: BLE001
            me = ""
    return get_spotify_service(), me


def cmd_crates(_args) -> int:
    print("Crates you can use:  " + ", ".join(gp.valid_crates()))
    return 0


def cmd_list(_args) -> int:
    entries = gp.load()
    if not entries:
        print("No genre playlists registered yet. Add one:  python manage_genre_playlists.py add House <playlist link>")
        return 0
    for e in entries:
        print(f"  {e['crate']:<14} {e['id']}  {e.get('name') or ''}")
    return 0


def cmd_add(args) -> int:
    pid = gp.extract_playlist_id(args.playlist)
    crate = gp.resolve_crate(args.crate)
    if not pid:
        print("That does not look like a Spotify playlist link.")
        return 2
    if not crate:
        print(f"'{args.crate}' is not one of your crates. Choose one of: {', '.join(gp.valid_crates())}")
        return 2
    name = args.name or ""
    if not args.no_check:
        try:
            svc, me = _spotify()
            meta = svc.get_playlist_meta(pid)
        except Exception as exc:                              # noqa: BLE001
            print(f"Spotify would not let me read that playlist ({exc}).\n"
                  "It is probably private or owned by someone else. Copy its songs into a playlist of your own and use that.")
            return 1
        problem = ownership_problem(meta, me)
        if problem:
            print("Not added: " + problem)
            return 1
        name = name or meta.get("name") or ""
        print(f"Checked: '{meta.get('name')}' is yours and has {meta.get('total')} songs.")
    entry = gp.add(pid, crate, name=name)
    print(f"Registered: songs added to {name or pid} will be filed in Library/{entry['crate']}.")
    return 0


def cmd_remove(args) -> int:
    if gp.remove(args.playlist):
        print("Removed.")
        return 0
    print("That playlist was not registered.")
    return 1


def cmd_check(_args) -> int:
    entries = gp.load()
    if not entries:
        print("Nothing registered.")
        return 0
    svc, me = _spotify()
    bad = 0
    for e in entries:
        try:
            meta = svc.get_playlist_meta(e["id"])
            problem = ownership_problem(meta, me)
        except Exception as exc:                              # noqa: BLE001
            problem = f"cannot be read ({exc})"
        if problem:
            bad += 1
            print(f"  PROBLEM  {e['crate']:<12} {e['id']}: {problem}")
        else:
            print(f"  ok       {e['crate']:<12} {meta.get('name')}  ({meta.get('total')} songs)")
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Register genre playlists (a song added there is filed in that crate).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crates").set_defaults(fn=cmd_crates)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    a = sub.add_parser("add")
    a.add_argument("crate", help="House, Punjabi, UK Garage ...")
    a.add_argument("playlist", help="playlist link or id")
    a.add_argument("--name", default="", help="a label for your own reference")
    a.add_argument("--no-check", action="store_true", help="do not ask Spotify (skips the ownership check)")
    a.set_defaults(fn=cmd_add)
    r = sub.add_parser("remove")
    r.add_argument("playlist")
    r.set_defaults(fn=cmd_remove)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
