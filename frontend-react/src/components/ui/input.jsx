import { forwardRef } from 'react';
import { cn } from '@/lib/utils';

const Input = forwardRef(({ className, type = 'text', ...props }, ref) => {
  return (
    <input
      type={type}
      className={cn(
        'flex h-10 w-full rounded-lg px-3 py-2 text-13 transition-all duration-150 outline-none disabled:cursor-not-allowed disabled:opacity-40',
        className
      )}
      style={{
        background: 'var(--surface-1)',
        color: 'var(--text-primary)',
        border: '1px solid var(--border-default)',
        '--tw-ring-color': 'var(--border-focus)',
      }}
      onFocus={e => {
        e.currentTarget.style.borderColor = 'var(--border-focus)';
        e.currentTarget.style.boxShadow   = '0 0 0 2px var(--accent-violet-dim)';
      }}
      onBlur={e => {
        e.currentTarget.style.borderColor = 'var(--border-default)';
        e.currentTarget.style.boxShadow   = 'none';
      }}
      ref={ref}
      {...props}
    />
  );
});

Input.displayName = 'Input';

export { Input };
