import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2, RotateCw, Loader2, Music, PlayCircle,
  Zap, SkipForward, Trash2, FolderInput, ChevronRight,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import ConfidenceBar from '@/components/ConfidenceBar';
import { cn } from '@/lib/utils';
import { ease, fadeUp } from '@/lib/motion';

const GENRE_OPTIONS = [
  'House', 'Trance', 'UK Garage', 'Drum & Bass', 'Dubstep',
  'Techno', 'Grime', 'Electronic',
  'Bollywood', 'Punjabi', 'Tamil',
  'Hip Hop', 'R&B', 'Pop', 'Latin',
];

export default function ReviewPage() {
  const { needsReviewItems, clearNeedsReviewItem } = useSocket();
  const { addToast } = useToast();
  const [selectedIdx,     setSelectedIdx]     = useState(0);
  const [retrying,        setRetrying]        = useState({});
  const [retryAllProgress,setRetryAllProgress]= useState(null);
  const [geminiQuota,     setGeminiQuota]     = useState(null);
  const [skippedTracks,   setSkippedTracks]   = useState(null);
  const [resettingSkipped,setResettingSkipped]= useState(false);
  const [selectedGenres,  setSelectedGenres]  = useState({});
  const [moving,          setMoving]          = useState({});

  useEffect(() => {
    api.getGeminiQuota().then(setGeminiQuota).catch(() => {});
    api.getSkippedTracks().then(setSkippedTracks).catch(() => {});
  }, []);

  // Keep selectedIdx in bounds
  useEffect(() => {
    if (selectedIdx >= needsReviewItems.length && needsReviewItems.length > 0)
      setSelectedIdx(needsReviewItems.length - 1);
  }, [needsReviewItems.length]);

  async function handleResetSkipped(trackId = null) {
    setResettingSkipped(true);
    try {
      await api.resetSkippedTracks(trackId);
      setSkippedTracks(await api.getSkippedTracks());
      addToast({ type: 'success', title: trackId ? 'Track unblocked' : 'All skipped tracks reset', duration: 4000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Reset failed', description: err.message, duration: 4000 });
    } finally { setResettingSkipped(false); }
  }

  function buildFilepath(item) {
    const folder = item.suggested_folder || 'Library/Electronic';
    if (item.filename) return `${folder}/${item.filename}`;
    const artist = item.artist || '';
    return artist ? `${folder}/${item.title} - ${artist}.mp3` : `${folder}/${item.title}.mp3`;
  }

  async function handleRetry(item) {
    const key = item.title;
    setRetrying(p => ({ ...p, [key]: true }));
    try {
      const result = await api.retagCatchallTrack(buildFilepath(item));
      if (result.moved) {
        addToast({ type: 'success', title: 'Reclassified', description: `${item.title} → ${result.new_folder}`, duration: 5000 });
        clearNeedsReviewItem(item.title);
      } else if (result.quota_exhausted) {
        addToast({ type: 'error', title: 'AI quota exhausted', description: 'Retried tomorrow automatically.', duration: 8000 });
      } else {
        addToast({ type: 'warning', title: 'Still unresolved', description: result.reason || 'Could not classify', duration: 5000 });
      }
    } catch (err) {
      addToast({ type: 'error', title: 'Retry failed', description: err.message, duration: 4000 });
    } finally { setRetrying(p => ({ ...p, [key]: false })); }
  }

  async function handleRetryAll() {
    const items = [...needsReviewItems];
    setRetryAllProgress({ current: 0, total: items.length });
    let processed = 0, unresolved = 0, notFound = 0;
    for (let i = 0; i < items.length; i++) {
      setRetryAllProgress({ current: i + 1, total: items.length });
      try {
        const result = await api.retagCatchallTrack(buildFilepath(items[i]));
        if (result.moved) { clearNeedsReviewItem(items[i].title); processed++; }
        else if (result.quota_exhausted) { addToast({ type: 'error', title: 'AI quota hit mid-run', description: `${processed} moved so far.`, duration: 8000 }); break; }
        else { unresolved++; }
      } catch (err) {
        if (err?.status === 404) notFound++;
      }
      await new Promise(r => setTimeout(r, 2000));
    }
    setRetryAllProgress(null);
    api.getGeminiQuota().then(setGeminiQuota).catch(() => {});
    if (processed > 0) addToast({ type: 'success', title: `${processed} track${processed !== 1 ? 's' : ''} reclassified`, description: unresolved > 0 ? `${unresolved} still unresolved` : undefined, duration: 6000 });
    else addToast({ type: 'warning', title: 'Nothing reclassified', description: 'Try again later or move manually.', duration: 8000 });
  }

  async function handleMoveAndRemember(item) {
    const itemKey = item.id || (item.title + '__' + item.artist);
    const genre = selectedGenres[itemKey];
    if (!genre) return;
    setMoving(p => ({ ...p, [itemKey]: true }));
    try {
      const result = await api.moveAndRemember(buildFilepath(item), genre, item.artist || '');
      if (result.moved) {
        addToast({ type: 'success', title: `Moved to ${genre}`, description: item.artist ? `${item.artist} will auto-route to ${genre}` : item.title, duration: 5000 });
        clearNeedsReviewItem(item.title);
      } else {
        addToast({ type: 'error', title: 'Move failed', description: result.error, duration: 5000 });
      }
    } catch (err) {
      addToast({ type: 'error', title: 'Move failed', description: err.message, duration: 4000 });
    } finally { setMoving(p => ({ ...p, [itemKey]: false })); }
  }

  const selectedItem = needsReviewItems[selectedIdx] ?? null;
  const selectedKey  = selectedItem ? (selectedItem.id || selectedItem.title + '__' + selectedItem.artist) : null;
  const conf         = selectedItem ? Math.round((selectedItem.confidence || 0) * 100) : 0;

  return (
    <div className="max-w-5xl mx-auto space-y-5">

      {/* Page header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Review Queue
          </h1>
          <p className="text-13 mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            Tracks awaiting genre classification
          </p>
        </div>
        <div className="flex items-center gap-2">
          {geminiQuota && (
            <Badge variant={geminiQuota.exhausted ? 'danger' : 'ai'} className="gap-1">
              <Zap className="w-3 h-3" />
              {geminiQuota.exhausted ? 'AI paused' : 'AI ready'}
            </Badge>
          )}
          {needsReviewItems.length > 0 && (
            <Badge variant="warning">{needsReviewItems.length} pending</Badge>
          )}
          {needsReviewItems.length > 1 && (
            <Button size="sm" disabled={!!retryAllProgress} onClick={handleRetryAll}>
              {retryAllProgress ? (
                <><Loader2 className="w-3.5 h-3.5 animate-spin" />{retryAllProgress.current}/{retryAllProgress.total}</>
              ) : (
                <><PlayCircle className="w-3.5 h-3.5" />Retry All</>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Retry All progress bar — shown independently of item count */}
      <AnimatePresence>
        {retryAllProgress && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="rounded-xl p-3 space-y-2 overflow-hidden"
            style={{ background: 'var(--accent-amber-dim)', border: '1px solid rgba(245,158,11,0.2)' }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--accent-amber)' }} />
                <span className="text-13 font-medium" style={{ color: 'var(--accent-amber)' }}>
                  Retrying All Tracks
                </span>
              </div>
              <span className="text-11 font-mono tabular-nums" style={{ color: 'var(--text-tertiary)' }}>
                {retryAllProgress.current} / {retryAllProgress.total}
              </span>
            </div>
            <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
              <motion.div
                className="h-full rounded-full"
                style={{ background: 'var(--accent-amber)' }}
                animate={{ width: `${Math.round((retryAllProgress.current / retryAllProgress.total) * 100)}%` }}
                transition={{ duration: 0.4, ease: 'easeOut' }}
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Empty state */}
      {needsReviewItems.length === 0 && !retryAllProgress && (
        <motion.div
          {...fadeUp}
          className="flex flex-col items-center justify-center py-20 rounded-2xl text-center"
          style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
        >
          <div className="w-12 h-12 rounded-2xl flex items-center justify-center mb-4"
               style={{ background: 'var(--accent-emerald-dim)' }}>
            <CheckCircle2 className="w-6 h-6" style={{ color: 'var(--accent-emerald)' }} />
          </div>
          <p className="text-16 font-semibold" style={{ color: 'var(--accent-emerald)' }}>All clear</p>
          <p className="text-13 mt-1" style={{ color: 'var(--text-tertiary)' }}>
            Every track has been placed in a genre folder
          </p>
        </motion.div>
      )}

      {/* Two-pane layout */}
      {needsReviewItems.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">

          {/* Left: track list */}
          <div className="rounded-xl overflow-hidden" style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}>
            <div className="px-4 py-3 text-label" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              Pending · {needsReviewItems.length}
            </div>
            <div className="overflow-y-auto scrollbar-thin" style={{ maxHeight: 480 }}>
              <AnimatePresence>
                {needsReviewItems.map((item, i) => (
                  <motion.button
                    key={item.id || item.title + '__' + item.artist}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0  }}
                    exit={{ opacity: 0, x: 8     }}
                    transition={{ ...ease, delay: i * 0.02 }}
                    onClick={() => setSelectedIdx(i)}
                    className="w-full flex items-center gap-3 px-4 py-2.5 text-left cursor-pointer transition-all"
                    style={{
                      background: i === selectedIdx ? 'var(--accent-amber-dim)' : 'transparent',
                      borderBottom: '1px solid var(--border-subtle)',
                      borderLeft: i === selectedIdx ? '2px solid var(--accent-amber)' : '2px solid transparent',
                    }}
                  >
                    <div className="w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0"
                         style={{ background: 'var(--accent-amber-dim)' }}>
                      <Music className="w-3.5 h-3.5" style={{ color: 'var(--accent-amber)' }} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-12 font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                        {item.title}
                      </p>
                      <p className="text-11 truncate" style={{ color: 'var(--text-tertiary)' }}>
                        {item.artist || 'Unknown Artist'}
                      </p>
                    </div>
                    <ChevronRight className="w-3.5 h-3.5 flex-shrink-0"
                                  style={{ color: i === selectedIdx ? 'var(--accent-amber)' : 'var(--text-muted)' }} />
                  </motion.button>
                ))}
              </AnimatePresence>
            </div>
          </div>

          {/* Right: classification panel */}
          <AnimatePresence mode="wait">
            {selectedItem && (
              <motion.div
                key={selectedKey}
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0  }}
                exit={{ opacity: 0, x: -16   }}
                transition={ease}
                className="rounded-xl p-5 space-y-5"
                style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
              >
                {/* Track info */}
                <div className="flex items-start gap-4">
                  <div className="w-16 h-16 rounded-xl flex items-center justify-center flex-shrink-0"
                       style={{ background: 'var(--accent-amber-dim)' }}>
                    <Music className="w-8 h-8" style={{ color: 'var(--accent-amber)' }} />
                  </div>
                  <div className="min-w-0">
                    <h2 className="font-display text-18 font-semibold truncate"
                        style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
                      {selectedItem.title}
                    </h2>
                    <p className="text-13 truncate mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                      {selectedItem.artist || 'Unknown Artist'}
                    </p>
                    <p className="text-11 mt-1" style={{ color: 'var(--text-tertiary)' }}>
                      {selectedItem.suggested_folder}
                    </p>
                  </div>
                </div>

                {/* AI confidence */}
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-label">
                    <Zap className="w-3 h-3" style={{ color: 'var(--accent-violet)' }} />
                    Groq AI Classification
                    <div className="w-1.5 h-1.5 rounded-full animate-pulse ml-1"
                         style={{ background: 'var(--accent-violet)' }} />
                  </div>
                  <div className="rounded-lg p-3 space-y-2"
                       style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                    {selectedItem.confidence != null ? (
                      <ConfidenceBar
                        pct={conf}
                        label={selectedItem.suggested_folder?.split('/').pop() || 'Unknown'}
                      />
                    ) : (
                      <p className="text-12" style={{ color: 'var(--text-tertiary)' }}>
                        No AI classification available
                      </p>
                    )}
                  </div>
                </div>

                {/* Manual genre override */}
                <div className="space-y-2">
                  <p className="text-label">Manual Override</p>
                  <div className="flex items-center gap-2">
                    <select
                      value={selectedGenres[selectedKey] || ''}
                      onChange={e => setSelectedGenres(p => ({ ...p, [selectedKey]: e.target.value }))}
                      className="flex-1 text-13 rounded-lg px-3 py-2 cursor-pointer outline-none transition-all"
                      style={{
                        background: 'var(--surface-1)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-default)',
                      }}
                    >
                      <option value="">Pick genre to move…</option>
                      {GENRE_OPTIONS.map(g => <option key={g} value={g}>{g}</option>)}
                    </select>
                    <Button
                      size="sm"
                      disabled={!selectedGenres[selectedKey] || moving[selectedKey]}
                      onClick={() => handleMoveAndRemember(selectedItem)}
                    >
                      {moving[selectedKey]
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <><FolderInput className="w-3.5 h-3.5" />Move &amp; Remember</>}
                    </Button>
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex items-center gap-2 pt-2" style={{ borderTop: '1px solid var(--border-subtle)' }}>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={retrying[selectedItem.title]}
                    onClick={() => handleRetry(selectedItem)}
                    className="gap-1.5"
                  >
                    {retrying[selectedItem.title]
                      ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      : <RotateCw className="w-3.5 h-3.5" />}
                    Retry AI
                  </Button>
                  {selectedIdx < needsReviewItems.length - 1 && (
                    <Button variant="ghost" size="sm" onClick={() => setSelectedIdx(i => i + 1)}>
                      Skip →
                    </Button>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}

      {/* Skipped/blocked tracks panel */}
      {skippedTracks && skippedTracks.total > 0 && (
        <motion.div
          {...fadeUp}
          className="rounded-xl p-4 space-y-3"
          style={{ background: 'var(--surface-0)', border: '1px solid rgba(244,63,94,0.2)' }}
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <SkipForward className="w-4 h-4" style={{ color: 'var(--accent-rose)' }} />
              <span className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>
                Blocked Downloads
              </span>
              <Badge variant="danger">{skippedTracks.total}</Badge>
            </div>
            <Button variant="destructive" size="sm" disabled={resettingSkipped} onClick={() => handleResetSkipped()}>
              {resettingSkipped ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <><Trash2 className="w-3.5 h-3.5" />Reset All</>}
            </Button>
          </div>
          <p className="text-12" style={{ color: 'var(--text-tertiary)' }}>
            These tracks failed {skippedTracks.threshold}+ times. Reset to retry.
          </p>
          <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
            {Object.entries(skippedTracks.skipped).map(([id, count]) => (
              <div key={id} className="flex items-center justify-between gap-2 text-12 py-1"
                   style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                <span className="font-mono truncate" style={{ color: 'var(--text-secondary)' }}>{id}</span>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span style={{ color: 'var(--accent-rose)' }}>{count} fails</span>
                  <button onClick={() => handleResetSkipped(id)} disabled={resettingSkipped}
                          className="cursor-pointer focus-ring rounded" style={{ color: 'var(--text-muted)' }}
                          title="Unblock this track">
                    <RotateCw className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      )}
    </div>
  );
}
