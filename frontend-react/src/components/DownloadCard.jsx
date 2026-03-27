import { motion } from 'framer-motion';
import { Music2, Loader2, CheckCircle2, SkipForward, XCircle, Clock } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { ProgressBar } from '@/components/ProgressBar';
import { StatusBadge } from '@/components/StatusBadge';
import { cn } from '@/lib/utils';

const iconConfig = {
  downloading: {
    icon: Loader2,
    color: 'text-yellow-400',
    bg: 'bg-yellow-500/10',
    spin: true,
  },
  completed: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/10',
  },
  success: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/10',
  },
  skipped: {
    icon: SkipForward,
    color: 'text-amber-400',
    bg: 'bg-amber-500/10',
  },
  failed: {
    icon: XCircle,
    color: 'text-red-400',
    bg: 'bg-red-500/10',
  },
};

export default function DownloadCard({ item, index = 0 }) {
  const config = iconConfig[item.status] || iconConfig.downloading;
  const Icon = config.icon;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -12, scale: 0.97 }}
      transition={{
        duration: 0.3,
        delay: Math.min(index * 0.05, 0.25),
        layout: { duration: 0.25, ease: 'easeInOut' },
      }}
    >
      <Card className={cn(
        'overflow-hidden transition-all duration-300 hover:border-border-light group',
        item.status === 'downloading' && 'border-yellow-500/20 glow-yellow',
        item.status === 'completed' && 'border-emerald-500/20',
        item.status === 'failed' && 'border-red-500/20 glow-red',
      )}>
        <div className="p-4 flex items-center gap-4">
          {/* Status icon */}
          <motion.div
            className={cn(
              'w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0',
              config.bg
            )}
            animate={{
              scale: item.status === 'downloading' ? [1, 1.06, 1] : 1,
            }}
            transition={{
              duration: 2,
              repeat: item.status === 'downloading' ? Infinity : 0,
              ease: 'easeInOut',
            }}
          >
            <Icon
              className={cn('w-5 h-5', config.color, config.spin && 'animate-spin')}
            />
          </motion.div>

          {/* Content */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium truncate">{item.title}</p>
            </div>
            <p className="text-xs text-gray-500 truncate mt-0.5">
              {item.artist || 'Unknown Artist'}
              {item.filename && (
                <span className="text-gray-600"> · {item.filename}</span>
              )}
            </p>

            {/* Progress bar for downloading state */}
            {item.status === 'downloading' && (
              <div className="mt-2.5 flex items-center gap-3">
                <ProgressBar value={item.progress} color="yellow" pulse className="flex-1" />
                <motion.span
                  key={Math.round(item.progress || 0)}
                  initial={{ scale: 1.2, opacity: 0.7 }}
                  animate={{ scale: 1, opacity: 1 }}
                  transition={{ duration: 0.15 }}
                  className="text-xs font-mono text-gray-500 flex-shrink-0 w-10 text-right"
                >
                  {Math.round(item.progress || 0)}%
                </motion.span>
              </div>
            )}

            {/* Error message for failed */}
            {item.status === 'failed' && item.error && (
              <p className="text-xs text-red-400/80 mt-1.5 truncate">
                {item.error}
              </p>
            )}

            {/* Reason for skipped */}
            {item.status === 'skipped' && item.reason && (
              <p className="text-xs text-amber-400/70 mt-1.5 truncate">
                {item.reason}
              </p>
            )}
          </div>

          {/* Right side: badge + timestamp */}
          <div className="flex flex-col items-end gap-1.5 flex-shrink-0">
            <StatusBadge status={item.status} />
            {item.timestamp && (
              <span className="text-[10px] text-gray-600 font-mono flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {item.timestamp}
              </span>
            )}
          </div>
        </div>
      </Card>
    </motion.div>
  );
}
