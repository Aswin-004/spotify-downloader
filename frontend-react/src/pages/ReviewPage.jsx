import { useState, useEffect } from 'react';
import { usePlayer } from '@/context/PlayerContext';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2, RotateCw, Loader2, Music, PlayCircle,
  Zap, SkipForward, Trash2, FolderInput, ChevronRight,
  Play, Pause, Volume2, Music2, Copy, X,
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
  const [bpmInputs,       setBpmInputs]       = useState({});
  const [bpmSaving,       setBpmSaving]       = useState({});
  const [duplicates,      setDuplicates]      = useState([]);
  const [dupGenres,       setDupGenres]       = useState({});
  const [dupDeleting,     setDupDeleting]     = useState({});
  const [dupKeeping,      setDupKeeping]      = useState({});
  const { nowPlaying, playing, toggle, playTrack } = usePlayer();

  useEffect(() => {
    api.getGeminiQuota().then(setGeminiQuota).catch(() => {});
    api.getSkippedTracks().then(setSkippedTracks).catch(() => {});
    api.getDuplicates().then(d => setDuplicates(d.duplicates || [])).catch(() => {});
  }, []);

  async function handleDeleteDuplicate(dup) {
    setDupDeleting(p => ({ ...p, [dup.filename]: true }));
    try {
      await api.deleteDuplicate(dup.filename);
      setDuplicates(prev => prev.filter(d => d.filename !== dup.filename));
      addToast({ type: 'success', title: 'Deleted', description: dup.title, duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Delete failed', description: err.message, duration: 4000 });
    } finally { setDupDeleting(p => ({ ...p, [dup.filename]: false })); }
  }

  async function handleKeepDuplicate(dup) {
    const genre = dupGenres[dup.filename];
    if (!genre) return;
    setDupKeeping(p => ({ ...p, [dup.filename]: true }));
    try {
      const result = await api.keepDuplicate(dup.filename, genre);
      if (result.moved) {
        setDuplicates(prev => prev.filter(d => d.filename !== dup.filename));
        addToast({ type: 'success', title: `Moved to ${result.destination}`, description: dup.title, duration: 5000 });
      }
    } catch (err) {
      addToast({ type: 'error', title: 'Move failed', description: err.message, duration: 4000 });
    } finally { setDupKeeping(p => ({ ...p, [dup.filename]: false })); }
  }

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
      if (result.moved || result.confirmed) {
        addToast({ type: 'success', title: result.moved ? 'Reclassified' : 'Confirmed', description: `${item.title} → ${result.new_folder}`, duration: 5000 });
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
        if (result.moved || result.confirmed) { clearNeedsReviewItem(items[i].title); processed++; }
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

  async function handleSaveBpm(item) {
    const key = item.id || (item.title + '__' + item.artist);
    const bpm = parseFloat(bpmInputs[key]);
    if (!bpm || bpm <= 0) return;
    setBpmSaving(p => ({ ...p, [key]: true }));
    try {
      await api.updateTrackBpm(item.filename, bpm);
      addToast({ type: 'success', title: `BPM set to ${bpm}`, description: item.title, duration: 4000 });
    } catch (err) {
      addToast({ type: 'error', title: 'BPM update failed', description: err.message, duration: 4000 });
    } finally { setBpmSaving(p => ({ ...p, [key]: false })); }
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
          <div style={{ border: '1px solid var(--border-subtle)', borderRadius: 4, overflow: 'hidden' }}>
            {/* Header */}
            <div style={{
              padding: '8px 12px', borderBottom: '1px solid var(--border-subtle)',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            }}>
              <span style={{ fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace', color: 'var(--text-secondary)' }}>
                PENDING
              </span>
              <span style={{ fontSize: 11, fontFamily: 'monospace', fontVariantNumeric: 'tabular-nums', color: 'var(--accent-amber)' }}>
                {needsReviewItems.length}
              </span>
            </div>
            <div className="overflow-y-auto scrollbar-thin" style={{ maxHeight: 480 }}>
              <AnimatePresence>
                {needsReviewItems.map((item, i) => {
                  const isSelected = i === selectedIdx;
                  return (
                    <motion.button
                      key={item.id || item.title + '__' + item.artist}
                      initial={{ opacity: 0, x: -8 }}
                      animate={{ opacity: 1, x: 0  }}
                      exit={{ opacity: 0, x: 8     }}
                      transition={{ ...ease, delay: i * 0.02 }}
                      onClick={() => setSelectedIdx(i)}
                      style={{
                        width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                        padding: '8px 12px 8px 0', paddingLeft: 12,
                        background: isSelected ? 'rgba(245,158,11,0.06)' : 'transparent',
                        borderBottom: '1px solid var(--border-subtle)',
                        borderLeft: `3px solid ${isSelected ? 'var(--accent-amber)' : 'transparent'}`,
                        border: 'none', cursor: 'pointer', textAlign: 'left',
                        transition: 'background 0.1s, border-color 0.1s',
                      }}
                      onMouseEnter={e => { if (!isSelected) e.currentTarget.style.background = 'var(--surface-0)'; }}
                      onMouseLeave={e => { if (!isSelected) e.currentTarget.style.background = 'transparent'; }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p style={{ fontSize: 12, fontWeight: 500, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.title}
                        </p>
                        <p style={{ fontSize: 10, color: 'var(--text-tertiary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {item.artist || 'Unknown Artist'}
                        </p>
                      </div>
                      <ChevronRight style={{ width: 12, height: 12, flexShrink: 0, color: isSelected ? 'var(--accent-amber)' : 'var(--text-muted)' }} />
                    </motion.button>
                  );
                })}
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
                style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)', borderRadius: 4, padding: 20, display: 'flex', flexDirection: 'column', gap: 18 }}
              >
                {/* Track info */}
                <div className="flex items-start gap-4">
                  <button
                    onClick={() => handleTogglePlay(selectedItem)}
                    disabled={!selectedItem.filename}
                    className="w-16 h-16 rounded-xl flex items-center justify-center flex-shrink-0 cursor-pointer transition-all"
                    style={{ background: 'var(--accent-amber-dim)' }}
                    title={nowPlaying?.filename === selectedItem?.filename && playing ? 'Pause preview' : 'Play preview'}
                  >
                    {nowPlaying?.filename === selectedItem?.filename && playing
                      ? <Pause className="w-8 h-8" style={{ color: 'var(--accent-amber)' }} />
                      : <Play  className="w-8 h-8" style={{ color: 'var(--accent-amber)' }} />}
                  </button>
                  <div className="min-w-0 flex-1">
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
                    {/* BPM + Camelot badges */}
                    {(selectedItem.bpm || selectedItem.camelot_key) && (
                      <div className="flex items-center gap-1.5 mt-2">
                        {selectedItem.bpm && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-11 font-mono font-semibold"
                                style={{ background: 'var(--accent-amber-dim)', color: 'var(--accent-amber)' }}>
                            {selectedItem.bpm} BPM
                          </span>
                        )}
                        {selectedItem.camelot_key && (
                          <span className="inline-flex items-center px-2 py-0.5 rounded text-11 font-mono font-semibold"
                                style={{ background: 'var(--accent-teal-dim, rgba(20,184,166,0.15))', color: 'var(--accent-teal, #14b8a6)' }}>
                            {selectedItem.camelot_key}
                          </span>
                        )}
                      </div>
                    )}
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

                {/* BPM correction */}
                <div className="space-y-2">
                  <p className="text-label flex items-center gap-1.5">
                    <Music2 className="w-3 h-3" style={{ color: 'var(--accent-violet)' }} />
                    BPM Correction
                  </p>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min="40" max="220" step="1"
                      placeholder="e.g. 140"
                      value={bpmInputs[selectedKey] || ''}
                      onChange={e => setBpmInputs(p => ({ ...p, [selectedKey]: e.target.value }))}
                      className="w-28 text-13 rounded-lg px-3 py-2 outline-none transition-all"
                      style={{
                        background: 'var(--surface-1)',
                        color: 'var(--text-primary)',
                        border: '1px solid var(--border-default)',
                      }}
                    />
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={!bpmInputs[selectedKey] || bpmSaving[selectedKey]}
                      onClick={() => handleSaveBpm(selectedItem)}
                    >
                      {bpmSaving[selectedKey]
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : 'Set BPM'}
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

      {/* Duplicates panel */}
      <AnimatePresence>
        {duplicates.length > 0 && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={ease}
            className="rounded-xl overflow-hidden"
            style={{ background: 'var(--surface-0)', border: '1px solid rgba(6,182,212,0.2)' }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3"
                 style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <div className="flex items-center gap-2">
                <Copy className="w-4 h-4" style={{ color: 'var(--accent-cyan)' }} />
                <span className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>
                  Duplicate Files
                </span>
                <span className="text-11 font-mono px-2 py-0.5 rounded-full"
                      style={{ background: 'var(--accent-cyan-dim)', color: 'var(--accent-cyan)' }}>
                  {duplicates.length}
                </span>
              </div>
              <p className="text-11" style={{ color: 'var(--text-muted)' }}>
                Keep (move to library) or delete
              </p>
            </div>

            {/* Rows */}
            <div className="divide-y" style={{ '--tw-divide-opacity': 1 }}>
              {duplicates.map(dup => (
                <motion.div
                  key={dup.filename}
                  layout
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.18 }}
                  className="flex items-center gap-3 px-4 py-3"
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-1)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  {/* Icon */}
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                       style={{ background: 'var(--accent-cyan-dim)' }}>
                    <Music className="w-3.5 h-3.5" style={{ color: 'var(--accent-cyan)' }} />
                  </div>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <p className="text-13 font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                      {dup.title}
                    </p>
                    <p className="text-10 truncate font-mono" style={{ color: 'var(--text-muted)' }}>
                      {dup.artist && <span className="mr-2">{dup.artist}</span>}
                      {dup.filename}
                    </p>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <select
                      value={dupGenres[dup.filename] || ''}
                      onChange={e => setDupGenres(p => ({ ...p, [dup.filename]: e.target.value }))}
                      className="text-11 rounded-lg px-2 py-1.5 cursor-pointer outline-none"
                      style={{
                        background: 'var(--surface-2)',
                        border: '1px solid var(--border-default)',
                        color: 'var(--text-secondary)',
                      }}
                    >
                      <option value="">Move to…</option>
                      {GENRE_OPTIONS.map(g => <option key={g} value={g}>{g}</option>)}
                    </select>

                    <Button
                      size="sm"
                      disabled={!dupGenres[dup.filename] || dupKeeping[dup.filename]}
                      onClick={() => handleKeepDuplicate(dup)}
                      title="Move to library"
                    >
                      {dupKeeping[dup.filename]
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <><FolderInput className="w-3.5 h-3.5" />Keep</>}
                    </Button>

                    <button
                      onClick={() => handleDeleteDuplicate(dup)}
                      disabled={dupDeleting[dup.filename]}
                      title="Delete permanently"
                      className="w-7 h-7 flex items-center justify-center rounded-lg cursor-pointer transition-colors focus-ring"
                      style={{ color: 'var(--text-muted)' }}
                      onMouseEnter={e => {
                        e.currentTarget.style.background = 'rgba(244,63,94,0.12)';
                        e.currentTarget.style.color = 'var(--accent-rose)';
                      }}
                      onMouseLeave={e => {
                        e.currentTarget.style.background = 'transparent';
                        e.currentTarget.style.color = 'var(--text-muted)';
                      }}
                    >
                      {dupDeleting[dup.filename]
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Trash2 className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

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
