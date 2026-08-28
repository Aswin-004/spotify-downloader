import { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Download, Loader2, TrendingUp, XCircle } from 'lucide-react';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { cn } from '@/lib/utils';
import { ease } from '@/lib/motion';

function AnimatedNumber({ value, suffix = '' }) {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);
  const rafRef  = useRef(null);

  useEffect(() => {
    const start = prevRef.current;
    const end   = value;
    if (start === end) return;

    const duration  = 500;
    const startTime = performance.now();

    function tick(now) {
      const elapsed  = now - startTime;
      if (elapsed >= duration) {
        setDisplay(end);
        prevRef.current = end;
        return;
      }
      const t = elapsed / duration;
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(Math.round(start + (end - start) * eased));
      rafRef.current = requestAnimationFrame(tick);
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value]);

  return <span>{display}{suffix}</span>;
}

const KPI_CONFIG = [
  {
    key:     'downloading',
    label:   'Active',
    icon:    Loader2,
    spin:    true,
    accent:  'var(--accent-cyan)',
    dim:     'var(--accent-cyan-dim)',
    border:  'rgba(6,182,212,0.18)',
  },
  {
    key:     'total',
    label:   'Total Downloads',
    icon:    Download,
    accent:  'var(--accent-violet)',
    dim:     'var(--accent-violet-dim)',
    border:  'rgba(139,92,246,0.15)',
  },
  {
    key:     'rate',
    label:   'Success Rate',
    icon:    TrendingUp,
    suffix:  '%',
    accent:  'var(--accent-emerald)',
    dim:     'var(--accent-emerald-dim)',
    border:  'rgba(16,185,129,0.15)',
  },
  {
    key:     'failed',
    label:   'Failed',
    icon:    XCircle,
    accent:  'var(--accent-rose)',
    dim:     'var(--accent-rose-dim)',
    border:  'rgba(244,63,94,0.15)',
  },
];

export default function StatsCards() {
  const { history, queueStatus, downloads } = useSocket();
  const [dbData, setDbData] = useState({ total: null, rate: null });

  const dlDownloading = Object.keys(downloads.downloading).length;
  const dlCompleted   = Object.keys(downloads.completed).length;
  const dlFailed      = Object.keys(downloads.failed).length;

  useEffect(() => {
    api.getAnalyticsOverview()
      .then(data => setDbData({
        total: data.total_downloads ?? null,
        rate:  data.success_rate    ?? null,
      }))
      .catch(() => {});
  }, [dlCompleted]);

  const total  = dbData.total !== null ? dbData.total : history.length;
  const rate   = dbData.rate  !== null
    ? Math.round(dbData.rate)
    : (total > 0
        ? Math.round((history.filter(h => h.status === 'success').length + dlCompleted) / total * 100)
        : 0);
  const failed = history.filter(h => h.status !== 'success' && h.status !== 'skipped').length + dlFailed;
  const active = dlDownloading > 0
    ? dlDownloading
    : Math.max(0, (queueStatus.total ?? 0) - (queueStatus.completed ?? 0));

  const values = { downloading: active, total, rate, failed };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.22,1,0.36,1] }}
      className="grid-stat"
    >
      {KPI_CONFIG.map(({ key, label, icon: Icon, spin, suffix, accent }) => (
        <div key={key} className="stat-card">
          <Icon
            className={cn('stat-ic', spin && values[key] > 0 && 'animate-spin')}
            style={{ width: 22, height: 22, color: accent }}
          />
          <div className="stat-lbl text-label">{label}</div>
          <div className="stat-val" style={{ color: accent }}>
            <AnimatedNumber value={values[key] ?? 0} suffix={suffix} />
          </div>
        </div>
      ))}
    </motion.div>
  );
}
