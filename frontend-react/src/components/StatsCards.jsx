import { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Download, ListMusic, TrendingUp, XCircle } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { useSocket } from '@/hooks/useSocket';
import { cn } from '@/lib/utils';

function AnimatedNumber({ value }) {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);

  useEffect(() => {
    const start = prevRef.current;
    const end = value;
    if (start === end) return;

    const duration = 400;
    const startTime = performance.now();

    function tick(now) {
      const elapsed = now - startTime;
      if (elapsed >= duration) {
        setDisplay(end);
        prevRef.current = end;
        return;
      }
      const progress = elapsed / duration;
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(Math.round(start + (end - start) * eased));
      requestAnimationFrame(tick);
    }

    requestAnimationFrame(tick);
  }, [value]);

  return <span>{display}</span>;
}

const cards = [
  {
    key: 'total',
    label: 'Total Downloads',
    icon: Download,
    color: 'text-primary',
    bg: 'bg-primary/10',
    gradient: 'from-primary/5 to-transparent',
  },
  {
    key: 'active',
    label: 'Active Queue',
    icon: ListMusic,
    color: 'text-blue-400',
    bg: 'bg-blue-400/10',
    gradient: 'from-blue-400/5 to-transparent',
  },
  {
    key: 'rate',
    label: 'Success Rate',
    icon: TrendingUp,
    color: 'text-emerald-400',
    bg: 'bg-emerald-400/10',
    gradient: 'from-emerald-400/5 to-transparent',
  },
  {
    key: 'failed',
    label: 'Failed',
    icon: XCircle,
    color: 'text-red-400',
    bg: 'bg-red-400/10',
    gradient: 'from-red-400/5 to-transparent',
  },
];

export default function StatsCards() {
  const { history, queueStatus, downloads } = useSocket();

  const ingestCompleted = Object.keys(downloads.completed).length;
  const ingestFailed = Object.keys(downloads.failed).length;
  const ingestDownloading = Object.keys(downloads.downloading).length;

  const total = history.length + ingestCompleted;
  const successCount = history.filter((h) => h.status === 'success').length + ingestCompleted;
  const failedCount = history.filter(
    (h) => h.status !== 'success' && h.status !== 'skipped'
  ).length + ingestFailed;
  const rate = total > 0 ? Math.round((successCount / total) * 100) : 0;
  const active =
    ingestDownloading > 0
      ? ingestDownloading
      : queueStatus.total > 0
        ? queueStatus.total - queueStatus.completed
        : 0;

  const values = { total, active, rate, failed: failedCount };

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      {cards.map(({ key, label, icon: Icon, color, bg, gradient }, i) => (
        <motion.div
          key={key}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: i * 0.08 }}
        >
          <Card className="relative overflow-hidden group hover:border-border-light transition-colors">
            <div
              className={cn(
                'absolute inset-0 bg-gradient-to-br opacity-0 group-hover:opacity-100 transition-opacity duration-500',
                gradient
              )}
            />
            <div className="relative p-4">
              <div className="flex items-center justify-between mb-2">
                <motion.div
                  className={cn('p-2 rounded-lg', bg)}
                  whileHover={{ scale: 1.1, rotate: -5 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 15 }}
                >
                  <Icon className={cn('w-4 h-4', color)} />
                </motion.div>
              </div>
              <div className="text-2xl font-bold tracking-tight">
                <AnimatedNumber value={values[key]} />
                {key === 'rate' && '%'}
              </div>
              <p className="text-xs text-gray-500 mt-1">{label}</p>
            </div>
          </Card>
        </motion.div>
      ))}
    </div>
  );
}
