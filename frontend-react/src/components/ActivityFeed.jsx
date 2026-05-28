import { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  CheckCircle2, SkipForward, XCircle, Loader2, Inbox,
  Radio, X, Zap,
} from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { cn } from '@/lib/utils';
import { slideInRight } from '@/lib/motion';

const STATUS = {
  success:     { icon: CheckCircle2, accent: 'var(--accent-emerald)', label: 'Done'       },
  completed:   { icon: CheckCircle2, accent: 'var(--accent-emerald)', label: 'Done'       },
  downloading: { icon: Loader2,      accent: 'var(--accent-cyan)',    label: 'Loading', spin: true },
  skipped:     { icon: SkipForward,  accent: 'var(--accent-slate)',   label: 'Skipped'    },
  failed:      { icon: XCircle,      accent: 'var(--accent-rose)',    label: 'Failed'     },
};

export default function ActivityFeed({ onClose }) {
  const { history, autoStatus, downloads } = useSocket();

  const events = useMemo(() => {
    const live = [
      ...Object.values(downloads.downloading).map(d => ({ ...d, status: 'downloading', _live: true })),
      ...Object.values(downloads.completed).map(d => ({ ...d, status: 'success',       _live: true })),
      ...Object.values(downloads.skipped).map(d => ({ ...d, status: 'skipped',         _live: true })),
      ...Object.values(downloads.failed).map(d => ({ ...d, status: 'failed',           _live: true })),
    ];
    return [...live, ...history];
  }, [downloads, history]);

  const isAutoActive = autoStatus.status === 'downloading' || autoStatus.status === 'checking';

  return (
    <div className="flex flex-col h-full">

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 flex-shrink-0"
           style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <div className="flex items-center gap-2">
          <Radio className="w-3.5 h-3.5" style={{ color: 'var(--accent-violet)' }} />
          <span className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>
            Activity
          </span>
          {/* Live waveform when downloads are active */}
          {events.some(e => e._live && e.status === 'downloading') && (
            <div className="flex items-center gap-0.5 ml-1" style={{ height: 12 }}>
              {[8,12,16,10,14].map((h, i) => (
                <div key={i} className="waveform-bar" style={{ height: h, animationDelay: `${i * 0.12}s`, width: 2 }} />
              ))}
            </div>
          )}
          {events.length > 0 && (
            <span className="text-10 font-mono tabular-nums px-1.5 py-0.5 rounded"
                  style={{ background: 'var(--surface-1)', color: 'var(--text-tertiary)' }}>
              {events.length}
            </span>
          )}
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="w-6 h-6 rounded flex items-center justify-center transition-colors cursor-pointer focus-ring"
            style={{ color: 'var(--text-tertiary)' }}
            title="Close activity feed (\\)"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Auto-sync banner */}
      <AnimatePresence>
        {isAutoActive && autoStatus.current && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="flex-shrink-0 overflow-hidden"
          >
            <div className="flex items-center gap-2 px-4 py-2"
                 style={{ background: 'var(--accent-cyan-dim)', borderBottom: '1px solid rgba(6,182,212,0.15)' }}>
              <div className="w-1.5 h-1.5 rounded-full animate-pulse flex-shrink-0"
                   style={{ background: 'var(--accent-cyan)' }} />
              <span className="text-11 truncate" style={{ color: 'var(--accent-cyan)' }}>
                Auto: {autoStatus.current}
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-2">
        {events.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center mb-3"
                 style={{ background: 'var(--surface-1)' }}>
              <Inbox className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
            </div>
            <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>
              No activity yet
            </p>
            <p className="text-11 mt-1" style={{ color: 'var(--text-muted)' }}>
              Events appear here in real-time
            </p>
          </div>
        ) : (
          <AnimatePresence initial={false}>
            {events.map((item, i) => {
              const cfg = STATUS[item.status] ?? STATUS.failed;
              const Icon = cfg.icon;

              return (
                <motion.div
                  key={`${item.title}-${item.status}-${item.timestamp}-${i}`}
                  layout
                  {...slideInRight}
                  transition={{ duration: 0.2, delay: Math.min(i * 0.025, 0.18) }}
                  className="flex items-start gap-2.5 px-2 py-2 rounded-lg transition-colors group cursor-default"
                  style={{
                    borderLeft: item._live && item.status === 'downloading'
                      ? '2px solid var(--accent-cyan)'
                      : '2px solid transparent',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-0)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  {/* Status icon */}
                  <div className="w-5 h-5 rounded-md flex items-center justify-center flex-shrink-0 mt-0.5"
                       style={{ background: `${cfg.accent}18` }}>
                    <Icon
                      className={cn('w-3 h-3', cfg.spin && 'animate-spin')}
                      style={{ color: cfg.accent }}
                    />
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <p className="text-12 font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                      {item.title}
                    </p>
                    <p className="text-11 truncate" style={{ color: 'var(--text-tertiary)' }}>
                      {item.artist}
                    </p>
                    {item.status === 'failed' && item.error && (
                      <p className="text-10 truncate mt-0.5" style={{ color: 'var(--accent-rose)' }}>
                        {item.error}
                      </p>
                    )}
                  </div>

                  {/* Timestamp */}
                  {item.timestamp && (
                    <span className="text-10 font-mono flex-shrink-0 mt-0.5" style={{ color: 'var(--text-muted)' }}>
                      {item.timestamp}
                    </span>
                  )}
                </motion.div>
              );
            })}
          </AnimatePresence>
        )}
      </div>
    </div>
  );
}
