import { useEffect, useState, useMemo } from 'react';
import { motion } from 'framer-motion';
import {
  Search,
  Trash2,
  CheckCircle2,
  XCircle,
  SkipForward,
  RotateCw,
  History as HistoryIcon,
} from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { cn } from '@/lib/utils';

const statusConfig = {
  success: {
    icon: CheckCircle2,
    variant: 'success',
    label: 'Downloaded',
    color: 'text-emerald-400',
  },
  skipped: {
    icon: SkipForward,
    variant: 'secondary',
    label: 'Skipped',
    color: 'text-gray-400',
  },
  failed: {
    icon: XCircle,
    variant: 'danger',
    label: 'Failed',
    color: 'text-red-400',
  },
  fallback: {
    icon: RotateCw,
    variant: 'warning',
    label: 'Fallback',
    color: 'text-amber-400',
  },
};

export default function History() {
  const { history: socketHistory } = useSocket();
  const [history, setHistory] = useState([]);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');

  // Merge socket history with initial load
  useEffect(() => {
    api.getHistory().then((data) => {
      if (data.history) setHistory(data.history);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (socketHistory.length > 0) setHistory(socketHistory);
  }, [socketHistory]);

  const filtered = useMemo(() => {
    let items = history;
    if (filter !== 'all') {
      items = items.filter((h) => h.status === filter);
    }
    if (search) {
      const q = search.toLowerCase();
      items = items.filter(
        (h) =>
          (h.title || '').toLowerCase().includes(q) ||
          (h.artist || '').toLowerCase().includes(q)
      );
    }
    return items;
  }, [history, search, filter]);

  async function handleClear() {
    try {
      await api.clearHistory();
      setHistory([]);
    } catch {
      // ignore
    }
  }

  const filters = [
    { value: 'all', label: 'All' },
    { value: 'success', label: 'Downloaded' },
    { value: 'failed', label: 'Failed' },
    { value: 'skipped', label: 'Skipped' },
  ];

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-primary/10">
            <HistoryIcon className="w-5 h-5 text-primary" />
          </div>
          <div>
            <h1 className="text-xl font-semibold">Download History</h1>
            <p className="text-sm text-gray-500">{history.length} entries</p>
          </div>
        </div>
        <Button variant="destructive" size="sm" onClick={handleClear}>
          <Trash2 className="w-3.5 h-3.5" />
          Clear
        </Button>
      </motion.div>

      {/* Filters */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.1 }}
        className="flex flex-col sm:flex-row gap-3"
      >
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by title or artist..."
            className="pl-9"
          />
        </div>
        <div className="flex gap-1 bg-surface rounded-xl p-1 border border-border">
          {filters.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                'px-3 py-1.5 rounded-lg text-xs font-medium transition-all',
                filter === f.value
                  ? 'bg-primary/15 text-primary'
                  : 'text-gray-400 hover:text-white hover:bg-surface-light'
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </motion.div>

      {/* History List */}
      <Card>
        <ScrollArea className="max-h-[calc(100vh-280px)]">
          {filtered.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-gray-600">
              <HistoryIcon className="w-10 h-10 mb-3" />
              <p className="text-sm">No history entries found</p>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {filtered.map((item, i) => {
                const config =
                  statusConfig[item.status] || statusConfig.failed;
                const StatusIcon = config.icon;

                return (
                  <motion.div
                    key={`${item.title}-${item.timestamp}-${i}`}
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ delay: Math.min(i * 0.02, 0.5) }}
                    className="flex items-center gap-4 px-5 py-3 hover:bg-surface-light/30 transition-colors"
                  >
                    <StatusIcon
                      className={cn('w-4 h-4 flex-shrink-0', config.color)}
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">
                        {item.title}
                      </p>
                      <p className="text-xs text-gray-500 truncate">
                        {item.artist}
                      </p>
                    </div>
                    <Badge variant={config.variant} className="flex-shrink-0">
                      {config.label}
                    </Badge>
                    <span className="text-xs text-gray-600 flex-shrink-0 font-mono min-w-[60px] text-right">
                      {item.timestamp}
                    </span>
                  </motion.div>
                );
              })}
            </div>
          )}
        </ScrollArea>
      </Card>
    </div>
  );
}
