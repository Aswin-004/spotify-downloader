import { useEffect, useState, useMemo } from 'react';
import { usePlayer } from '@/context/PlayerContext';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Trash2, CheckCircle2, XCircle, SkipForward, X,
  RotateCw, History as HistoryIcon, Play, Pause,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';

const STATUS_CONFIG = {
  success: {
    icon: CheckCircle2,
    label: 'Downloaded',
    accent: 'var(--accent-emerald)',
    dim: 'var(--accent-emerald-dim)',
  },
  skipped: {
    icon: SkipForward,
    label: 'Skipped',
    accent: 'var(--accent-slate)',
    dim: 'var(--accent-slate-dim)',
  },
  failed: {
    icon: XCircle,
    label: 'Failed',
    accent: 'var(--accent-rose)',
    dim: 'var(--accent-rose-dim)',
  },
  fallback: {
    icon: RotateCw,
    label: 'Fallback',
    accent: 'var(--accent-amber)',
    dim: 'var(--accent-amber-dim)',
  },
};

const FILTERS = [
  { value: 'all',     label: 'All'        },
  { value: 'success', label: 'Downloaded' },
  { value: 'failed',  label: 'Failed'     },
  { value: 'skipped', label: 'Skipped'    },
];

function friendlyError(raw) {
  if (!raw) return null;
  const s = raw.toLowerCase();
  if (s.includes('no youtube match') || s.includes('no match') || s.includes('no strict match'))
    return "Couldn't find this track on YouTube";
  if (s.includes('duration mismatch') || s.includes('duration'))
    return 'YouTube version has a different length';
  if (s.includes('timeout') || s.includes('timed out'))
    return 'Download took too long — try again';
  if (s.includes('ffmpeg') || s.includes('conversion'))
    return 'Audio conversion failed';
  if (s.includes('file missing'))
    return 'Download failed — file not saved';
  if (s.includes('move failed'))
    return 'Could not move file to music folder';
  return raw.length > 80 ? raw.slice(0, 80) + '…' : raw;
}

export default function History() {
  const { history: socketHistory } = useSocket();
  const [history, setHistory] = useState([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [confirmClear, setConfirmClear] = useState(false);
  const { nowPlaying, playing, toggle, playTrack } = usePlayer();

  function handleTogglePlay(item) {
    if (!item?.filename) return;
    if (nowPlaying?.filename === item.filename) { toggle(); return; }
    playTrack({
      title:    item.title    || item.filename,
      artist:   item.artist   || '',
      bpm:      item.bpm      ?? null,
      camelot:  item.camelot_key || null,
      audioUrl: api.previewTrackUrl(item.filename),
      filename: item.filename,
      path:     item.filename,
    });
  }

  useEffect(() => {
    if (!confirmClear) return;
    const t = setTimeout(() => setConfirmClear(false), 4000);
    return () => clearTimeout(t);
  }, [confirmClear]);

  useEffect(() => {
    api.getHistory().then(data => {
      if (data.history) setHistory(data.history);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!socketHistory.length) return;
    setHistory(prev => {
      const existingKeys = new Set(prev.map(h => `${h.title}|${h.timestamp}`));
      const newEntries = socketHistory.filter(h => !existingKeys.has(`${h.title}|${h.timestamp}`));
      return newEntries.length ? [...newEntries, ...prev] : prev;
    });
  }, [socketHistory]);

  const filtered = useMemo(() => {
    let items = history;
    if (filter !== 'all') items = items.filter(h => h.status === filter);
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(h =>
        (h.title || '').toLowerCase().includes(q) ||
        (h.artist || '').toLowerCase().includes(q)
      );
    }
    return items;
  }, [history, search, filter]);

  async function handleClear() {
    if (!confirmClear) { setConfirmClear(true); return; }
    setConfirmClear(false);
    try {
      await api.clearHistory();
      setHistory([]);
    } catch {}
  }

  return (
    <div className="max-w-4xl mx-auto space-y-5 pb-10">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center"
               style={{ background: 'var(--accent-violet-dim)' }}>
            <HistoryIcon className="w-4.5 h-4.5" style={{ color: 'var(--accent-violet)' }} />
          </div>
          <div>
            <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
              Download History
            </h1>
            <p className="text-11 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
              {filtered.length !== history.length
                ? `${filtered.length} of ${history.length}`
                : `${history.length}`} entries
            </p>
          </div>
        </div>

        <Button
          variant="destructive"
          size="sm"
          onClick={handleClear}
          className={confirmClear ? 'animate-pulse' : ''}
          title={confirmClear ? 'Click again to confirm' : 'Clear all history'}
        >
          <Trash2 className="w-3.5 h-3.5" />
          {confirmClear ? 'Confirm?' : 'Clear'}
        </Button>
      </motion.div>

      {/* Filters */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...ease, delay: 0.08 }}
        style={{ display: 'flex', flexDirection: 'column', gap: 10 }}
      >
        {/* Terminal search */}
        <div
          style={{
            display: 'flex', alignItems: 'center',
            border: '1px solid var(--border-default)',
            borderRadius: 4, background: 'var(--surface-0)',
            transition: 'border-color 0.15s, box-shadow 0.15s',
          }}
          onFocusCapture={e => {
            e.currentTarget.style.borderColor = 'var(--accent-cyan)';
            e.currentTarget.style.boxShadow = '0 0 0 1px var(--accent-cyan)';
          }}
          onBlurCapture={e => {
            e.currentTarget.style.borderColor = 'var(--border-default)';
            e.currentTarget.style.boxShadow = 'none';
          }}
        >
          <span style={{
            paddingLeft: 12, paddingRight: 6, flexShrink: 0,
            color: 'var(--accent-cyan)', fontSize: 11, fontWeight: 700,
            letterSpacing: '0.04em', userSelect: 'none',
            fontFamily: 'ui-monospace, monospace',
          }}>filter:</span>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="title or artist…"
            style={{
              flex: 1, height: 38, background: 'transparent', border: 'none',
              outline: 'none', color: 'var(--text-primary)',
              fontSize: 13, fontFamily: 'ui-monospace, monospace',
            }}
          />
          {search && (
            <button onClick={() => setSearch('')}
              style={{ padding: '0 10px', color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}>
              <X style={{ width: 12, height: 12 }} />
            </button>
          )}
        </div>

        {/* Flat underline status tabs */}
        <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border-subtle)' }}>
          {FILTERS.map(f => {
            const isActive = filter === f.value;
            const accent = f.value === 'all' ? 'var(--accent-violet)'
              : f.value === 'success' ? 'var(--accent-emerald)'
              : f.value === 'failed'  ? 'var(--accent-rose)'
              : 'var(--accent-slate)';
            return (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                style={{
                  padding: '6px 14px', border: 'none', cursor: 'pointer',
                  background: 'transparent',
                  fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
                  textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace',
                  color: isActive ? accent : 'var(--text-muted)',
                  borderBottom: isActive ? `2px solid ${accent}` : '2px solid transparent',
                  marginBottom: -1, transition: 'color 0.15s, border-color 0.15s',
                }}
                onMouseEnter={e => { if (!isActive) e.currentTarget.style.color = 'var(--text-secondary)'; }}
                onMouseLeave={e => { if (!isActive) e.currentTarget.style.color = 'var(--text-muted)'; }}
              >
                {f.label}
              </button>
            );
          })}
        </div>
      </motion.div>

      {/* History log */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ ...ease, delay: 0.12 }}
        style={{ border: '1px solid var(--border-subtle)', borderRadius: 4, overflow: 'hidden' }}
      >
        <ScrollArea className="max-h-[calc(100vh-300px)]">
          {filtered.length === 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '56px 0' }}>
              <HistoryIcon style={{ width: 20, height: 20, color: 'var(--text-muted)', marginBottom: 10 }} />
              <p style={{ fontSize: 13, color: 'var(--text-tertiary)', fontWeight: 500 }}>No entries found</p>
              <p style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                {search || filter !== 'all' ? 'Try adjusting your filters' : 'Downloads appear here once complete'}
              </p>
            </div>
          ) : (
            <AnimatePresence initial={false}>
              {filtered.map((item, i) => {
                const cfg = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.failed;
                const StatusIcon = cfg.icon;
                const isPlaying = nowPlaying?.filename === item.filename && playing;

                return (
                  <motion.div
                    key={`${item.title}-${item.timestamp}-${i}`}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(i * 0.012, 0.25), duration: 0.15 }}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      padding: '7px 12px 7px 0',
                      borderTop: i > 0 ? '1px solid var(--border-subtle)' : 'none',
                      borderLeft: `3px solid ${cfg.accent}`,
                      paddingLeft: 12, transition: 'background 0.1s',
                    }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-0)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    {/* Timestamp */}
                    <span style={{
                      fontSize: 9, fontFamily: 'monospace', color: 'var(--text-muted)',
                      fontVariantNumeric: 'tabular-nums', flexShrink: 0,
                      minWidth: 52, letterSpacing: '0.02em',
                    }}>
                      {item.timestamp}
                    </span>

                    {/* Content */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p style={{
                        fontSize: 12, fontWeight: 500, color: 'var(--text-primary)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {item.title}
                      </p>
                      {(item.artist || (item.status === 'failed' && item.error)) && (
                        <p style={{
                          fontSize: 10, marginTop: 1,
                          color: item.status === 'failed' && item.error ? 'var(--accent-rose)' : 'var(--text-tertiary)',
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {item.status === 'failed' && item.error ? friendlyError(item.error) : item.artist}
                        </p>
                      )}
                    </div>

                    {/* Play button */}
                    {item.status === 'success' && item.filename && (
                      <button
                        onClick={() => handleTogglePlay(item)}
                        title={isPlaying ? 'Pause' : 'Preview'}
                        style={{
                          width: 22, height: 22, border: 'none', borderRadius: 4,
                          background: isPlaying ? 'var(--accent-violet-dim)' : 'transparent',
                          color: 'var(--accent-violet)', cursor: 'pointer',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          flexShrink: 0,
                        }}
                      >
                        {isPlaying ? <Pause style={{ width: 10, height: 10 }} /> : <Play style={{ width: 10, height: 10 }} />}
                      </button>
                    )}

                    {/* Status chip */}
                    <span style={{
                      fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
                      textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace',
                      padding: '2px 6px', borderRadius: 2, flexShrink: 0,
                      background: `${cfg.accent}18`, color: cfg.accent,
                    }}>
                      {cfg.label}
                    </span>
                  </motion.div>
                );
              })}
            </AnimatePresence>
          )}
        </ScrollArea>
      </motion.div>
    </div>
  );
}
