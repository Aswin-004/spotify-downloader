import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';

const colorMap = {
  green: 'bg-green-500',
  yellow: 'bg-yellow-500',
  red: 'bg-red-500',
  blue: 'bg-blue-500',
  emerald: 'bg-emerald-500',
  amber: 'bg-amber-500',
  primary: 'bg-primary',
};

export function ProgressBar({
  value = 0,
  color = 'primary',
  size = 'md',
  showLabel = false,
  pulse = false,
  className,
}) {
  const clamped = Math.min(100, Math.max(0, value));
  const heights = { sm: 'h-1', md: 'h-2', lg: 'h-3' };
  const isActive = clamped > 0 && clamped < 100;

  return (
    <div className={cn('space-y-1', className)}>
      <div
        className={cn(
          'relative w-full overflow-hidden rounded-full bg-white/5',
          heights[size] || heights.md
        )}
      >
        <motion.div
          className={cn(
            'h-full rounded-full relative',
            colorMap[color] || colorMap.primary
          )}
          initial={false}
          animate={{ width: `${clamped}%` }}
          transition={{ duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] }}
        />
        {(pulse || isActive) && clamped < 100 && (
          <div className="absolute inset-0 rounded-full overflow-hidden">
            <div
              className="h-full animate-shimmer rounded-full"
              style={{
                width: `${clamped}%`,
                background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.15) 50%, transparent 100%)',
                backgroundSize: '200% 100%',
              }}
            />
          </div>
        )}
      </div>
      {showLabel && (
        <div className="flex justify-end">
          <span className="text-xs font-mono text-gray-500">{clamped}%</span>
        </div>
      )}
    </div>
  );
}
