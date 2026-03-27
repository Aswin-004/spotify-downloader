import { motion, AnimatePresence } from 'framer-motion';
import { Music, CheckCircle2, XCircle, Loader2, AlertTriangle } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ProgressBar } from '@/components/ProgressBar';
import { cn } from '@/lib/utils';

const statusConfig = {
  idle: { label: 'Idle', variant: 'secondary', icon: Music },
  starting: { label: 'Starting', variant: 'info', icon: Loader2 },
  downloading: { label: 'Downloading', variant: 'default', icon: Loader2 },
  completed: { label: 'Complete', variant: 'success', icon: CheckCircle2 },
  failed: { label: 'Failed', variant: 'danger', icon: XCircle },
  fallback: { label: 'Fallback', variant: 'warning', icon: AlertTriangle },
};

export default function QueueCard() {
  const { downloadStatus } = useSocket();

  const status = downloadStatus.status;
  if (status === 'idle') return null;

  const config = statusConfig[status] || statusConfig.idle;
  const Icon = config.icon;
  const isActive = status === 'downloading' || status === 'starting';

  const progressColor =
    status === 'completed' ? 'emerald' :
    status === 'failed' ? 'red' :
    status === 'fallback' ? 'amber' : 'primary';

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -20 }}
        transition={{ duration: 0.3 }}
      >
        <Card
          className={cn(
            'overflow-hidden transition-all duration-300',
            isActive && 'border-primary/30 glow-green'
          )}
        >
          <div className="p-5">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Icon
                  className={cn(
                    'w-5 h-5',
                    isActive && 'animate-spin text-primary',
                    status === 'completed' && 'text-emerald-400',
                    status === 'failed' && 'text-red-400',
                    status === 'fallback' && 'text-amber-400'
                  )}
                />
                <h3 className="font-semibold text-sm">
                  {isActive
                    ? 'Downloading...'
                    : status === 'completed'
                      ? 'Complete!'
                      : status === 'failed'
                        ? 'Failed'
                        : 'Processing...'}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                {downloadStatus.match_quality && status === 'completed' && (
                  <Badge
                    variant={
                      downloadStatus.match_quality === 'exact'
                        ? 'success'
                        : downloadStatus.match_quality === 'approx'
                          ? 'warning'
                          : 'danger'
                    }
                  >
                    {downloadStatus.match_quality === 'exact'
                      ? '✓ Exact'
                      : downloadStatus.match_quality === 'approx'
                        ? '⚠ Approx'
                        : '✗ Fallback'}
                  </Badge>
                )}
                <Badge variant={config.variant}>{config.label}</Badge>
              </div>
            </div>
            <div className="space-y-2">
              <ProgressBar value={downloadStatus.progress} color={progressColor} />
              <div className="flex items-center justify-between">
                <p className="text-xs text-gray-400 truncate max-w-[80%]">
                  {downloadStatus.current || status}
                </p>
                <span className="text-xs font-mono text-gray-500">
                  {downloadStatus.progress}%
                </span>
              </div>
            </div>
          </div>
        </Card>
      </motion.div>
    </AnimatePresence>
  );
}
