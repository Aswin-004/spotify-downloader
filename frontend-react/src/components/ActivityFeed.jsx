import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, SkipForward, RotateCw, XCircle, Radio } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

const statusIcons = {
  success: { icon: CheckCircle2, color: 'text-emerald-400', label: 'Downloaded' },
  skipped: { icon: SkipForward, color: 'text-gray-400', label: 'Skipped' },
  failed: { icon: XCircle, color: 'text-red-400', label: 'Failed' },
  fallback: { icon: RotateCw, color: 'text-amber-400', label: 'Retry' },
};

export default function ActivityFeed() {
  const { history, autoStatus } = useSocket();

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4 text-primary" />
          <h3 className="text-sm font-semibold">Activity</h3>
        </div>
        <span className="text-xs text-gray-500">{history.length} events</span>
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
        {history.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-gray-600">
            <Radio className="w-8 h-8 mb-2" />
            <p className="text-sm">No activity yet</p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {history.map((item, i) => {
              const config = statusIcons[item.status] || statusIcons.failed;
              const StatusIcon = config.icon;

              return (
                <motion.div
                  key={`${item.title}-${item.timestamp}-${i}`}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.2, delay: i * 0.02 }}
                  className="flex items-start gap-2.5 px-2 py-2 rounded-lg hover:bg-surface-light/50 transition-colors group"
                >
                  <StatusIcon
                    className={cn('w-4 h-4 mt-0.5 flex-shrink-0', config.color)}
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
