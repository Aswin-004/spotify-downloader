import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center rounded-md text-11 font-semibold tracking-wide transition-colors',
  {
    variants: {
      variant: {
        default:     'px-2 py-0.5',
        success:     'px-2 py-0.5',
        warning:     'px-2 py-0.5',
        danger:      'px-2 py-0.5',
        info:        'px-2 py-0.5',
        secondary:   'px-2 py-0.5',
        ai:          'px-2 py-0.5',
      },
    },
    defaultVariants: { variant: 'default' },
  }
);

const variantStyles = {
  default:   { background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)', border: '1px solid rgba(139,92,246,0.25)' },
  success:   { background: 'var(--accent-emerald-dim)', color: 'var(--accent-emerald)', border: '1px solid rgba(16,185,129,0.25)' },
  warning:   { background: 'var(--accent-amber-dim)', color: 'var(--accent-amber)', border: '1px solid rgba(245,158,11,0.25)' },
  danger:    { background: 'var(--accent-rose-dim)', color: 'var(--accent-rose)', border: '1px solid rgba(244,63,94,0.25)' },
  info:      { background: 'var(--accent-cyan-dim)', color: 'var(--accent-cyan)', border: '1px solid rgba(6,182,212,0.25)' },
  secondary: { background: 'var(--accent-slate-dim)', color: 'var(--accent-slate)', border: '1px solid rgba(100,116,139,0.25)' },
  ai:        { background: 'var(--accent-violet-dim)', color: 'var(--text-accent)', border: '1px solid rgba(139,92,246,0.3)' },
};

function Badge({ className, variant = 'default', style, ...props }) {
  return (
    <span
      className={cn(badgeVariants({ variant }), className)}
      style={{ ...variantStyles[variant], ...style }}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
