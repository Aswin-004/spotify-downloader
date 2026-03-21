import { cn } from '@/lib/utils';

function Progress({ value = 0, className, indicatorClassName }) {
  return (
    <div
      className={cn(
        'relative h-2 w-full overflow-hidden rounded-full bg-surface-light',
        className
      )}
    >
      <div
        className={cn(
          'h-full rounded-full bg-primary transition-all duration-500 ease-out',
          indicatorClassName
        )}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  );
}

export { Progress };
