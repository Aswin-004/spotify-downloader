import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderSync,
  DatabaseZap,
  Sparkles,
  Play,
  Square,
  Trash2,
  ChevronDown,
  ChevronRight,
  AlertTriangle,
  CheckCircle2,
  Loader2,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { cn } from '@/lib/utils';

const TASKS = [
  {
    id: 'organise',
    icon: FolderSync,
    title: 'Reorganise Library',
    description: 'Splits Indian→Bollywood/Punjabi, cleans up tiny folders, deduplicates, sweeps numbered rips, and resolves NeedsReview artist folders.',
    color: 'purple',
    steps: ['Split Indian folder', 'Clean tiny folders', 'Deduplicate', 'Sweep numbered rips', 'Resolve NeedsReview artists'],
  },
  {
    id: 'repair_index',
    icon: DatabaseZap,
    title: 'Repair MongoDB Index',
    description: 'Fixes stale file paths, indexes orphaned files on disk, and soft-deletes ghost entries that no longer exist.',
    color: 'blue',
    steps: ['Build disk index', 'Fix stale paths', 'Index orphaned files'],
  },
  {
    id: 'backfill_gemini',
    icon: Sparkles,
    title: 'Backfill Gemini Tags',
    description: 'Runs up to 4 passes: classify Indian remainder, identify Unknown tracks, enrich missing genre tags, and add BPM/key via librosa.',
    color: 'amber',
    passes: [
      { id: 1, label: 'Pass 1 — Classify Indian/ via Gemini' },
      { id: 2, label: 'Pass 2 — Identify NeedsReview/Unknown/' },
      { id: 3, label: 'Pass 3 — Enrich missing gemini_genre tags' },
      { id: 4, label: 'Pass 4 — BPM + key via librosa' },
    ],
  },
];

const COLOR = {
  purple: { badge: 'bg-purple-500/20 text-purple-300', border: 'border-purple-500/30', icon: 'text-purple-400', btn: 'bg-purple-600 hover:bg-purple-700' },
  blue:   { badge: 'bg-blue-500/20 text-blue-300',     border: 'border-blue-500/30',   icon: 'text-blue-400',   btn: 'bg-blue-600 hover:bg-blue-700'   },
  amber:  { badge: 'bg-amber-500/20 text-amber-300',   border: 'border-amber-500/30',  icon: 'text-amber-400',  btn: 'bg-amber-600 hover:bg-amber-700'  },
};

function LogLine({ line }) {
  const isError  = /error|fail|warn|✗/i.test(line);
  const isDone   = /✓|done|exit 0/i.test(line);
  const isHeader = line.startsWith('===') || line.startsWith('▶');
  return (
    <div className={cn(
      'font-mono text-xs leading-5 px-1',
      isError  && 'text-red-400',
      isDone   && !isError && 'text-emerald-400',
      isHeader && !isError && !isDone && 'text-purple-300 font-semibold',
      !isError && !isDone && !isHeader && 'text-gray-300',
    )}>
      {line}
    </div>
  );
}

function TaskCard({ task, activeTask, onRun, running, logs }) {
  const [expanded, setExpanded] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const [passes, setPasses] = useState([1, 2, 3, 4]);
  const [limit, setLimit] = useState('');
  const logRef = useRef(null);
  const c = COLOR[task.color];
  const Icon = task.icon;
  const isMe = activeTask === task.id;
  const myLogs = isMe ? logs : [];
  const lastLog = myLogs[myLogs.length - 1];
  const isDone = lastLog?.done;
  const exitOk = isDone && lastLog?.exit_code === 0;

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [myLogs.length]);

  // Auto-expand log when task starts/finishes
  useEffect(() => {
    if (isMe && myLogs.length > 0) setExpanded(true);
  }, [isMe, myLogs.length]);

  function togglePass(id) {
    setPasses((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id].sort()
    );
  }

  return (
    <Card className={cn('overflow-hidden border', c.border, 'bg-white/5')}>
      <div className="p-5 space-y-4">
        {/* Header */}
        <div className="flex items-start gap-4">
          <div className={cn('p-2.5 rounded-xl', c.badge)}>
            <Icon className={cn('w-5 h-5', c.icon)} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="font-semibold text-gray-100">{task.title}</h3>
              {isMe && running && (
                <Badge className="bg-blue-500/20 text-blue-300 text-[10px]">
                  <Loader2 className="w-3 h-3 mr-1 animate-spin inline" />
                  Running
                </Badge>
              )}
              {isDone && (
                <Badge className={exitOk ? 'bg-emerald-500/20 text-emerald-300 text-[10px]' : 'bg-red-500/20 text-red-300 text-[10px]'}>
                  {exitOk ? '✓ Done' : '✗ Failed'}
                </Badge>
              )}
            </div>
            <p className="text-xs text-gray-400 mt-1">{task.description}</p>
          </div>
        </div>

        {/* Options */}
        <div className="flex flex-wrap gap-4 items-center">
          {/* Dry-run toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <div
              onClick={() => setDryRun((v) => !v)}
              className={cn(
                'w-9 h-5 rounded-full transition-colors relative',
                dryRun ? 'bg-amber-500' : 'bg-gray-700'
              )}
            >
              <div className={cn(
                'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform shadow',
                dryRun ? 'translate-x-4' : 'translate-x-0.5'
              )} />
            </div>
            <span className="text-xs text-gray-400">Dry run</span>
          </label>

          {/* Backfill-only: pass selection */}
          {task.passes && (
            <div className="flex flex-wrap gap-1.5">
              {task.passes.map((p) => (
                <button
                  key={p.id}
                  onClick={() => togglePass(p.id)}
                  className={cn(
                    'px-2 py-0.5 rounded text-[11px] border transition-all',
                    passes.includes(p.id)
                      ? 'border-amber-500 bg-amber-500/15 text-amber-300'
                      : 'border-gray-700 text-gray-500 hover:border-gray-500'
                  )}
                  title={p.label}
                >
                  P{p.id}
                </button>
              ))}
            </div>
          )}

          {/* Backfill-only: limit */}
          {task.passes && (
            <input
              type="number"
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              placeholder="Limit (0=all)"
              className="w-28 h-7 px-2 text-xs rounded-lg bg-white/5 border border-gray-700 text-gray-300 placeholder:text-gray-600 focus:outline-none focus:border-amber-500"
            />
          )}
        </div>

        {/* Run button */}
        <Button
          onClick={() => onRun(task.id, { dry_run: dryRun, passes, limit: parseInt(limit) || 0 })}
          disabled={running || (task.passes && passes.length === 0)}
          className={cn('w-full', c.btn, 'text-white disabled:opacity-40')}
          size="sm"
        >
          {isMe && running ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Running…
            </>
          ) : (
            <>
              <Play className="w-4 h-4 mr-2" />
              {dryRun ? 'Preview (dry run)' : 'Run now'}
            </>
          )}
        </Button>

        {/* Log panel */}
        <AnimatePresence>
          {myLogs.length > 0 && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
            >
              <div className="border border-gray-700/60 rounded-lg overflow-hidden">
                <button
                  onClick={() => setExpanded((v) => !v)}
                  className="w-full flex items-center justify-between px-3 py-2 bg-gray-900/60 hover:bg-gray-800/60 transition-colors"
                >
                  <span className="text-xs text-gray-400 font-medium">
                    Output log ({myLogs.length} lines)
                  </span>
                  <div className="flex items-center gap-2">
                    {isDone && (
                      exitOk
                        ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                        : <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
                    )}
                    {expanded
                      ? <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
                      : <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
                    }
                  </div>
                </button>
                <AnimatePresence>
                  {expanded && (
                    <motion.div
                      initial={{ height: 0 }}
                      animate={{ height: 'auto' }}
                      exit={{ height: 0 }}
                      transition={{ duration: 0.15 }}
                    >
                      <div
                        ref={logRef}
                        className="max-h-60 overflow-y-auto bg-gray-950/80 p-3 space-y-0.5"
                      >
                        {myLogs.map((entry, i) => (
                          <LogLine key={i} line={entry.line} />
                        ))}
                        {isMe && running && (
                          <div className="flex items-center gap-1.5 pt-1">
                            <Loader2 className="w-3 h-3 text-blue-400 animate-spin" />
                            <span className="text-xs text-blue-400">Running…</span>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </Card>
  );
}

export default function MaintenancePage() {
  const { maintenanceLogs, maintenanceRunning, startMaintenance, stopMaintenance, clearMaintenanceLogs } = useSocket();
  const [activeTask, setActiveTask] = useState(null);
  const [error, setError] = useState('');

  async function handleRun(taskId, opts) {
    setError('');
    clearMaintenanceLogs();
    setActiveTask(taskId);
    startMaintenance(taskId);
    try {
      await api.runMaintenance(taskId, opts);
    } catch (err) {
      setError(err.message || 'Failed to start task');
      setActiveTask(null);
      stopMaintenance();
      clearMaintenanceLogs();
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-10">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="space-y-1"
      >
        <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent">
          Maintenance
        </h1>
        <p className="text-gray-400">One-shot tools for library health and Gemini enrichment</p>
      </motion.div>

      {/* Running notice */}
      <AnimatePresence>
        {maintenanceRunning && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="flex items-center gap-3 px-4 py-3 rounded-xl bg-blue-500/10 border border-blue-500/20"
          >
            <Loader2 className="w-4 h-4 text-blue-400 animate-spin flex-shrink-0" />
            <span className="text-sm text-blue-300">
              Task running — output is streaming below
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="flex items-center gap-3 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/20"
          >
            <AlertTriangle className="w-4 h-4 text-red-400 flex-shrink-0" />
            <span className="text-sm text-red-300">{error}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Task cards */}
      <div className="space-y-4">
        {TASKS.map((task, idx) => (
          <motion.div
            key={task.id}
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: idx * 0.08 }}
          >
            <TaskCard
              task={task}
              activeTask={activeTask}
              onRun={handleRun}
              running={maintenanceRunning}
              logs={maintenanceLogs}
            />
          </motion.div>
        ))}
      </div>

      {/* Tip */}
      <p className="text-xs text-gray-600 text-center">
        Run <strong className="text-gray-500">Reorganise → Repair Index → Backfill Gemini</strong> in order for best results.
        All tasks are idempotent — safe to re-run.
      </p>
    </div>
  );
}
