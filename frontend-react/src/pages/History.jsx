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
import { cn } from '@/lib/utils';
import { ease } from '@/lib/motion';

const STATUS_CONFIG = {
  success: {
    icon: CheckCircle2,
    label: 'Downloaded',
    accent: 'var(--accent-emerald)',
    dim: 'var(--accent-emerald-dim)',
    badge: 'badge-emerald',
  },
  skipped: {
    icon: SkipForward,
    label: 'Skipped',
    accent: 'var(--accent-slate)',
    dim: 'var(--accent-slate-dim)',
    badge: 'badge-slate',
  },
  failed: {
    icon: XCircle,
    label: 'Failed',
    accent: 'var(--accent-rose)',
    dim: 'var(--accent-rose-dim)',
    badge: 'badge-rose',
  },
  fallback: {
    icon: RotateCw,
    label: 'Fallback',
    accent: 'var(--accent-amber)',
    dim: 'var(--accent-amber-dim)',
    badge: 'badge-amber',
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
        {/* Search */}
        <div className="search-wrap" style={{ maxWidth: 'none' }}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter by title or artist…"
            className="tb-search"
            style={{ cursor: 'text', height: 22 }}
          />
          {search && (
            <button onClick={() => setSearch('')} className="flex-shrink-0" style={{ color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}>
              <X style={{ width: 12, height: 12 }} />
            </button>
          )}
        </div>

        {/* Status tabs */}
        <div className="tab-row">
          {FILTERS.map(f => {
            const isActive = filter === f.value;
            return (
              <button
                key={f.value}
                onClick={() => setFilter(f.value)}
                className={cn('tab-btn', isActive && 'on')}
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
        className="glass-card"
      >
        <div className="tbl-hdr">
          <span style={{ width: 32, flexShrink: 0 }} />
          <span className="flex-1">Track</span>
          <span style={{ minWidth: 52, textAlign: 'right' }}>Time</span>
          <span>Status</span>
        </div>
        <ScrollArea className="max-h-[calc(100vh-340px)]">
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
                    className="tbl-row"
                  >
                    {/* Status icon thumb */}
                    <div className="tbl-thumb" style={{ width: 32, height: 32, background: `linear-gradient(135deg, ${cfg.accent}33, ${cfg.accent}10)` }}>
                      <StatusIcon className="w-4 h-4" style={{ color: cfg.accent }} />
                    </div>

                    {/* Content */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <p className="tbl-name truncate">
                        {item.title}
                      </p>
                      {(item.artist || (item.status === 'failed' && item.error)) && (
                        <p className="truncate" style={{
                          fontSize: 11, marginTop: 1,
                          color: item.status === 'failed' && item.error ? 'var(--accent-rose)' : 'var(--text-tertiary)',
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
                          width: 24, height: 24, border: 'none', borderRadius: 100,
                          background: isPlaying ? 'var(--accent-violet-dim)' : 'transparent',
                          color: 'var(--accent-violet)', cursor: 'pointer',
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          flexShrink: 0,
                        }}
                      >
                        {isPlaying ? <Pause style={{ width: 11, height: 11 }} /> : <Play style={{ width: 11, height: 11 }} />}
                      </button>
                    )}

                    {/* Timestamp */}
                    <span className="text-mono flex-shrink-0" style={{ minWidth: 52, textAlign: 'right', color: 'var(--text-muted)', fontSize: 10 }}>
                      {item.timestamp}
                    </span>

                    {/* Status chip */}
                    <span className={cn('tbl-badge', cfg.badge)}>
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
