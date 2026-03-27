import { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, SkipForward, RotateCw, XCircle, Radio, Loader2, Inbox } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

const statusIcons = {
  success: { icon: CheckCircle2, color: 'text-emerald-400', label: 'Downloaded' },
  completed: { icon: CheckCircle2, color: 'text-emerald-400', label: 'Downloaded' },
  downloading: { icon: Loader2, color: 'text-yellow-400', label: 'Downloading', spin: true },
  skipped: { icon: SkipForward, color: 'text-amber-400', label: 'Skipped' },
  failed: { icon: XCircle, color: 'text-red-400', label: 'Failed' },
  fallback: { icon: RotateCw, color: 'text-amber-400', label: 'Retry' },
};

export default function ActivityFeed() {
  const { history, autoStatus, downloads } = useSocket();

  // Build live ingest feed from structured downloads state, newest first
  const ingestEvents = useMemo(() => {
    const events = [
      ...Object.values(downloads.downloading).map((d) => ({ ...d, status: 'downloading' })),
      ...Object.values(downloads.completed).map((d) => ({ ...d, status: 'success' })),
      ...Object.values(downloads.skipped).map((d) => ({ ...d, status: 'skipped' })),
      ...Object.values(downloads.failed).map((d) => ({ ...d, status: 'failed' })),
    ];
    return events;
  }, [downloads]);

  const allEvents = [...ingestEvents, ...history];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-semibold">Activity</h3>
        </div>
        <span className="text-xs text-gray-500">{allEvents.length} events</span>
      </div>

      {/* Auto-downloader banner */}
      {autoStatus.status !== 'idle' && autoStatus.current && (
        <div className="px-4 py-2 border-b border-border bg-primary/5">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
            <span className="text-xs text-gray-400 truncate">
              Auto: {autoStatus.current}
            </span>
          </div>
        </div>
      )}

      {/* Feed */}
      <ScrollArea className="flex-1 p-2">
        {allEvents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-600">
            <div className="w-12 h-12 rounded-2xl bg-surface-light flex items-center justify-center mb-3">
              <Inbox className="w-6 h-6" />
            </div>
            <p className="text-sm font-medium text-gray-500">No activity yet</p>
            <p className="text-xs text-gray-600 mt-1">Events will appear here in real-time</p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {allEvents.map((item, i) => {
              const config = statusIcons[item.status] || statusIcons.failed;
              const StatusIcon = config.icon;

              return (
                <motion.div
                  key={`${item.title}-${item.status}-${item.timestamp}-${i}`}
                  layout
                  initial={{ opacity: 0, x: 16, scale: 0.97 }}
                  animate={{ opacity: 1, x: 0, scale: 1 }}
                  transition={{ duration: 0.25, delay: Math.min(i * 0.02, 0.4), layout: { duration: 0.2 } }}
                  className="flex items-start gap-2.5 px-2 py-2 rounded-lg hover:bg-surface-light/50 transition-colors group"
                >
                  <StatusIcon
                    className={cn(
                      'w-4 h-4 mt-0.5 flex-shrink-0',
                      config.color,
                      config.spin && 'animate-spin'
                    )}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate">
                      {item.title}
                    </p>
                    <p className="text-[11px] text-gray-500 truncate">
                      {item.artist}
                    </p>
                  </div>
                  <span className="text-[10px] text-gray-600 flex-shrink-0 mt-0.5">
                    {item.timestamp}
                  </span>
                </motion.div>
              );
            })}
          </AnimatePresence>
        )}
      </ScrollArea>
    </div>
  );
}
