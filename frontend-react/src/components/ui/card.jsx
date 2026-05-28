import { forwardRef } from 'react';
import { cn } from '@/lib/utils';

const Card = forwardRef(({ className, style, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('rounded-xl', className)}
    style={{
      background: 'var(--surface-0)',
      border: '1px solid var(--border-subtle)',
      boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
      ...style,
    }}
    {...props}
  />
));
Card.displayName = 'Card';

const CardHeader = forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('flex flex-col gap-1 p-4', className)} {...props} />
));
CardHeader.displayName = 'CardHeader';

const CardTitle = forwardRef(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn('font-display text-14 font-semibold leading-tight tracking-tight', className)}
    style={{ color: 'var(--text-primary)' }}
    {...props}
  />
));
CardTitle.displayName = 'CardTitle';

const CardDescription = forwardRef(({ className, ...props }, ref) => (
  <p ref={ref} className={cn('text-12', className)} style={{ color: 'var(--text-secondary)' }} {...props} />
));
CardDescription.displayName = 'CardDescription';

const CardContent = forwardRef(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('p-4 pt-0', className)} {...props} />
));
CardContent.displayName = 'CardContent';

export { Card, CardHeader, CardTitle, CardDescription, CardContent };
