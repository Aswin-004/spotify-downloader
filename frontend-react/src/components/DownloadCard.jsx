import { motion } from 'framer-motion';
import { Loader2, CheckCircle2, SkipForward, XCircle, Clock, FolderOpen } from 'lucide-react';
import { cn, formatTimeAgo } from '@/lib/utils';
import { ease } from '@/lib/motion';
import BPMDisplay from '@/components/BPMDisplay';
import GenreBadge from '@/components/GenreBadge';

const STATUS_CONFIG = {
  downloading: { icon: Loader2,      spin: true,  accent: 'var(--accent-cyan)',    dim: 'var(--accent-cyan-dim)'    },
  completed:   { icon: CheckCircle2, spin: false, accent: 'var(--accent-emerald)', dim: 'var(--accent-emerald-dim)' },
  success:     { icon: CheckCircle2, spin: false, accent: 'var(--accent-emerald)', dim: 'var(--accent-emerald-dim)' },
  skipped:     { icon: SkipForward,  spin: false, accent: 'var(--accent-slate)',   dim: 'var(--accent-slate-dim)'   },
  failed:      { icon: XCircle,      spin: false, accent: 'var(--accent-rose)',    dim: 'var(--accent-rose-dim)'    },
};

export default function DownloadCard({ item, index = 0 }) {
  const cfg = STATUS_CONFIG[item.status] ?? STATUS_CONFIG.downloading;
  const Icon = cfg.icon;

  const borderColor = item.status === 'downloading'
    ? 'rgba(6,182,212,0.20)'
    : item.status === 'failed'
    ? 'rgba(244,63,94,0.18)'
    : item.status === 'completed'
    ? 'rgba(16,185,129,0.12)'
    : 'var(--border-subtle)';

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0  }}
      exit={{ opacity: 0, y: -8    }}
      whileHover={{ y: -3, transition: { duration: 0.15, ease: [0.22,1,0.36,1] } }}
      transition={{ ...ease, delay: Math.min(index * 0.03, 0.2) }}
    >
      <div
        className={`rounded-xl overflow-hidden transition-all duration-200 ${item.status === 'downloading' ? 'glow-downloading' : ''}`}
        style={{
          background: 'var(--surface-0)',
          border: `1px solid ${borderColor}`,
        }}
        onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-default)'; }}
        onMouseLeave={e => { e.currentTarget.style.borderColor = borderColor; }}
      >
        <div className="flex items-center gap-3 p-3">

          {/* Status icon */}
          <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
               style={{ background: cfg.dim }}>
            <Icon
              className={cn('w-4 h-4', cfg.spin && 'animate-spin')}
              style={{ color: cfg.accent }}
            />
          </div>

          {/* Main content */}
          <div className="flex-1 min-w-0">
            {/* Track title + artist */}
            <p className="font-display text-14 font-semibold truncate leading-tight"
               style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
              {item.title}
            </p>
            <p className="text-12 truncate mt-0.5" style={{ color: 'var(--text-secondary)' }}>
              {item.artist || 'Unknown Artist'}
            </p>

            {/* Metadata row — completed state */}
            {item.status === 'completed' && (
              <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                {item.routing_label && <GenreBadge genre={item.routing_label} />}
                <BPMDisplay bpm={item.bpm} musicalKey={item.key} camelot={item.camelot} />
                {item.energy != null && (
                  <span className="text-10 font-mono px-1.5 py-0.5 rounded"
                        style={{ background: 'var(--surface-1)', color: 'var(--text-tertiary)' }}>
                    E{Math.round((item.energy ?? 0) * 10)}
                  </span>
                )}
              </div>
            )}

            {/* Progress bar — downloading state */}
            {item.status === 'downloading' && (
              <div className="flex items-center gap-2 mt-2">
                <div className="flex-1 h-1 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                  <motion.div
                    className="h-full rounded-full"
                    style={{ background: 'var(--accent-cyan)', transformOrigin: 'left' }}
                    animate={{ scaleX: (item.progress ?? 0) / 100 }}
                    transition={{ type: 'spring', stiffness: 120, damping: 20 }}
                  />
                </div>
                <span className="font-mono text-11 tabular-nums flex-shrink-0 w-9 text-right"
                      style={{ color: 'var(--accent-cyan)' }}>
                  {Math.round(item.progress ?? 0)}%
                </span>
              </div>
            )}

            {/* Error text — failed */}
            {item.status === 'failed' && item.error && (
              <p className="text-11 mt-1.5 truncate" style={{ color: 'var(--accent-rose)' }}>
                {item.error}
              </p>
            )}

            {/* Reason — skipped */}
            {item.status === 'skipped' && item.reason && (
              <p className="text-11 mt-1.5 truncate" style={{ color: 'var(--text-tertiary)' }}>
                {item.reason}
              </p>
            )}
          </div>

          {/* Right: timestamp */}
          {item.timestamp && (
            <div className="flex-shrink-0 flex items-center gap-1" style={{ color: 'var(--text-muted)' }}>
              <Clock className="w-3 h-3" />
              <span className="text-10 font-mono">{item.timestamp}</span>
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}
