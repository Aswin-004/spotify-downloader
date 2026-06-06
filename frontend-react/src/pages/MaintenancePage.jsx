import { useState, useEffect, useRef, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderSync, DatabaseZap, Sparkles, Tags,
  Play, AlertTriangle, CheckCircle2, Loader2, RefreshCw,
  ChevronRight, ChevronDown, Code2,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useSocket } from '@/hooks/useSocket';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';
import { cn } from '@/lib/utils';

const TASKS = [
  {
    id: 'organise',
    icon: FolderSync,
    title: 'Re-sort Music into Folders',
    description: 'Tidies your library: splits mixed folders, removes near-empty ones, cleans up duplicates, and moves any "Needs Sorting" tracks into proper genre folders.',
    accent: 'var(--accent-violet)',
    dim: 'var(--accent-violet-dim)',
    border: 'rgba(139,92,246,0.2)',
    steps: ['Split mixed genre folders', 'Remove near-empty folders', 'Remove duplicates', 'Clean up numbered rips', 'Sort pending tracks'],
  },
  {
    id: 'repair_index',
    icon: DatabaseZap,
    title: 'Fix Library Scan',
    description: 'Rescans your music folder to fix any tracks that appear missing or out of place. Run this if files seem to be re-downloading even though you already have them.',
    accent: 'var(--accent-cyan)',
    dim: 'var(--accent-cyan-dim)',
    border: 'rgba(6,182,212,0.2)',
    steps: ['Scan music folder', 'Fix broken file paths', 'Register untracked files'],
  },
  {
    id: 'backfill_gemini',
    icon: Sparkles,
    title: 'Re-classify Undetected Genres',
    description: 'Uses AI to detect the genre of tracks that ended up in the Unclassified folder, add BPM and musical key info, and fetch missing album artwork.',
    accent: 'var(--accent-amber)',
    dim: 'var(--accent-amber-dim)',
    border: 'rgba(245,158,11,0.2)',
    passes: [
      { id: 1, label: 'Step 1 — Detect genres for Indian/Bollywood tracks (AI classifier, ~2s per track)' },
      { id: 2, label: 'Step 2 — Move tracks with a detected genre into the right folder (rule-based, fast)' },
      { id: 3, label: 'Step 3 — Fill in missing genre tags for remaining unknowns (AI classifier, ~2s per track)' },
      { id: 4, label: 'Step 4 — Add BPM + musical key via audio analysis (~3s per track)' },
      { id: 5, label: 'Step 5 — Fetch missing album artwork from MusicBrainz/Spotify (~1s per track)' },
    ],
  },
  {
    id: 'backfill_lastfm',
    icon: Tags,
    title: 'Enrich Genre & Mood Tags (Last.fm)',
    description: 'Fetches community genre, mood, and listener data from Last.fm for all library tracks. Runs in batches of 200 — re-run until complete. Safe to run while downloads are active.',
    accent: 'var(--accent-emerald)',
    dim: 'var(--accent-emerald-dim)',
    border: 'rgba(16,185,129,0.2)',
    hasLimit: true,
  },
];

function LogLine({ line }) {
  const isError  = /\[(?:error|fail|warn)\]|(?:error|fail|warn(?:ing)?):|✗/i.test(line);
  const isDone   = /✓|done|exit 0/i.test(line);
  const isHeader = line.startsWith('===') || line.startsWith('▶');
  const color = isError
    ? 'var(--accent-rose)'
    : isDone
      ? 'var(--accent-emerald)'
      : isHeader
        ? 'var(--accent-violet)'
        : 'var(--text-secondary)';
  return (
    <div className="font-mono text-11 leading-5 px-1" style={{ color }}>
      {line}
    </div>
  );
}

function TaskCard({ task, activeTask, onRun, running, logs, devMode }) {
  const [expanded, setExpanded] = useState(false);
  const [dryRun, setDryRun] = useState(false);
  const defaultPasses = task.passes
    ? (() => {
        const max = Math.max(...task.passes.map(p => p.id));
        return task.passes.filter(p => p.id < max).map(p => p.id);
      })()
    : [];
  const [passes, setPasses] = useState(defaultPasses);
  const [limit, setLimit] = useState('');
  const logRef = useRef(null);
  const Icon = task.icon;
  const isMe    = useMemo(() => activeTask === task.id, [activeTask, task.id]);
  const myLogs  = useMemo(() => logs.filter(e => e.task === task.id), [logs, task.id]);
  const lastLog = useMemo(() => myLogs[myLogs.length - 1], [myLogs]);
  const isDone  = useMemo(() => Boolean(lastLog?.done), [lastLog]);
  const exitOk  = useMemo(() => isDone && lastLog?.exit_code === 0, [isDone, lastLog]);
  // Last few meaningful lines shown in simple-mode summary after completion
  const summaryLines = useMemo(() => {
    if (!isDone || myLogs.length < 2) return [];
    return myLogs
      .slice(0, -1)   // exclude the "✓ Done" line itself
      .slice(-4)      // last 4 output lines
      .map(e => e.line)
      .filter(l => l && !l.startsWith('▶ Started') && !l.startsWith('Starting '));
  }, [isDone, myLogs]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [myLogs.length]);

  useEffect(() => {
    if (myLogs.length > 0) setExpanded(true);
  }, [myLogs.length]);

  function togglePass(id) {
    setPasses(prev => prev.includes(id) ? prev.filter(p => p !== id) : [...prev, id].sort());
  }

  const isRunningMe = isMe && running;

  return (
    <motion.div
      style={{
        background: 'var(--surface-0)',
        border: `1px solid ${isRunningMe ? task.border : 'var(--border-subtle)'}`,
        borderLeft: `3px solid ${isRunningMe ? task.accent : task.border}`,
        borderRadius: 4, overflow: 'hidden',
        boxShadow: isRunningMe ? `0 0 12px ${task.accent}22` : 'none',
        transition: 'border-color 0.3s, box-shadow 0.3s',
      }}
    >
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {/* Header */}
        <div className="flex items-start gap-3">
          <Icon
            className={cn('w-4 h-4 flex-shrink-0 mt-0.5', isRunningMe && 'animate-spin')}
            style={{ color: task.accent }}
          />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap mb-1">
              <h3 style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)' }}>
                {task.title}
              </h3>
              {isRunningMe && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
                  padding: '2px 6px', borderRadius: 2,
                  background: 'var(--accent-cyan-dim)', color: 'var(--accent-cyan)',
                  fontFamily: 'ui-monospace, monospace', textTransform: 'uppercase',
                }}>
                  <Loader2 className="w-2.5 h-2.5 animate-spin" />
                  RUNNING
                </span>
              )}
              {isDone && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
                  padding: '2px 6px', borderRadius: 2,
                  fontFamily: 'ui-monospace, monospace', textTransform: 'uppercase',
                  background: exitOk ? 'var(--accent-emerald-dim)' : 'var(--accent-rose-dim)',
                  color: exitOk ? 'var(--accent-emerald)' : 'var(--accent-rose)',
                }}>
                  {exitOk ? <><CheckCircle2 className="w-2.5 h-2.5" /> DONE</> : <><AlertTriangle className="w-2.5 h-2.5" /> FAILED</>}
                </span>
              )}
            </div>
            <p style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{task.description}</p>
          </div>
        </div>

        {/* Options — developer mode only */}
        {devMode && (
          <div className="flex flex-wrap gap-3 items-center">
            {/* Dry-run toggle */}
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <div
                onClick={() => setDryRun(v => !v)}
                className="w-9 h-5 rounded-full relative transition-colors duration-200 cursor-pointer"
                style={{ background: dryRun ? 'var(--accent-amber)' : 'var(--surface-2)' }}
              >
                <div
                  className="absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 shadow"
                  style={{ transform: dryRun ? 'translateX(16px)' : 'translateX(2px)' }}
                />
              </div>
              <span className="text-11" style={{ color: 'var(--text-tertiary)' }}>Preview only (no changes)</span>
            </label>

            {/* Pass selection for backfill tasks */}
            {task.passes && (
              <div className="flex flex-wrap gap-1.5">
                {task.passes.map(p => (
                  <button
                    key={p.id}
                    onClick={() => togglePass(p.id)}
                    title={p.label}
                    className="px-2 py-0.5 rounded-lg text-11 transition-all duration-150 cursor-pointer focus-ring"
                    style={passes.includes(p.id)
                      ? { border: `1px solid ${task.accent}`, background: task.dim, color: task.accent }
                      : { border: '1px solid var(--border-subtle)', color: 'var(--text-muted)', background: 'transparent' }
                    }
                  >
                    Step {p.id}
                  </button>
                ))}
              </div>
            )}

            {/* Limit input */}
            {(task.passes || task.hasLimit) && (
              <input
                type="number"
                value={limit}
                onChange={e => setLimit(e.target.value)}
                placeholder="Limit (0=all)"
                className="w-28 h-7 px-2 text-11 rounded-lg focus-ring"
                style={{
                  background: 'var(--surface-1)',
                  border: '1px solid var(--border-default)',
                  color: 'var(--text-secondary)',
                }}
              />
            )}
          </div>
        )}

        {/* Run button */}
        <Button
          onClick={() => onRun(task.id, { dry_run: dryRun, passes, limit: Math.max(0, parseInt(limit) || 0) })}
          disabled={running || (task.passes && passes.length === 0)}
          className="w-full"
          size="sm"
        >
          {isRunningMe ? (
            <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Running…</>
          ) : (
            <><Play className="w-3.5 h-3.5" /> {dryRun ? 'Preview (dry run)' : 'Run now'}</>
          )}
        </Button>

        {/* Log / status panel */}
        <AnimatePresence>
          {myLogs.length > 0 && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              {devMode ? (
                /* Developer mode: full collapsible log */
                <div className="rounded-xl overflow-hidden"
                     style={{ border: '1px solid var(--border-subtle)' }}>
                  <button
                    onClick={() => setExpanded(v => !v)}
                    className="w-full flex items-center justify-between px-3 py-2 transition-colors duration-150 cursor-pointer focus-ring"
                    style={{ background: 'var(--surface-1)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'var(--surface-1)'}
                  >
                    <span className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>
                      Output log ({myLogs.length} lines)
                    </span>
                    <div className="flex items-center gap-2">
                      {isDone && (
                        exitOk
                          ? <CheckCircle2 className="w-3.5 h-3.5" style={{ color: 'var(--accent-emerald)' }} />
                          : <AlertTriangle className="w-3.5 h-3.5" style={{ color: 'var(--accent-rose)' }} />
                      )}
                      <motion.div animate={{ rotate: expanded ? 90 : 0 }} transition={{ duration: 0.15 }}>
                        <ChevronRight className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                      </motion.div>
                    </div>
                  </button>
                  <AnimatePresence>
                    {expanded && (
                      <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: 'auto' }}
                        exit={{ height: 0 }}
                        transition={{ duration: 0.15 }}
                        className="overflow-hidden"
                      >
                        <div
                          ref={logRef}
                          className="max-h-60 overflow-y-auto scrollbar-thin p-3 space-y-0.5"
                          style={{ background: 'var(--void)' }}
                        >
                          {myLogs.map((entry, i) => (
                            <LogLine key={i} line={entry.line} />
                          ))}
                          {isRunningMe && (
                            <div className="flex items-center gap-1.5 pt-1">
                              <Loader2 className="w-3 h-3 animate-spin" style={{ color: 'var(--accent-cyan)' }} />
                              <span className="text-11" style={{ color: 'var(--accent-cyan)' }}>Running…</span>
                            </div>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              ) : (
                /* Simple mode: just status summary */
                <div className="rounded-xl px-4 py-3 flex items-center gap-3"
                     style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                  {isRunningMe ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin flex-shrink-0" style={{ color: 'var(--accent-cyan)' }} />
                      <span className="text-12" style={{ color: 'var(--accent-cyan)' }}>
                        Running… ({myLogs.length} steps)
                      </span>
                    </>
                  ) : isDone ? (
                    exitOk ? (
                      <>
                        <CheckCircle2 className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-emerald)' }} />
                        <div className="flex-1 min-w-0">
                          <span className="text-12" style={{ color: 'var(--accent-emerald)' }}>Completed successfully</span>
                          {summaryLines.length > 0 && (
                            <div className="mt-1 space-y-0.5">
                              {summaryLines.map((line, i) => (
                                <div key={i} className="font-mono leading-4"
                                     style={{ fontSize: 10, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {line}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </>
                    ) : (
                      <>
                        <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-rose)' }} />
                        <div className="flex-1 min-w-0">
                          <span className="text-12" style={{ color: 'var(--accent-rose)' }}>
                            Finished with errors — enable Developer mode for details
                          </span>
                          {summaryLines.length > 0 && (
                            <div className="mt-1">
                              <div className="font-mono leading-4"
                                   style={{ fontSize: 10, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {summaryLines[summaryLines.length - 1]}
                              </div>
                            </div>
                          )}
                        </div>
                      </>
                    )
                  ) : null}
                </div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}

export default function MaintenancePage() {
  const { maintenanceLogs, maintenanceRunning, startMaintenance, stopMaintenance, clearMaintenanceLogs } = useSocket();
  const [activeTask, setActiveTask] = useState(null);
  const [error, setError] = useState('');
  const [cacheClearState, setCacheClearState] = useState('idle');
  const [devMode, setDevMode] = useState(() => {
    try { return localStorage.getItem('maintenance_devmode') === 'true'; } catch { return false; }
  });

  function toggleDevMode() {
    setDevMode(v => {
      const next = !v;
      try { localStorage.setItem('maintenance_devmode', String(next)); } catch {}
      return next;
    });
  }

  useEffect(() => {
    api.getMaintenanceStatus().then(s => {
      if (s?.running && s?.task) setActiveTask(s.task);
    }).catch(() => {});
  }, []);

  async function handleClearGenreCache() {
    setCacheClearState('loading');
    try {
      await api.clearGenreCache();
      setCacheClearState('done');
      setTimeout(() => setCacheClearState('idle'), 3000);
    } catch {
      setCacheClearState('error');
      setTimeout(() => setCacheClearState('idle'), 3000);
    }
  }

  async function handleRun(taskId, opts) {
    setError('');
    clearMaintenanceLogs(taskId);   // only clear THIS task's previous logs
    setActiveTask(taskId);
    startMaintenance(taskId);
    try {
      await api.runMaintenance(taskId, opts);
    } catch (err) {
      const safeMsg =
        err?.response?.data?.error ||
        err?.response?.data?.message ||
        (err?.response?.status ? `Server error (${err.response.status})` : 'Failed to start task');
      setError(safeMsg);
      setActiveTask(null);
      stopMaintenance();
      clearMaintenanceLogs(taskId);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-5 pb-10">

      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-start justify-between gap-4"
      >
        <div>
          <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Maintenance
          </h1>
          <p className="text-12 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
            Fix and improve your music library — run these occasionally to keep everything tidy
          </p>
        </div>

        {/* Developer mode toggle */}
        <button
          onClick={toggleDevMode}
          title={devMode ? 'Disable developer mode' : 'Enable developer mode (shows raw logs and advanced options)'}
          className="flex items-center gap-2 px-3 py-1.5 flex-shrink-0 transition-all duration-200 cursor-pointer focus-ring"
          style={devMode
            ? { borderRadius: 4, background: 'var(--accent-violet-dim)', border: '1px solid rgba(139,92,246,0.35)', color: 'var(--accent-violet)' }
            : { borderRadius: 4, background: 'var(--surface-1)', border: '1px solid var(--border-subtle)', color: 'var(--text-muted)' }
          }
        >
          <Code2 className="w-3.5 h-3.5" />
          <span className="text-11 font-medium">Dev mode</span>
        </button>
      </motion.div>

      {/* Running notice */}
      <AnimatePresence>
        {maintenanceRunning && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="flex items-center gap-3 px-4 py-3"
            style={{ borderRadius: 4, background: 'var(--accent-cyan-dim)', border: '1px solid rgba(6,182,212,0.2)' }}
          >
            <Loader2 className="w-4 h-4 animate-spin flex-shrink-0" style={{ color: 'var(--accent-cyan)' }} />
            <span className="text-13" style={{ color: 'var(--accent-cyan)' }}>
              Task running — output is streaming below
            </span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="flex items-center gap-3 px-4 py-3"
            style={{ borderRadius: 4, background: 'var(--accent-rose-dim)', border: '1px solid rgba(244,63,94,0.2)' }}
          >
            <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-rose)' }} />
            <span className="text-13" style={{ color: 'var(--text-secondary)' }}>{error}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Task cards */}
      <div className="space-y-3">
        {TASKS.map((task, idx) => (
          <motion.div
            key={task.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...ease, delay: idx * 0.07 }}
          >
            <TaskCard
              task={task}
              activeTask={activeTask}
              onRun={handleRun}
              running={maintenanceRunning}
              logs={maintenanceLogs}
              devMode={devMode}
            />
          </motion.div>
        ))}
      </div>

      {/* Quick Actions */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ ...ease, delay: 0.28 }}
        style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)', borderRadius: 4, padding: 18 }}
      >
        <h3 className="text-14 font-semibold mb-0.5" style={{ color: 'var(--text-primary)' }}>Quick Actions</h3>
        <p className="text-11 mb-4" style={{ color: 'var(--text-muted)' }}>One-click utilities that take effect immediately.</p>
        <div className="flex flex-wrap gap-3">
          <Button
            size="sm"
            variant="outline"
            disabled={cacheClearState === 'loading'}
            onClick={handleClearGenreCache}
            style={cacheClearState === 'done'
              ? { borderColor: 'var(--accent-emerald)', color: 'var(--accent-emerald)' }
              : cacheClearState === 'error'
                ? { borderColor: 'var(--accent-rose)', color: 'var(--accent-rose)' }
                : {}
            }
          >
            {cacheClearState === 'loading'
              ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
              : <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            }
            {cacheClearState === 'done'
              ? 'Reset ✓'
              : cacheClearState === 'error'
                ? 'Failed ✗'
                : 'Reset Genre Memory'}
          </Button>
        </div>
        <p className="text-11 mt-3" style={{ color: 'var(--text-muted)' }}>
          Use this if a track keeps going to the wrong folder — it clears the app's remembered genre guesses so they're looked up fresh next time.
        </p>
      </motion.div>

      {/* Tip */}
      <p className="text-11 text-center" style={{ color: 'var(--text-muted)' }}>
        For best results, run in order:{' '}
        <strong style={{ color: 'var(--text-tertiary)' }}>Re-sort → Fix Library Scan → Re-classify Genres</strong>.
        All tasks are safe to run multiple times.
      </p>
    </div>
  );
}
