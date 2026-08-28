import { useEffect, useState, useCallback } from 'react';
import { motion } from 'framer-motion';
import {
  BarChart3, Download, CheckCircle2, HardDrive, Users,
  XCircle, RotateCw, Loader2, TrendingUp, Music, Database,
  Zap, AlertTriangle, Calendar, Star,
} from 'lucide-react';
import {
  LineChart, Line, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Button } from '@/components/ui/button';
import { useToast } from '@/components/ui/toast';
import { api } from '@/services/api';
import { ease } from '@/lib/motion';

// Chart color palette using token hex values
const CHART = {
  emerald: '#10B981',
  violet:  '#8B5CF6',
  cyan:    '#06B6D4',
  amber:   '#F59E0B',
  rose:    '#F43F5E',
  slate:   '#64748B',
  grid:    '#17171F',
  axis:    '#2D2D3A',
  tick:    '#555566',
  tick2:   '#888899',
};

const PIE_COLORS = [CHART.emerald, CHART.cyan, CHART.slate, CHART.amber, CHART.rose, CHART.violet];

const PLATFORM_COLORS = {
  youtube:    CHART.emerald,
  soundcloud: CHART.cyan,
  unknown:    CHART.slate,
};

const ERROR_TYPE_COLORS = {
  network:          CHART.cyan,
  metadata_missing: CHART.amber,
  format_invalid:   CHART.rose,
  rate_limit:       CHART.violet,
};

function Skeleton({ className = '' }) {
  return <div className={`shimmer rounded-xl ${className}`} />;
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border-default)',
      borderRadius: 3, padding: '8px 10px',
    }}>
      <p style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 4, fontFamily: 'monospace' }}>{label}</p>
      {payload.map((entry, i) => (
        <p key={i} style={{ fontSize: 11, color: entry.color }}>
          {entry.name}: <span style={{ fontFamily: 'monospace', fontWeight: 700 }}>{entry.value}</span>
        </p>
      ))}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, accent, loading }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={ease}
      className="stat-card"
    >
      <Icon className="stat-ic" style={{ width: 22, height: 22, color: accent }} />
      <div className="stat-lbl text-label">{label}</div>
      {loading ? (
        <Skeleton className="h-6 w-16 mt-1" />
      ) : (
        <div className="stat-val" style={{ color: accent }}>{value}</div>
      )}
    </motion.div>
  );
}

function ChartCard({ title, icon: Icon, accent, children, action }) {
  return (
    <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '10px 16px', borderBottom: '1px solid var(--border-subtle)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          {Icon && <Icon style={{ width: 12, height: 12, color: accent || 'var(--accent-violet)' }} />}
          <h3 style={{
            fontSize: 11, fontWeight: 700, letterSpacing: '0.07em',
            textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace',
            color: 'var(--text-secondary)',
          }}>{title}</h3>
        </div>
        {action}
      </div>
      <div style={{ padding: '16px 16px' }}>{children}</div>
    </div>
  );
}

function EmptyChart({ icon: Icon = BarChart3, message }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: 200 }}>
      <Icon style={{ width: 18, height: 18, color: 'var(--text-muted)', marginBottom: 8 }} />
      <p style={{ fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'ui-monospace, monospace', letterSpacing: '0.04em' }}>{message}</p>
    </div>
  );
}

export default function Analytics() {
  const { addToast } = useToast();

  const [overview, setOverview] = useState(null);
  const [perDay, setPerDay] = useState([]);
  const [topArtists, setTopArtists] = useState([]);
  const [sourceBreakdown, setSourceBreakdown] = useState([]);
  const [taggingBreakdown, setTaggingBreakdown] = useState([]);
  const [recentDownloads, setRecentDownloads] = useState([]);
  const [failedDownloads, setFailedDownloads] = useState([]);
  const [cacheAnalytics, setCacheAnalytics] = useState(null);
  const [failureSummary, setFailureSummary] = useState(null);
  const [weeklyStats, setWeeklyStats] = useState(null);
  const [dayRange, setDayRange] = useState(30);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState(false);
  const [retrying, setRetrying] = useState({});

  const fetchAll = useCallback(async () => {
    setFetchError(false);
    try {
      const [ov, pd, ta, sb, tb, rd, fd, ca, fs, ws] = await Promise.all([
        api.getAnalyticsOverview(),
        api.getAnalyticsDownloadsPerDay(dayRange),
        api.getAnalyticsTopArtists(),
        api.getAnalyticsSourceBreakdown(),
        api.getAnalyticsTaggingBreakdown(),
        api.getAnalyticsRecent(),
        api.getAnalyticsFailed(),
        api.getCacheAnalytics(),
        api.getTaggingFailuresSummary(),
        api.getDownloadHistoryStats(),
      ]);
      setOverview(ov);
      setPerDay(pd);
      setTopArtists(ta);
      setSourceBreakdown(sb);
      setTaggingBreakdown(tb);
      setRecentDownloads(rd);
      setFailedDownloads(fd);
      setCacheAnalytics(ca);
      setFailureSummary(fs);
      setWeeklyStats(ws);
    } catch {
      setFetchError(true);
    } finally {
      setLoading(false);
    }
  }, [dayRange]);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 60000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  useEffect(() => {
    api.getAnalyticsDownloadsPerDay(dayRange).then(setPerDay).catch(() => {});
  }, [dayRange]);

  async function handleRetry(trackId, idx) {
    setRetrying(prev => ({ ...prev, [idx]: true }));
    try {
      await api.retryDownload(trackId);
      addToast({ type: 'success', title: 'Retry Queued', description: 'Track re-queued for download' });
      setFailedDownloads(prev => prev.filter((_, i) => i !== idx));
    } catch {
      addToast({ type: 'error', title: 'Retry Failed', description: 'Could not re-queue the track' });
    } finally {
      setRetrying(prev => ({ ...prev, [idx]: false }));
    }
  }

  function formatTime(isoStr) {
    if (!isoStr) return '—';
    try {
      const d = new Date(isoStr);
      const diff = Math.floor((Date.now() - d) / 1000);
      if (diff < 60) return `${diff}s ago`;
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      return d.toLocaleDateString();
    } catch { return isoStr; }
  }

  return (
    <div className="max-w-5xl mx-auto space-y-5 pb-10">

      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl flex items-center justify-center"
               style={{ background: 'var(--accent-violet-dim)' }}>
            <BarChart3 className="w-4.5 h-4.5" style={{ color: 'var(--accent-violet)' }} />
          </div>
          <div>
            <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
              Analytics
            </h1>
            <p className="text-11 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
              Library statistics & insights
            </p>
          </div>
        </div>
        <Button variant="ghost" size="sm"
                onClick={() => { setLoading(true); setFetchError(false); fetchAll(); }}>
          <RotateCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </motion.div>

      {/* Error banner */}
      {fetchError && !loading && (
        <motion.div
          initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
          className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl"
          style={{ background: 'var(--accent-rose-dim)', border: '1px solid rgba(244,63,94,0.2)' }}
        >
          <div className="flex items-center gap-2.5">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" style={{ color: 'var(--accent-rose)' }} />
            <span className="text-13" style={{ color: 'var(--text-secondary)' }}>
              Failed to load analytics — backend may be unreachable.
            </span>
          </div>
          <Button size="sm" variant="ghost"
                  onClick={() => { setLoading(true); setFetchError(false); fetchAll(); }}
                  style={{ color: 'var(--accent-rose)', flexShrink: 0 }}>
            <RotateCw className="w-3.5 h-3.5 mr-1" /> Retry
          </Button>
        </motion.div>
      )}

      {/* KPI strip */}
      <div className="grid-stat">
        <StatCard icon={Download}    label="Total Downloads" value={overview?.total_downloads ?? '—'}                     accent="var(--accent-emerald)" loading={loading} />
        <StatCard icon={TrendingUp}  label="Success Rate"    value={overview ? `${overview?.success_rate ?? '—'}%` : '—'} accent="var(--accent-cyan)"    loading={loading} />
        <StatCard icon={HardDrive}   label="MB Cached"        value={overview?.musicbrainz_cached ?? '—'}                  accent="var(--accent-amber)"   loading={loading} />
        <StatCard icon={Users}       label="Total Artists"    value={overview?.total_artists ?? '—'}                       accent="var(--accent-violet)"  loading={loading} />
      </div>

      {/* Downloads per day */}
      <ChartCard
        title="Downloads Per Day"
        icon={Download}
        accent={CHART.emerald}
        action={
          <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border-subtle)' }}>
            {[7, 30, 90].map(d => {
              const active = dayRange === d;
              return (
                <button
                  key={d}
                  onClick={() => setDayRange(d)}
                  style={{
                    padding: '4px 10px', border: 'none', cursor: 'pointer',
                    background: 'transparent',
                    fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
                    fontFamily: 'ui-monospace, monospace',
                    color: active ? 'var(--accent-emerald)' : 'var(--text-muted)',
                    borderBottom: active ? '2px solid var(--accent-emerald)' : '2px solid transparent',
                    marginBottom: -1, transition: 'color 0.15s, border-color 0.15s',
                  }}
                >
                  {d}D
                </button>
              );
            })}
          </div>
        }
      >
        {loading ? (
          <Skeleton className="h-64 w-full" />
        ) : perDay.length === 0 ? (
          <EmptyChart icon={BarChart3} message="No download data yet" />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={perDay}>
              <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} />
              <XAxis dataKey="date" tick={{ fill: CHART.tick, fontSize: 11 }} tickFormatter={v => v.slice(5)} stroke={CHART.axis} />
              <YAxis tick={{ fill: CHART.tick, fontSize: 11 }} stroke={CHART.axis} allowDecimals={false} />
              <Tooltip content={<CustomTooltip />} />
              <Line type="monotone" dataKey="count" name="Downloads" stroke={CHART.emerald} strokeWidth={2}
                    dot={{ fill: CHART.emerald, r: 3 }} activeDot={{ r: 5, fill: CHART.emerald }} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </ChartCard>

      {/* Top artists + Source breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Top Artists" icon={Music} accent={CHART.violet}>
          {loading ? (
            <Skeleton className="h-64 w-full" />
          ) : topArtists.length === 0 ? (
            <EmptyChart icon={Music} message="No artist data yet" />
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={topArtists} layout="vertical" margin={{ left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} horizontal={false} />
                <XAxis type="number" tick={{ fill: CHART.tick, fontSize: 11 }} stroke={CHART.axis} allowDecimals={false} />
                <YAxis type="category" dataKey="artist" tick={{ fill: CHART.tick2, fontSize: 11 }} width={100} stroke={CHART.axis} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="count" name="Downloads" fill={CHART.violet} radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </ChartCard>

        <ChartCard title="Source Breakdown" icon={BarChart3} accent={CHART.cyan}>
          {loading ? (
            <Skeleton className="h-64 w-full" />
          ) : sourceBreakdown.length === 0 ? (
            <EmptyChart message="No source data yet" />
          ) : (
            <div className="flex flex-col items-center">
              <ResponsiveContainer width="100%" height={220}>
                <PieChart>
                  <Pie data={sourceBreakdown} dataKey="count" nameKey="platform"
                       cx="50%" cy="50%" outerRadius={80} innerRadius={45}
                       strokeWidth={0} paddingAngle={3}>
                    {sourceBreakdown.map((entry, i) => (
                      <Cell key={entry.platform} fill={PLATFORM_COLORS[entry.platform] || PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                  <Legend formatter={v => <span style={{ color: CHART.tick2, fontSize: 11 }}>{v}</span>} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>
      </div>

      {/* Recent + Failed tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Recent downloads */}
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="flex items-center gap-2 px-5 py-4"
               style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <Download className="w-4 h-4" style={{ color: CHART.emerald }} />
            <h3 className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>Recent Downloads</h3>
          </div>
          <ScrollArea className="max-h-[360px]">
            {loading ? (
              <div className="p-4 space-y-2">
                {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
              </div>
            ) : recentDownloads.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12">
                <Download className="w-7 h-7 mb-2" style={{ color: 'var(--text-muted)' }} />
                <p className="text-12" style={{ color: 'var(--text-tertiary)' }}>No downloads yet</p>
              </div>
            ) : (
              <div className="px-2 pb-2">
                {recentDownloads.map((item, i) => (
                  <div key={item._id || i} className="tbl-row">
                    <div className="tbl-thumb" style={{ width: 32, height: 32, background: item.tagging_report ? 'linear-gradient(135deg, rgba(16,185,129,0.2), rgba(16,185,129,0.06))' : 'var(--glass-default)' }}>
                      {item.tagging_report
                        ? <CheckCircle2 className="w-3.5 h-3.5" style={{ color: CHART.emerald }} />
                        : <XCircle className="w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                      }
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="tbl-name truncate">{item.track_title || '—'}</p>
                      <p className="tbl-artist truncate">{item.artist || '—'}</p>
                    </div>
                    <span className="tbl-badge flex-shrink-0 hidden sm:inline-flex">{item.source_platform || '—'}</span>
                    <span className="text-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {formatTime(item.downloaded_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>

        {/* Failed downloads */}
        <div className="glass-card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="flex items-center justify-between px-5 py-4"
               style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <div className="flex items-center gap-2">
              <XCircle className="w-4 h-4" style={{ color: CHART.rose }} />
              <h3 className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>Failed Downloads</h3>
            </div>
            {failedDownloads.length > 0 && (
              <span className="tbl-badge badge-rose">
                {failedDownloads.length}
              </span>
            )}
          </div>
          <ScrollArea className="max-h-[360px]">
            {loading ? (
              <div className="p-4 space-y-2">
                {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10 w-full" />)}
              </div>
            ) : failedDownloads.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12">
                <CheckCircle2 className="w-7 h-7 mb-2" style={{ color: CHART.emerald }} />
                <p className="text-12" style={{ color: 'var(--text-tertiary)' }}>No failures — great!</p>
              </div>
            ) : (
              <div className="px-2 pb-2">
                {failedDownloads.map((item, i) => (
                  <div key={item._id || i} className="tbl-row">
                    <div className="tbl-thumb" style={{ width: 32, height: 32, background: 'linear-gradient(135deg, rgba(244,63,94,0.2), rgba(244,63,94,0.06))' }}>
                      <XCircle className="w-3.5 h-3.5" style={{ color: CHART.rose }} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="tbl-name truncate">{item.title || '—'}</p>
                      <p className="truncate" style={{ fontSize: 11, marginTop: 1, color: 'var(--accent-rose)' }}>
                        {item.error || '—'}
                      </p>
                    </div>
                    <span className="text-mono flex-shrink-0" style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                      {formatTime(item.timestamp)}
                    </span>
                    <button
                      disabled={retrying[i]}
                      onClick={() => handleRetry(item.track_id, i)}
                      className="btn-s flex-shrink-0"
                      style={{ padding: '4px 10px', fontSize: 11 }}
                    >
                      {retrying[i]
                        ? <Loader2 className="w-3 h-3 animate-spin inline" />
                        : <RotateCw className="w-3 h-3 inline" />
                      }
                      {' '}Retry
                    </button>
                  </div>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>
      </div>

      {/* Tagging breakdown */}
      <ChartCard title="Tagging Breakdown" icon={Database} accent={CHART.amber}>
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : (
          <div className="grid grid-cols-3 gap-4">
            {(taggingBreakdown ?? []).map(item => (
              <div key={item.source}
                   className="flex flex-col items-center rounded-xl py-4"
                   style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <span className="font-display text-28 font-bold tabular-nums" style={{ color: 'var(--accent-amber)', letterSpacing: '-0.02em' }}>
                  {item.count}
                </span>
                <span className="text-11 mt-1" style={{ color: 'var(--text-tertiary)' }}>{item.source}</span>
              </div>
            ))}
          </div>
        )}
      </ChartCard>

      {/* This Week */}
      <ChartCard title="This Week" icon={Calendar} accent={CHART.cyan}>
        {loading || !weeklyStats ? (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="flex flex-col items-center justify-center rounded-xl py-5"
                 style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
              <Download className="w-5 h-5 mb-2" style={{ color: CHART.emerald }} />
              <span className="font-display text-28 font-bold tabular-nums" style={{ color: CHART.emerald, letterSpacing: '-0.02em' }}>
                {weeklyStats?.total_this_week ?? '—'}
              </span>
              <span className="text-11 mt-1" style={{ color: 'var(--text-tertiary)' }}>Downloads</span>
            </div>
            <div className="flex flex-col items-center justify-center rounded-xl py-5"
                 style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
              <CheckCircle2 className="w-5 h-5 mb-2" style={{ color: CHART.cyan }} />
              <span className="font-display text-28 font-bold tabular-nums" style={{ color: CHART.cyan, letterSpacing: '-0.02em' }}>
                {weeklyStats?.success_rate ?? '—'}%
              </span>
              <span className="text-11 mt-1" style={{ color: 'var(--text-tertiary)' }}>Tagged</span>
            </div>
            <div className="rounded-xl p-4"
                 style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
              <div className="flex items-center gap-1.5 mb-3">
                <Star className="w-3.5 h-3.5" style={{ color: CHART.amber }} />
                <span className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>Top Artists</span>
              </div>
              {(weeklyStats?.top_artists ?? []).length === 0 ? (
                <p className="text-11 text-center" style={{ color: 'var(--text-muted)' }}>No data yet</p>
              ) : (
                <ol className="space-y-1.5">
                  {(weeklyStats?.top_artists ?? []).map((a, i) => (
                    <li key={a.artist} className="flex items-center gap-2">
                      <span className="text-10 font-mono w-4 flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{i + 1}.</span>
                      <span className="text-11 truncate flex-1" style={{ color: 'var(--text-primary)' }}>{a.artist}</span>
                      <span className="text-10 font-mono px-1.5 py-0.5 rounded-full flex-shrink-0"
                            style={{ background: 'var(--surface-2)', color: 'var(--text-tertiary)' }}>{a.count}</span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        )}
      </ChartCard>

      {/* MusicBrainz Cache */}
      <ChartCard title="MusicBrainz Cache" icon={Database} accent={CHART.amber}>
        {loading || !cacheAnalytics ? (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-28 w-full" />)}
          </div>
        ) : (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Hit rate donut */}
            <div className="flex flex-col items-center rounded-xl py-4"
                 style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
              {Number.isFinite(cacheAnalytics.cache_hit_rate) && cacheAnalytics.cache_hit_rate >= 0 && cacheAnalytics.cache_hit_rate <= 100 ? (
                <div className="relative w-20 h-20">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={[{ value: cacheAnalytics.cache_hit_rate }, { value: 100 - cacheAnalytics.cache_hit_rate }]}
                           cx="50%" cy="50%" innerRadius={26} outerRadius={36}
                           startAngle={90} endAngle={-270} strokeWidth={0}>
                        <Cell fill={CHART.amber} />
                        <Cell fill={CHART.grid} />
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <span className="absolute inset-0 flex items-center justify-center text-12 font-bold font-mono"
                        style={{ color: CHART.amber }}>
                    {cacheAnalytics.cache_hit_rate}%
                  </span>
                </div>
              ) : (
                <div className="flex h-20 w-20 flex-col items-center justify-center rounded-full"
                     style={{ border: '1px solid rgba(244,63,94,0.3)', background: 'var(--accent-rose-dim)' }}>
                  <AlertTriangle className="h-5 w-5" style={{ color: 'var(--accent-rose)' }} />
                </div>
              )}
              <span className="text-11 mt-2" style={{ color: 'var(--text-tertiary)' }}>Hit Rate</span>
            </div>
            {[
              { icon: Zap, value: cacheAnalytics.total_cached_tracks, label: 'Cached Tracks', color: CHART.amber },
              { icon: HardDrive, value: `${cacheAnalytics.avg_age_days}d`, label: 'Avg Age', color: 'var(--text-secondary)' },
              { icon: TrendingUp, value: cacheAnalytics.api_calls_saved, label: 'API Calls Saved', color: CHART.emerald },
            ].map(({ icon: Icon, value, label, color }) => (
              <div key={label} className="flex flex-col items-center justify-center rounded-xl py-4"
                   style={{ background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <Icon className="w-5 h-5 mb-2" style={{ color }} />
                <span className="font-display text-28 font-bold tabular-nums" style={{ color, letterSpacing: '-0.02em' }}>{value}</span>
                <span className="text-11 mt-1" style={{ color: 'var(--text-tertiary)' }}>{label}</span>
              </div>
            ))}
          </div>
        )}
      </ChartCard>

      {/* Failure Types + Retry Trend */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <ChartCard title="Failure Types" icon={AlertTriangle} accent={CHART.rose}>
          {loading || !failureSummary ? (
            <Skeleton className="h-52 w-full" />
          ) : failureSummary.by_error_type.length === 0 ? (
            <EmptyChart icon={CheckCircle2} message="No failures recorded" />
          ) : (
            <div className="flex flex-col items-center">
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie data={failureSummary.by_error_type} dataKey="count" nameKey="error_type"
                       cx="50%" cy="50%" outerRadius={70} innerRadius={38}
                       strokeWidth={0} paddingAngle={3}>
                    {failureSummary.by_error_type.map((entry, i) => (
                      <Cell key={entry.error_type} fill={ERROR_TYPE_COLORS[entry.error_type] || PIE_COLORS[i % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip content={<CustomTooltip />} />
                  <Legend formatter={v => <span style={{ color: CHART.tick2, fontSize: 11 }}>{v}</span>} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </ChartCard>

        <ChartCard title="Retry Trend (7d)" icon={RotateCw} accent={CHART.amber}>
          {loading || !failureSummary ? (
            <Skeleton className="h-28 w-full" />
          ) : failureSummary.retry_trend.length === 0 ? (
            <div className="flex items-center justify-center h-28 text-12" style={{ color: 'var(--text-tertiary)' }}>
              No retries in the last 7 days
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={110}>
              <BarChart data={failureSummary.retry_trend} margin={{ left: -10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={CHART.grid} />
                <XAxis dataKey="date" tick={{ fill: CHART.tick, fontSize: 10 }} tickFormatter={v => v.slice(5)} stroke={CHART.axis} />
                <YAxis tick={{ fill: CHART.tick, fontSize: 10 }} stroke={CHART.axis} allowDecimals={false} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="retries" name="Retries" fill={CHART.amber} radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
          {failureSummary?.recent_failures?.length > 0 && (
            <div className="mt-4 space-y-1.5">
              <p className="text-11 font-medium mb-2" style={{ color: 'var(--text-muted)' }}>Recent</p>
              {failureSummary.recent_failures.slice(0, 5).map((f, i) => (
                <div key={i} className="flex items-center justify-between gap-2 rounded-lg px-3 py-2"
                     style={{ background: 'var(--surface-1)' }}>
                  <div className="min-w-0">
                    <p className="text-11 truncate" style={{ color: 'var(--text-primary)' }}>{f.title || '—'}</p>
                    <p className="text-10 truncate" style={{ color: 'var(--text-muted)' }}>{f.artist || ''}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="text-10 font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide"
                          style={{ background: 'var(--surface-2)', color: ERROR_TYPE_COLORS[f.error_type] || 'var(--text-tertiary)' }}>
                      {f.error_type || 'unknown'}
                    </span>
                    <span className="text-10 font-mono" style={{ color: 'var(--text-muted)' }}>{formatTime(f.timestamp)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ChartCard>
      </div>
    </div>
  );
}
