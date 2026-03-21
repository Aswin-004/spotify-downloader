import { cn } from '@/lib/utils';

function ScrollArea({ className, children, ...props }) {
  return (
    <div
      className={cn('overflow-y-auto scrollbar-thin', className)}
      {...props}
    >
      {children}
    </div>
  );
}

export { ScrollArea };
