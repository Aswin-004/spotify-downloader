import { motion } from 'framer-motion';
import { spring } from '@/lib/motion';
import { cn } from '@/lib/utils';

// Animated confidence bar — shows AI genre classification confidence
// pct: 0-100, label: genre name
export default function ConfidenceBar({ pct = 0, label, showPct = true, className, compact = false }) {
  const color = pct >= 70
    ? 'var(--accent-violet)'
    : pct >= 40
    ? 'var(--accent-amber)'
    : 'var(--accent-rose)';

  const bgColor = pct >= 70
    ? 'var(--accent-violet-dim)'
    : pct >= 40
    ? 'var(--accent-amber-dim)'
    : 'var(--accent-rose-dim)';

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {/* Bar track */}
      <div
        className="flex-1 rounded-full overflow-hidden"
        style={{
          height: compact ? 3 : 4,
          background: 'var(--surface-2)',
        }}
      >
        <motion.div
          className="h-full rounded-full origin-left"
          style={{ background: color }}
          initial={{ scaleX: 0 }}
          animate={{ scaleX: pct / 100 }}
          transition={{ ...spring, delay: 0.05 }}
        />
      </div>

      {/* Label and percentage */}
      {!compact && (
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {label && (
            <span className="text-11 font-semibold tracking-wide uppercase"
                  style={{ color: 'var(--text-secondary)' }}>
              {label}
            </span>
          )}
          {showPct && (
            <span className="font-mono text-11 font-bold tabular-nums"
                  style={{ color }}>
              {Math.round(pct)}%
            </span>
          )}
        </div>
      )}
    </div>
  );
}
