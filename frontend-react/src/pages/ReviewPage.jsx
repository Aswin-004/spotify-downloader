import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2,
  RotateCw,
  Loader2,
  Music,
  PlayCircle,
  Zap,
  SkipForward,
  Trash2,
  FolderInput,
} from 'lucide-react';

import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const GENRE_OPTIONS = [
  'House', 'Trance', 'UK Garage', 'Drum & Bass', 'Dubstep',
  'Techno', 'Grime', 'Electronic',
  'Bollywood', 'Punjabi', 'Tamil',
  'Hip Hop', 'R&B', 'Pop', 'Latin',
];

export default function ReviewPage() {
  const { needsReviewItems, clearNeedsReviewItem } = useSocket();
  const { addToast } = useToast();
  const [retrying, setRetrying] = useState({});
  const [retryAllProgress, setRetryAllProgress] = useState(null); // null | { current, total }
  const [geminiQuota, setGeminiQuota] = useState(null); // { remaining, total, exhausted }
  const [skippedTracks, setSkippedTracks] = useState(null); // { skipped: {id: count}, threshold, total }
  const [resettingSkipped, setResettingSkipped] = useState(false);
  const [selectedGenres, setSelectedGenres] = useState({}); // { [itemKey]: genre }
  const [moving, setMoving] = useState({}); // { [itemKey]: boolean }

  useEffect(() => {
    api.getGeminiQuota().then(setGeminiQuota).catch(() => {});
    api.getSkippedTracks().then(setSkippedTracks).catch(() => {});
  }, []);

  async function handleResetSkipped(trackId = null) {
    setResettingSkipped(true);
    try {
      await api.resetSkippedTracks(trackId);
      const fresh = await api.getSkippedTracks();
      setSkippedTracks(fresh);
      addToast({ type: 'success', title: trackId ? 'Track unblocked' : 'All skipped tracks reset', duration: 4000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Reset failed', description: err.message, duration: 4000 });
    } finally {
      setResettingSkipped(false);
    }
  }

  function buildFilepath(item) {
    const folder = item.suggested_folder || 'Library/Electronic';
    // Prefer explicit filename (set after the file lands on disk).
    // Fall back to "Title - Artist.mp3" (current naming format), then title-only for
    // older files that predate the title-artist format.
    if (item.filename) return `${folder}/${item.filename}`;
    const artist = item.artist || '';
    return artist
      ? `${folder}/${item.title} - ${artist}.mp3`
      : `${folder}/${item.title}.mp3`;
  }

  async function handleRetry(item) {
    const key = item.title;
    setRetrying((prev) => ({ ...prev, [key]: true }));
    try {
      const filepath = buildFilepath(item);
      const result = await api.retagCatchallTrack(filepath);
      if (result.moved) {
        addToast({
          type: 'success',
          title: 'Reclassified',
          description: `${item.title} → ${result.new_folder}`,
          duration: 5000,
        });
        clearNeedsReviewItem(item.title);
      } else if (result.quota_exhausted) {
        addToast({
          type: 'error',
          title: 'AI quota exhausted',
          description: 'Daily limit reached — this track will be retried automatically tomorrow.',
          duration: 8000,
        });
      } else {
        addToast({
          type: 'warning',
          title: 'Still unresolved',
          description: result.reason || 'Could not classify this track',
          duration: 5000,
        });
      }
    } catch (err) {
      addToast({
        type: 'error',
        title: 'Retry failed',
        description: err.message || 'Unknown error',
        duration: 4000,
      });
    } finally {
      setRetrying((prev) => ({ ...prev, [key]: false }));
    }
  }

  async function handleRetryAll() {
    const items = [...needsReviewItems];
    setRetryAllProgress({ current: 0, total: items.length });
    let processed = 0;
    let notFound = 0;
    let unresolved = 0;
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      setRetryAllProgress({ current: i + 1, total: items.length });
      const filepath = buildFilepath(item);
      try {
        const result = await api.retagCatchallTrack(filepath);
        if (result.moved) {
          clearNeedsReviewItem(item.title);
          processed++;
        } else if (result.quota_exhausted) {
          addToast({
            type: 'error',
            title: 'AI quota hit mid-run',
            description: `${processed} moved so far. Remaining tracks will be retried tomorrow.`,
            duration: 8000,
          });
          break;
        } else {
          unresolved++;
        }
      } catch (err) {
        // 404 = file not found on disk (path mismatch or already moved)
        if (err?.response?.status === 404) notFound++;
        // else: network error — skip silently
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    setRetryAllProgress(null);
    api.getGeminiQuota().then(setGeminiQuota).catch(() => {});

    if (processed > 0) {
      addToast({
        type: 'success',
        title: `${processed} track${processed !== 1 ? 's' : ''} reclassified`,
        description: unresolved > 0 ? `${unresolved} still unresolved — try again later or move manually` : undefined,
        duration: 6000,
      });
    } else if (notFound > 0) {
      addToast({
        type: 'error',
        title: 'Files not found on disk',
        description: `${notFound} track${notFound !== 1 ? 's' : ''} couldn't be located. Refresh the page to reload the queue.`,
        duration: 8000,
      });
    } else {
      addToast({
        type: 'warning',
        title: 'Nothing reclassified',
        description: unresolved > 0
          ? `${unresolved} tracks could not be classified — try again later.`
          : 'No tracks could be classified via artist lookup, Spotify, Last.fm, or MusicBrainz.',
        duration: 8000,
      });
    }
  }

  async function handleMoveAndRemember(item) {
    const itemKey = item.id || (item.title + '__' + item.artist);
    const genre = selectedGenres[itemKey];
    if (!genre) return;
    const key = itemKey;
    setMoving((prev) => ({ ...prev, [key]: true }));
    try {
      const filepath = buildFilepath(item);
      const result = await api.moveAndRemember(filepath, genre, item.artist || '');
      if (result.moved) {
        addToast({
          type: 'success',
          title: `Moved to ${genre}`,
          description: item.artist
            ? `${item.artist} will auto-route to ${genre} from now on`
            : item.title,
          duration: 5000,
        });
        clearNeedsReviewItem(item.title);
      } else {
        addToast({ type: 'error', title: 'Move failed', description: result.error, duration: 5000 });
      }
    } catch (err) {
      addToast({ type: 'error', title: 'Move failed', description: err.message, duration: 4000 });
    } finally {
      setMoving((prev) => ({ ...prev, [key]: false }));
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold">Unclassified Tracks</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            These tracks need your genre pick before they join your crates.
            Hit <strong className="text-amber-300">Retry</strong> to let the AI classify, or choose a genre and hit <strong className="text-emerald-400">Move &amp; Remember</strong> to sort it yourself.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {geminiQuota && (
            <Badge className={cn(
              'border gap-1',
              geminiQuota.exhausted
                ? 'bg-red-500/20 text-red-400 border-red-500/30'
                : 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
            )}>
              <Zap className="w-3 h-3" />
              {geminiQuota.exhausted
                ? 'AI classifier paused'
                : 'AI classifier: ready'}
            </Badge>
          )}
          {needsReviewItems.length > 0 && (
            <Badge className="bg-amber-500/20 text-amber-400 border-amber-500/30">
              {needsReviewItems.length} pending
            </Badge>
          )}
          {needsReviewItems.length > 1 && (
            <Button
              size="sm"
              disabled={!!retryAllProgress}
              onClick={handleRetryAll}
              className="bg-amber-600 hover:bg-amber-700 text-white disabled:opacity-50"
            >
              {retryAllProgress ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  {retryAllProgress.current}/{retryAllProgress.total}
                </>
              ) : (
                <>
                  <PlayCircle className="w-3.5 h-3.5 mr-1.5" />
                  Retry All
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Empty state */}
      {needsReviewItems.length === 0 && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex flex-col items-center justify-center py-20 text-center"
        >
          <div className="w-14 h-14 rounded-full bg-emerald-500/10 flex items-center justify-center mb-4">
            <CheckCircle2 className="w-7 h-7 text-emerald-400" />
          </div>
          <p className="text-lg font-semibold text-emerald-400">All sorted — nothing to review</p>
          <p className="text-sm text-gray-500 mt-1">Every downloaded track has been placed in a genre folder</p>
        </motion.div>
      )}

      {/* Skipped Tracks panel */}
      {skippedTracks && skippedTracks.total > 0 && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-xl border border-red-500/20 bg-red-500/5 p-4 space-y-3"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <SkipForward className="w-4 h-4 text-red-400" />
              <span className="text-sm font-semibold text-red-300">
                Blocked Downloads
              </span>
              <Badge className="bg-red-500/20 text-red-400 border-red-500/30">
                {skippedTracks.total}
              </Badge>
            </div>
            <Button
              size="sm"
              variant="ghost"
              disabled={resettingSkipped}
              onClick={() => handleResetSkipped()}
              className="text-red-400 hover:text-red-300 hover:bg-red-500/10 shrink-0"
            >
              {resettingSkipped ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <>
                  <Trash2 className="w-3.5 h-3.5 mr-1.5" />
                  Reset All
                </>
              )}
            </Button>
          </div>
          <p className="text-xs text-gray-500">
            These tracks failed to download {skippedTracks.threshold}+ times in a row so the app stopped trying.
            Click the reset button to try downloading them again.
          </p>
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {Object.entries(skippedTracks.skipped).map(([id, count]) => (
              <div key={id} className="flex items-center justify-between gap-2 text-xs text-gray-400 py-1 border-b border-red-500/10 last:border-0">
                <span className="font-mono truncate">{id}</span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-red-400">{count} fails</span>
                  <button
                    onClick={() => handleResetSkipped(id)}
                    disabled={resettingSkipped}
                    className="text-gray-500 hover:text-red-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                    title="Unblock this track"
                  >
                    <RotateCw className="w-3 h-3" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {/* Track list */}
      <AnimatePresence>
        {needsReviewItems.map((item) => {
          const itemKey = item.id || (item.title + '__' + item.artist);
          const conf = Math.round((item.confidence || 0) * 100);
          const isRetrying = retrying[item.title];
          return (
            <motion.div
              key={item.id || (item.title + '__' + item.artist)}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10, height: 0, marginBottom: 0 }}
              transition={{ duration: 0.2 }}
              className="p-4 rounded-xl border border-amber-500/20 bg-amber-500/5 space-y-3"
            >
              {/* Top row: icon + info + retry */}
              <div className="flex items-center gap-4">
                <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center">
                  <Music className="w-4 h-4 text-amber-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{item.title}</p>
                  <p className="text-xs text-gray-400 truncate">{item.artist}</p>
                  <div className="flex items-center gap-2 mt-1">
                    <span className={cn(
                      'text-[10px] px-1.5 py-0.5 rounded-full',
                      conf >= 60 ? 'bg-amber-500/20 text-amber-400' : 'bg-red-500/20 text-red-400'
                    )}>
                      {conf >= 60 ? 'Low confidence' : 'Genre unknown'}
                    </span>
                    {item.timestamp && (
                      <span className="text-[10px] text-gray-600">{item.timestamp}</span>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={isRetrying}
                  onClick={() => handleRetry(item)}
                  className="flex-shrink-0 text-amber-400 hover:text-amber-300 hover:bg-amber-500/10"
                >
                  {isRetrying ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <>
                      <RotateCw className="w-3.5 h-3.5 mr-1.5" />
                      Retry
                    </>
                  )}
                </Button>
              </div>

              {/* Bottom row: manual genre picker */}
              <div className="flex items-center gap-2 pl-12">
                <select
                  value={selectedGenres[itemKey] || ''}
                  onChange={(e) => setSelectedGenres((prev) => ({ ...prev, [itemKey]: e.target.value }))}
                  className="flex-1 text-xs bg-gray-900 border border-gray-700 text-gray-300 rounded-md px-2 py-1.5 cursor-pointer focus:outline-none focus:ring-1 focus:ring-amber-500/50 focus:border-amber-500/60"
                >
                  <option value="">Pick genre to move…</option>
                  {GENRE_OPTIONS.map((g) => (
                    <option key={g} value={g}>{g}</option>
                  ))}
                </select>
                <Button
                  size="sm"
                  disabled={!selectedGenres[itemKey] || moving[itemKey]}
                  onClick={() => handleMoveAndRemember(item)}
                  className="shrink-0 bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-40 text-xs"
                >
                  {moving[itemKey] ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <>
                      <FolderInput className="w-3.5 h-3.5 mr-1" />
                      Move &amp; Remember
                    </>
                  )}
                </Button>
              </div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
