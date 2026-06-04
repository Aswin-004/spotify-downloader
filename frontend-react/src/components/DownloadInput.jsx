import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Loader2, Music2, Disc3, AlertTriangle, ArrowRight } from 'lucide-react';
import { api } from '@/services/api';
import { formatDuration } from '@/lib/utils';

const SPOTIFY_URL_RE = /open\.spotify\.com\/(track|album|playlist)\/[A-Za-z0-9]+/;

// Shared sharp-corner button style
function cmdBtn(active, accent = '#8B5CF6') {
  return {
    height: 42, padding: '0 18px',
    background: active ? accent : 'var(--surface-1)',
    color: active ? '#fff' : 'var(--text-muted)',
    border: 'none', borderRadius: 3,
    fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
    cursor: active ? 'pointer' : 'not-allowed',
    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
    transition: 'background 0.12s',
    flexShrink: 0,
  };
}

export default function DownloadInput() {
  const [url,         setUrl]         = useState('');
  const [loading,     setLoading]     = useState(false);
  const [metadata,    setMetadata]    = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [error,       setError]       = useState('');

  async function handleFetch() {
    if (!url.trim()) return;
    if (!SPOTIFY_URL_RE.test(url.trim())) {
      setError('Enter a valid Spotify track, album, or playlist URL');
      return;
    }
    setLoading(true);
    setError('');
    setMetadata(null);
    try {
      setMetadata(await api.fetchMetadata(url.trim()));
    } catch (err) {
      setError(err.message || 'Failed to fetch metadata');
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    setError('');
    try {
      const res = await api.downloadStream(url.trim());
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Download failed (${res.status})`);
      }
      const cd       = res.headers.get('Content-Disposition') || '';
      const match    = cd.match(/filename[^;=\n]*=['"]?(.*?)['"]?(?:;|$)/);
      const filename = match ? match[1] : `${metadata?.title || 'track'}.mp3`;
      const blob     = await res.blob();
      const objUrl   = URL.createObjectURL(blob);
      const link     = document.createElement('a');
      link.href      = objUrl;
      link.download  = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objUrl);
      setUrl('');
      setMetadata(null);
    } catch (err) {
      setError(err.message || 'Download failed');
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>

      {/* ── Command input line ──────────────────────────────────── */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0  }}
        transition={{ duration: 0.3, ease: [0.22,1,0.36,1] }}
      >
        <div
          style={{
            display: 'flex', alignItems: 'stretch',
            border: '1px solid var(--border-default)',
            borderRadius: 4, background: 'var(--surface-0)',
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
          onFocusCapture={e => {
            e.currentTarget.style.borderColor = 'var(--accent-cyan)';
            e.currentTarget.style.boxShadow   = '0 0 0 1px var(--accent-cyan)';
          }}
          onBlurCapture={e => {
            e.currentTarget.style.borderColor = 'var(--border-default)';
            e.currentTarget.style.boxShadow   = 'none';
          }}
        >
          {/* Terminal prompt */}
          <div style={{
            display: 'flex', alignItems: 'center',
            paddingLeft: 14, paddingRight: 8,
            color: 'var(--accent-cyan)', fontSize: 15, fontWeight: 700,
            flexShrink: 0, userSelect: 'none', letterSpacing: '-0.02em',
          }}>
            ▸
          </div>

          {/* URL input */}
          <input
            value={url}
            onChange={e => { setUrl(e.target.value); if (error) setError(''); }}
            onKeyDown={e => e.key === 'Enter' && handleFetch()}
            placeholder="open.spotify.com/track/…"
            disabled={loading}
            style={{
              flex: 1, height: 52, background: 'transparent', border: 'none',
              outline: 'none', color: 'var(--text-primary)',
              fontSize: 13, fontFamily: 'ui-monospace, "SF Mono", monospace',
              letterSpacing: '0.01em',
            }}
          />

          {/* Fetch button */}
          <button
            onClick={handleFetch}
            disabled={loading || !url.trim()}
            style={{
              height: 52, padding: '0 22px',
              background: loading || !url.trim() ? 'transparent' : 'var(--accent-violet)',
              color: loading || !url.trim() ? 'var(--text-muted)' : '#fff',
              border: 'none', borderLeft: '1px solid var(--border-subtle)',
              borderRadius: '0 3px 3px 0',
              fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
              cursor: loading || !url.trim() ? 'not-allowed' : 'pointer',
              transition: 'background 0.15s, color 0.15s',
              flexShrink: 0, display: 'flex', alignItems: 'center', gap: 7,
            }}
          >
            {loading && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            {loading ? 'SCANNING…' : 'FETCH'}
          </button>
        </div>

        {/* Type labels */}
        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
          {['TRACK', 'ALBUM', 'PLAYLIST'].map(t => (
            <span key={t} style={{
              fontSize: 9, fontWeight: 700, letterSpacing: '0.12em',
              color: 'var(--text-muted)', padding: '2px 6px',
              border: '1px solid var(--border-subtle)', borderRadius: 2,
            }}>{t}</span>
          ))}
        </div>
      </motion.div>

      {/* ── Inline error ────────────────────────────────────────── */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.15 }}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              fontSize: 12, color: 'var(--accent-rose)', paddingLeft: 2,
            }}
          >
            <span style={{ fontWeight: 700, fontSize: 10 }}>✕</span>
            {error}
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── Metadata result card ─────────────────────────────────── */}
      <AnimatePresence>
        {metadata && (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1,  y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2, ease: [0.22,1,0.36,1] }}
            style={{
              borderRadius: 4, overflow: 'hidden',
              border: '1px solid var(--border-default)',
              background: 'var(--surface-0)',
            }}
          >
            {/* Accent top edge */}
            <div style={{ height: 2, background: 'var(--accent-violet)' }} />

            <div style={{ padding: '14px 16px 16px' }}>

              {/* Track info header */}
              <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                <div style={{
                  width: 48, height: 48, borderRadius: 3, flexShrink: 0,
                  background: 'var(--surface-2)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  {metadata.type === 'album'
                    ? <Disc3  style={{ width: 20, height: 20, color: 'var(--accent-violet)' }} />
                    : <Music2 style={{ width: 20, height: 20, color: 'var(--accent-violet)' }} />}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  {/* Status chips */}
                  <div style={{ display: 'flex', gap: 5, marginBottom: 5 }}>
                    <span style={{
                      fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
                      color: 'var(--accent-emerald)', textTransform: 'uppercase',
                    }}>
                      {metadata.type} FOUND
                    </span>
                    <span style={{
                      fontSize: 9, fontWeight: 700, letterSpacing: '0.08em',
                      color: metadata.source === 'cache' ? 'var(--accent-amber)' : 'var(--text-muted)',
                      background: metadata.source === 'cache' ? 'var(--accent-amber-dim)' : 'var(--surface-1)',
                      padding: '1px 5px', borderRadius: 2,
                    }}>
                      {metadata.source === 'cache' ? 'CACHED' : 'SPOTIFY'}
                    </span>
                  </div>
                  {/* Title */}
                  <div style={{
                    fontSize: 15, fontWeight: 700, color: 'var(--text-primary)',
                    letterSpacing: '-0.01em',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {metadata.type === 'album' ? metadata.name : metadata.title}
                  </div>
                  {/* Sub info */}
                  <div style={{
                    fontSize: 11, color: 'var(--text-tertiary)', marginTop: 3,
                    display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center',
                  }}>
                    <span>{metadata.artist}</span>
                    {metadata.type === 'track' && metadata.album && (
                      <><span style={{ color: 'var(--text-muted)' }}>·</span><span>{metadata.album}</span></>
                    )}
                    {metadata.type === 'track' && metadata.duration > 0 && (
                      <><span style={{ color: 'var(--text-muted)' }}>·</span>
                      <span style={{ fontFamily: 'monospace' }}>{formatDuration(metadata.duration)}</span></>
                    )}
                    {metadata.type === 'album' && (
                      <><span style={{ color: 'var(--text-muted)' }}>·</span><span>{metadata.total_tracks} tracks</span></>
                    )}
                  </div>
                </div>
              </div>

              {/* Duplicate warning */}
              {metadata.type === 'track' && metadata.already_in_library && (
                <div style={{
                  display: 'flex', gap: 7, padding: '7px 10px', marginBottom: 10,
                  borderRadius: 2, background: 'rgba(245,158,11,0.07)',
                  border: '1px solid rgba(245,158,11,0.2)',
                }}>
                  <AlertTriangle style={{ width: 12, height: 12, color: 'var(--accent-amber)', flexShrink: 0, marginTop: 1 }} />
                  <div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--accent-amber)', letterSpacing: '0.02em' }}>
                      ALREADY IN LIBRARY
                    </div>
                    {metadata.existing_folder && (
                      <div style={{ fontSize: 10, color: 'rgba(245,158,11,0.55)', marginTop: 1 }}>
                        {metadata.existing_folder}
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Album tracks */}
              {metadata.type === 'album' && metadata.tracks && (
                <div style={{
                  maxHeight: 180, overflowY: 'auto', marginBottom: 12,
                  border: '1px solid var(--border-subtle)', borderRadius: 2,
                }}>
                  {metadata.tracks.map((t, i) => (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '6px 10px',
                      borderBottom: i < metadata.tracks.length - 1
                        ? '1px solid var(--border-subtle)' : 'none',
                    }}>
                      <span style={{
                        width: 18, textAlign: 'right',
                        fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace',
                      }}>
                        {i + 1}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: 12, color: 'var(--text-primary)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{t.title}</div>
                        <div style={{
                          fontSize: 10, color: 'var(--text-muted)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>{t.artist}</div>
                      </div>
                      <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'monospace', flexShrink: 0 }}>
                        {formatDuration(t.duration || 0)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              {/* Download CTA */}
              <button
                onClick={handleDownload}
                disabled={downloading}
                style={cmdBtn(!downloading)}
                className="w-full"
              >
                {downloading
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> DOWNLOADING…</>
                  : <>
                      {metadata.type === 'album'
                        ? 'DOWNLOAD ALL'
                        : metadata.already_in_library
                          ? 'DOWNLOAD AGAIN'
                          : 'DOWNLOAD'}
                      <ArrowRight style={{ width: 13, height: 13 }} />
                    </>}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
