import { motion, AnimatePresence } from 'framer-motion';
import { Music, CheckCircle2, XCircle, Loader2, AlertTriangle } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
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
  const { status, progress, current, match_quality } = downloadStatus;

  if (status === 'idle') return null;

  const config = statusConfig[status] || statusConfig.idle;
  const StatusIcon = config.icon;
  const isActive = status === 'downloading' || status === 'starting';

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
                <StatusIcon
                  className={cn(
                    'w-5 h-5',
                    isActive && 'animate-spin',
                    status === 'completed' && 'text-emerald-400',
                    status === 'failed' && 'text-red-400',
                    status === 'fallback' && 'text-amber-400',
                    isActive && 'text-primary'
                  )}
                />
                <h3 className="font-semibold text-sm">
                  {isActive
                    ? current?.toLowerCase().includes('retry')
                      ? 'Retrying...'
                      : current?.toLowerCase().includes('fallback')
                        ? 'Fallback source...'
                        : current?.toLowerCase().includes('searching') ||
                            progress <= 10
                          ? 'Matching...'
                          : 'Downloading...'
                    : status === 'completed'
                      ? 'Complete!'
                      : status === 'failed'
                        ? 'Failed'
                        : 'Processing...'}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                {match_quality && status === 'completed' && (
                  <Badge
                    variant={
                      match_quality === 'exact'
                        ? 'success'
                        : match_quality === 'approx'
                          ? 'warning'
                          : 'danger'
                    }
                  >
                    {match_quality === 'exact'
                      ? '✓ Exact'
                      : match_quality === 'approx'
                        ? '⚠ Approx'
                        : '✗ Fallback'}
                  </Badge>
                )}
                <Badge variant={config.variant}>{config.label}</Badge>
              </div>
            </div>

            {/* Progress */}
            <div className="space-y-2">
              <Progress
                value={progress}
                indicatorClassName={cn(
                  status === 'completed' && 'bg-emerald-500',
                  status === 'failed' && 'bg-red-500',
                  status === 'fallback' && 'bg-amber-500'
                )}
              />
              <div className="flex items-center justify-between">
                <p className="text-xs text-gray-400 truncate max-w-[80%]">
                  {current || status}
                </p>
                <span className="text-xs font-mono text-gray-500">
                  {progress}%
                </span>
              </div>
            </div>
          </div>
        </Card>
      </motion.div>
    </AnimatePresence>
  );
}
