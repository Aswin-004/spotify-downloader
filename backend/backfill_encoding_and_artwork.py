"""
One-shot: fix Latin-1 encoding corruption in ID3 tags + fill missing artwork
via iTunes Search API (free, no key, great coverage including Bollywood/Latin).
"""
import sys, os, time, urllib.request, urllib.parse, json
sys.path.insert(0, str(os.path.dirname(__file__)))
os.environ.update({'FLASK_ENV':'development',
                   'REDIRECT_URI':'http://127.0.0.1:8888/callback',
                   'ALLOWED_ORIGINS':'http://localhost:5173'})

from pathlib import Path
from mutagen.id3 import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
from config import config

BASE = Path(config.BASE_DOWNLOAD_DIR) / 'Library'
UA   = 'ObsidianDJ/1.0 (aswin.abhinab22@gmail.com)'


def _fix_mojibake(s: str) -> str:
    """Convert Latin-1 interpreted UTF-8 back to correct Unicode."""
    try:
        return s.encode('latin-1').decode('utf-8')
    except Exception:
        return s


def _itunes_artwork(title: str, artist: str) -> bytes | None:
    clean_title  = title.split(' - ')[0].split('(')[0].strip()
    clean_artist = artist.split('&')[0].split(',')[0].strip()
    q = f"{clean_title} {clean_artist}".strip()
    url = f"https://itunes.apple.com/search?{urllib.parse.urlencode({'term':q,'media':'music','limit':1,'entity':'song'})}"
    try:
        req  = urllib.request.Request(url, headers={'User-Agent': UA})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        results = data.get('results', [])
        if not results:
            return None
        art_url = results[0].get('artworkUrl100', '').replace('100x100bb', '600x600bb')
        if not art_url:
            return None
        req2 = urllib.request.Request(art_url, headers={'User-Agent': UA})
        return urllib.request.urlopen(req2, timeout=10).read()
    except Exception as e:
        print(f"    iTunes error: {e}")
        return None


def main():
    files_to_fix = []
    for f in sorted(BASE.rglob('*.mp3')):
        try:
            t = ID3(str(f))
            needs_art      = not t.getall('APIC')
            title_raw      = str(t.get('TIT2', '')).strip()
            artist_raw     = str(t.get('TPE1', '')).strip()
            title_fixed    = _fix_mojibake(title_raw)
            artist_fixed   = _fix_mojibake(artist_raw)
            has_encoding   = (title_fixed != title_raw or artist_fixed != artist_raw)
            if needs_art or has_encoding:
                files_to_fix.append((f, t, title_raw, artist_raw,
                                     title_fixed, artist_fixed,
                                     needs_art, has_encoding))
        except Exception:
            pass

    print(f"Files needing artwork: {sum(1 for *_,na,_ in files_to_fix if na)}")
    print(f"Files with encoding fix: {sum(1 for *_,_,he in files_to_fix if he)}")
    print()

    art_ok = art_fail = enc_fixed = 0

    for (f, tags, ti_raw, ar_raw, ti_fix, ar_fix, needs_art, has_enc) in files_to_fix:
        print(f"[{f.parent.name}] {f.name[:55]}")
        changed = False

        if has_enc:
            print(f"  encoding: {ti_raw!r} → {ti_fix!r}")
            if ti_fix != ti_raw:
                tags['TIT2'] = TIT2(encoding=3, text=[ti_fix])
                changed = True
            if ar_fix != ar_raw:
                print(f"  artist:   {ar_raw!r} → {ar_fix!r}")
                tags['TPE1'] = TPE1(encoding=3, text=[ar_fix])
                changed = True
            enc_fixed += 1

        if needs_art:
            # Use fixed title/artist for better search hit
            art_data = _itunes_artwork(ti_fix, ar_fix)
            if art_data:
                tags['APIC'] = APIC(encoding=3, mime='image/jpeg',
                                    type=3, desc='Cover', data=art_data)
                print(f"  artwork: ✓ iTunes ({len(art_data)//1024} KB)")
                art_ok += 1
                changed = True
            else:
                print(f"  artwork: ✗ not found")
                art_fail += 1

        if changed:
            tags.save(str(f))

        time.sleep(0.4)  # polite to iTunes

    print(f"\nDone:")
    print(f"  Artwork embedded : {art_ok}")
    print(f"  Artwork not found: {art_fail}")
    print(f"  Encoding fixed   : {enc_fixed}")


if __name__ == '__main__':
    main()
