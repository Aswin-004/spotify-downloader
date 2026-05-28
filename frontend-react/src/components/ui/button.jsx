import { forwardRef } from 'react';
import { cva } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-lg text-13 font-medium transition-all duration-150 focus-ring disabled:pointer-events-none disabled:opacity-40 cursor-pointer active:scale-[0.97]',
  {
    variants: {
      variant: {
        default:     'text-white hover:opacity-90',
        secondary:   'border hover:opacity-90',
        ghost:       'hover:opacity-80',
        destructive: 'border',
        outline:     'border bg-transparent hover:opacity-90',
        link:        'underline-offset-4 hover:underline p-0 h-auto',
      },
      size: {
        default: 'h-9 px-4',
        sm:      'h-7 px-3 text-12',
        lg:      'h-11 px-6 text-14',
        icon:    'h-9 w-9 p-0',
        'icon-sm': 'h-7 w-7 p-0',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

// Style map — keeps inline styles out of className for token compatibility
const variantStyles = {
  default: {
    background: 'var(--accent-violet)',
    color: 'white',
    boxShadow: '0 2px 8px var(--accent-violet-glow)',
  },
  secondary: {
    background: 'var(--surface-1)',
    color: 'var(--text-primary)',
    borderColor: 'var(--border-default)',
  },
  ghost: {
    background: 'transparent',
    color: 'var(--text-secondary)',
  },
  destructive: {
    background: 'var(--accent-rose-dim)',
    color: 'var(--accent-rose)',
    borderColor: 'rgba(244,63,94,0.25)',
  },
  outline: {
    background: 'transparent',
    color: 'var(--text-secondary)',
    borderColor: 'var(--border-default)',
  },
  link: {
    background: 'transparent',
    color: 'var(--accent-violet)',
  },
};

const Button = forwardRef(({ className, variant = 'default', size = 'default', style, ...props }, ref) => {
  return (
    <button
      className={cn(buttonVariants({ variant, size, className }))}
      ref={ref}
      style={{ ...variantStyles[variant], ...style }}
      {...props}
    />
  );
});

Button.displayName = 'Button';

export { Button, buttonVariants };
