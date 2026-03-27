import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, Loader2, SkipForward, XCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const statusConfig = {
  downloading: {
    label: 'Downloading',
    variant: 'warning',
    icon: Loader2,
    spin: true,
  },
  completed: {
    label: 'Completed',
    variant: 'success',
    icon: CheckCircle2,
  },
  success: {
    label: 'Completed',
    variant: 'success',
    icon: CheckCircle2,
  },
  skipped: {
    label: 'Skipped',
    variant: 'secondary',
    icon: SkipForward,
  },
  failed: {
    label: 'Failed',
    variant: 'danger',
    icon: XCircle,
  },
};

export function StatusBadge({ status, className }) {
  const config = statusConfig[status] || statusConfig.downloading;
  const Icon = config.icon;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={status}
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.8, opacity: 0 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
      >
        <Badge variant={config.variant} className={cn('flex items-center gap-1', className)}>
          <Icon className={cn('w-3 h-3', config.spin && 'animate-spin')} />
          {config.label}
        </Badge>
      </motion.div>
    </AnimatePresence>
  );
}
