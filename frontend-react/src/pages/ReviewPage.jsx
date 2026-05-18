import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  AlertTriangle,
  CheckCircle2,
  RotateCw,
  Loader2,
  Music,
  PlayCircle,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

export default function ReviewPage() {
  const { needsReviewItems, clearNeedsReviewItem } = useSocket();
  const { addToast } = useToast();
  const [retrying, setRetrying] = useState({});
  const [retryAllProgress, setRetryAllProgress] = useState(null); // null | { current, total }

  async function handleRetry(item) {
    const key = item.title;
    setRetrying((prev) => ({ ...prev, [key]: true }));
    try {
      // filepath stored as absolute or relative — pass the title+artist path hint
      const filepath = item.suggested_folder
        ? `${item.suggested_folder}/${item.title}.mp3`
        : `Library/Electronic/${item.title}.mp3`;
      const result = await api.retagCatchallTrack(filepath);
      if (result.moved) {
        addToast({
          type: 'success',
          title: 'Reclassified',
          description: `${item.title} → ${result.new_folder}`,
          duration: 5000,
        });
        clearNeedsReviewItem(item.title);
      } else {
        addToast({
          type: 'warning',
          title: 'Still unresolved',
          description: result.reason || 'Gemini could not classify this track',
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
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      setRetryAllProgress({ current: i + 1, total: items.length });
      const filepath = item.suggested_folder
        ? `${item.suggested_folder}/${item.title}.mp3`
        : `Library/Electronic/${item.title}.mp3`;
      try {
        const result = await api.retagCatchallTrack(filepath);
        if (result.moved) clearNeedsReviewItem(item.title);
      } catch { /* continue to next */ }
      await new Promise((r) => setTimeout(r, 800));
    }
    setRetryAllProgress(null);
    addToast({ type: 'success', title: 'Retry All complete', description: 'All tracks have been processed' });
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-bold">Catch-all Tracks</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Songs routed to <span className="font-mono text-amber-400">Library/Electronic/</span> because Gemini was unavailable.
            Retry to reclassify, or wait — the maintenance worker retries automatically every hour.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
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
          <p className="text-lg font-semibold text-emerald-400">All tracks correctly routed</p>
          <p className="text-sm text-gray-500 mt-1">No catch-all tracks pending review</p>
        </motion.div>
      )}

      {/* Track list */}
      <AnimatePresence>
        {needsReviewItems.map((item) => {
          const conf = Math.round((item.confidence || 0) * 100);
          const isRetrying = retrying[item.title];
          return (
            <motion.div
              key={item.title + '__' + item.artist}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 10, height: 0, marginBottom: 0 }}
              transition={{ duration: 0.2 }}
              className="flex items-center gap-4 p-4 rounded-xl border border-amber-500/20 bg-amber-500/5"
            >
              {/* Icon */}
              <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-amber-500/10 flex items-center justify-center">
                <Music className="w-4 h-4 text-amber-400" />
              </div>

              {/* Info */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate">{item.title}</p>
                <p className="text-xs text-gray-400 truncate">{item.artist}</p>
                <div className="flex items-center gap-2 mt-1">
                  <span className={cn(
                    'text-[10px] px-1.5 py-0.5 rounded-full font-mono',
                    conf >= 40 ? 'bg-amber-500/20 text-amber-400' : 'bg-red-500/20 text-red-400'
                  )}>
                    {conf}% confidence
                  </span>
                  {item.genre_source && (
                    <span className="text-[10px] text-gray-600">{item.genre_source}</span>
                  )}
                  {item.timestamp && (
                    <span className="text-[10px] text-gray-600">{item.timestamp}</span>
                  )}
                </div>
              </div>

              {/* Retry button */}
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
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
