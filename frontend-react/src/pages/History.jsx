import { useEffect, useState, useMemo } from 'react';
import { usePlayer } from '@/context/PlayerContext';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Search, Trash2, CheckCircle2, XCircle, SkipForward,
  RotateCw, History as HistoryIcon, Play, Pause,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
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
        className="flex flex-col sm:flex-row gap-3"
      >
        {/* Search */}
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none"
                  style={{ color: 'var(--text-muted)' }} />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search by title or artist..."
            className="pl-9"
          />
        </div>

        {/* Status filter pills */}
        <div className="flex gap-1 p-1 rounded-xl flex-shrink-0"
             style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
          {FILTERS.map(f => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className="px-3 py-1.5 rounded-lg text-12 font-medium transition-all duration-150 cursor-pointer focus-ring"
              style={filter === f.value
                ? { background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)' }
                : { color: 'var(--text-muted)' }
              }
              onMouseEnter={e => {
                if (filter !== f.value) e.currentTarget.style.color = 'var(--text-tertiary)';
              }}
              onMouseLeave={e => {
                if (filter !== f.value) e.currentTarget.style.color = 'var(--text-muted)';
              }}
            >
              {f.label}
            </button>
          ))}
        </div>
      </motion.div>

      {/* History list */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ ...ease, delay: 0.12 }}
        className="rounded-xl overflow-hidden"
        style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
      >
        <ScrollArea className="max-h-[calc(100vh-280px)]">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16">
              <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-3"
                   style={{ background: 'var(--surface-1)' }}>
                <HistoryIcon className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
              </div>
              <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>
                No history entries found
              </p>
              <p className="text-11 mt-1" style={{ color: 'var(--text-muted)' }}>
                {search || filter !== 'all' ? 'Try adjusting your filters' : 'Downloads appear here once complete'}
              </p>
            </div>
          ) : (
            <AnimatePresence initial={false}>
              {filtered.map((item, i) => {
                const cfg = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.failed;
                const StatusIcon = cfg.icon;

                return (
                  <motion.div
                    key={`${item.title}-${item.timestamp}-${i}`}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(i * 0.015, 0.3), duration: 0.18 }}
                    className="flex items-start gap-3 px-5 py-3 transition-colors duration-150"
                    style={{ borderTop: i > 0 ? '1px solid var(--border-subtle)' : 'none' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    {/* Status icon */}
                    <div className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 mt-0.5"
                         style={{ background: `${cfg.accent}18` }}>
                      <StatusIcon className="w-3 h-3" style={{ color: cfg.accent }} />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <p className="text-13 font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                        {item.title}
                      </p>
                      <p className="text-11 truncate" style={{ color: 'var(--text-tertiary)' }}>
                        {item.artist}
                      </p>
                      {item.status === 'failed' && item.error && (
                        <p className="text-10 truncate mt-0.5" style={{ color: 'var(--accent-rose)' }}>
                          {friendlyError(item.error)}
                        </p>
                      )}
                    </div>

                    {/* Play button — only for successful downloads with a filename */}
                    {item.status === 'success' && item.filename && (
                      <button
                        onClick={() => handleTogglePlay(item)}
                        title={nowPlaying?.filename === item.filename && playing ? 'Pause' : 'Preview'}
                        className="w-6 h-6 flex items-center justify-center rounded-lg flex-shrink-0 transition-colors"
                        style={{
                          background: nowPlaying?.filename === item.filename && playing
                            ? 'var(--accent-violet-dim)' : 'transparent',
                          color: 'var(--accent-violet)',
                        }}
                      >
                        {nowPlaying?.filename === item.filename && playing
                          ? <Pause className="w-3 h-3" />
                          : <Play  className="w-3 h-3" />}
                      </button>
                    )}

                    {/* Status badge */}
                    <span
                      className="text-10 font-semibold px-2 py-0.5 rounded-full flex-shrink-0"
                      style={{ background: cfg.dim, color: cfg.accent, letterSpacing: '0.04em' }}
                    >
                      {cfg.label.toUpperCase()}
                    </span>

                    {/* Timestamp */}
                    <span className="text-10 font-mono flex-shrink-0 mt-0.5 min-w-[56px] text-right"
                          style={{ color: 'var(--text-muted)' }}>
                      {item.timestamp}
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
