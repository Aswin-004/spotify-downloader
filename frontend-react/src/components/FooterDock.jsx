import { motion, AnimatePresence } from 'framer-motion';
import { Loader2, X, Music2 } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { slideUpDock } from '@/lib/motion';

export default function FooterDock() {
  const { downloadStatus, downloads, queueStatus } = useSocket();

  const isDownloading = downloadStatus?.status === 'downloading';
  const activeCount   = Object.keys(downloads.downloading).length;
  const current       = downloadStatus?.current;
  const progress      = downloadStatus?.progress ?? 0;

  // Show when there are active downloads
  const visible = isDownloading || activeCount > 0;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          {...slideUpDock}
          className="dock-glow flex-shrink-0 flex items-center gap-4 px-6"
          style={{
            height: 44,
            background: 'var(--void)',
            borderTop: '1px solid rgba(6,182,212,0.18)',
          }}
        >
          {/* Waveform bars */}
          <div className="flex items-center gap-0.5 flex-shrink-0" style={{ height: 16 }}>
            {[10,16,22,14,20,12,18].map((h, i) => (
              <div key={i} className="waveform-bar" style={{ height: h, animationDelay: `${i * 0.1}s` }} />
            ))}
          </div>

          {/* Track info */}
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <Music2 className="w-3 h-3 flex-shrink-0" style={{ color: 'var(--text-tertiary)' }} />
            <span className="text-12 truncate" style={{ color: 'var(--text-secondary)' }}>
              {current?.title && current?.artist
                ? `${current.artist} — ${current.title}`
                : current?.title || 'Downloading...'}
            </span>
          </div>

          {/* Progress bar */}
          <div className="prog-bar w-32 flex-shrink-0" style={{ margin: 0 }}>
            <motion.div
              className="prog-fill h-full"
              style={{ transformOrigin: 'left', width: '100%' }}
              animate={{ scaleX: progress / 100 }}
              transition={{ type: 'spring', stiffness: 120, damping: 20 }}
            />
          </div>

          {/* Progress % */}
          <span className="font-mono text-11 tabular-nums flex-shrink-0"
                style={{ color: 'var(--accent-cyan)' }}>
            {Math.round(progress)}%
          </span>

          {/* Queue count */}
          {queueStatus?.total > 0 && (
            <span className="tbl-badge flex-shrink-0">
              Queue: {queueStatus.total}
            </span>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
