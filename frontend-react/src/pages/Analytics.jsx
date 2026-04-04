// ANALYTICS — Complete Analytics Dashboard page
import { useEffect, useState, useCallback } from 'react'; // ANALYTICS
import { motion } from 'framer-motion'; // ANALYTICS
import { // ANALYTICS
  BarChart3, // ANALYTICS
  Download, // ANALYTICS
  CheckCircle2, // ANALYTICS
  HardDrive, // ANALYTICS
  Users, // ANALYTICS
  XCircle, // ANALYTICS
  RotateCw, // ANALYTICS
  Loader2, // ANALYTICS
  TrendingUp, // ANALYTICS
  Music, // ANALYTICS
} from 'lucide-react'; // ANALYTICS
import { // ANALYTICS
  LineChart, // ANALYTICS
  Line, // ANALYTICS
  BarChart, // ANALYTICS
  Bar, // ANALYTICS
  PieChart, // ANALYTICS
  Pie, // ANALYTICS
  Cell, // ANALYTICS
  XAxis, // ANALYTICS
  YAxis, // ANALYTICS
  CartesianGrid, // ANALYTICS
  Tooltip, // ANALYTICS
  ResponsiveContainer, // ANALYTICS
  Legend, // ANALYTICS
} from 'recharts'; // ANALYTICS
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'; // ANALYTICS
import { Button } from '@/components/ui/button'; // ANALYTICS
import { Badge } from '@/components/ui/badge'; // ANALYTICS
import { ScrollArea } from '@/components/ui/scroll-area'; // ANALYTICS
import { useToast } from '@/components/ui/toast'; // ANALYTICS
import { api } from '@/services/api'; // ANALYTICS
import { cn } from '@/lib/utils'; // ANALYTICS

// ANALYTICS — Skeleton loader component
function Skeleton({ className }) { // ANALYTICS
  return ( // ANALYTICS
    <div // ANALYTICS
      className={cn( // ANALYTICS
        'animate-pulse rounded-xl bg-surface-light/50', // ANALYTICS
        className // ANALYTICS
      )} // ANALYTICS
    /> // ANALYTICS
  ); // ANALYTICS
} // ANALYTICS

// ANALYTICS — Custom tooltip for recharts
function CustomTooltip({ active, payload, label }) { // ANALYTICS
  if (!active || !payload?.length) return null; // ANALYTICS
  return ( // ANALYTICS
    <div className="rounded-lg border border-border bg-surface p-3 shadow-xl"> {/* ANALYTICS */}
      <p className="text-xs font-medium text-gray-300 mb-1">{label}</p> {/* ANALYTICS */}
      {payload.map((entry, i) => ( // ANALYTICS
        <p key={i} className="text-xs" style={{ color: entry.color }}> {/* ANALYTICS */}
          {entry.name}: <span className="font-mono font-bold">{entry.value}</span> {/* ANALYTICS */}
        </p> // ANALYTICS
      ))} {/* ANALYTICS */}
    </div> // ANALYTICS
  ); // ANALYTICS
} // ANALYTICS

// ANALYTICS — Stat card for the overview row
function StatCard({ icon: Icon, label, value, color, bg, loading }) { // ANALYTICS
  return ( // ANALYTICS
    <motion.div // ANALYTICS
      initial={{ opacity: 0, y: 20 }} // ANALYTICS
      animate={{ opacity: 1, y: 0 }} // ANALYTICS
    > {/* ANALYTICS */}
      <Card className="relative overflow-hidden"> {/* ANALYTICS */}
        <div className="absolute inset-0 bg-gradient-to-br from-white/[0.02] to-transparent" /> {/* ANALYTICS */}
        <CardContent className="p-5"> {/* ANALYTICS */}
          <div className="flex items-center gap-4"> {/* ANALYTICS */}
            <div className={cn('p-2.5 rounded-xl', bg)}> {/* ANALYTICS */}
              <Icon className={cn('w-5 h-5', color)} /> {/* ANALYTICS */}
            </div> {/* ANALYTICS */}
            <div className="min-w-0"> {/* ANALYTICS */}
              <p className="text-xs text-gray-500 mb-0.5">{label}</p> {/* ANALYTICS */}
              {loading ? ( // ANALYTICS
                <Skeleton className="h-7 w-16" /> // ANALYTICS
              ) : ( // ANALYTICS
                <p className="text-2xl font-bold font-mono tracking-tight">{value}</p> // ANALYTICS
              )} {/* ANALYTICS */}
            </div> {/* ANALYTICS */}
          </div> {/* ANALYTICS */}
        </CardContent> {/* ANALYTICS */}
      </Card> {/* ANALYTICS */}
    </motion.div> // ANALYTICS
  ); // ANALYTICS
} // ANALYTICS

// ANALYTICS — Pie chart colors matching theme
const PIE_COLORS = ['#22c55e', '#3b82f6', '#6b7280', '#f59e0b', '#ef4444', '#a855f7']; // ANALYTICS

// ANALYTICS — Source platform color map
const PLATFORM_COLORS = { // ANALYTICS
  youtube: '#22c55e', // ANALYTICS
  soundcloud: '#3b82f6', // ANALYTICS
  unknown: '#6b7280', // ANALYTICS
}; // ANALYTICS

export default function Analytics() { // ANALYTICS
  const { addToast } = useToast(); // ANALYTICS

  // ANALYTICS — State for all data sections
  const [overview, setOverview] = useState(null); // ANALYTICS
  const [perDay, setPerDay] = useState([]); // ANALYTICS
  const [topArtists, setTopArtists] = useState([]); // ANALYTICS
  const [sourceBreakdown, setSourceBreakdown] = useState([]); // ANALYTICS
  const [taggingBreakdown, setTaggingBreakdown] = useState([]); // ANALYTICS
  const [recentDownloads, setRecentDownloads] = useState([]); // ANALYTICS
  const [failedDownloads, setFailedDownloads] = useState([]); // ANALYTICS
  const [dayRange, setDayRange] = useState(30); // ANALYTICS
  const [loading, setLoading] = useState(true); // ANALYTICS
  const [retrying, setRetrying] = useState({}); // ANALYTICS

  // ANALYTICS — Fetch all analytics data
  const fetchAll = useCallback(async () => { // ANALYTICS
    try { // ANALYTICS
      const [ov, pd, ta, sb, tb, rd, fd] = await Promise.all([ // ANALYTICS
        api.getAnalyticsOverview(), // ANALYTICS
        api.getAnalyticsDownloadsPerDay(dayRange), // ANALYTICS
        api.getAnalyticsTopArtists(), // ANALYTICS
        api.getAnalyticsSourceBreakdown(), // ANALYTICS
        api.getAnalyticsTaggingBreakdown(), // ANALYTICS
        api.getAnalyticsRecent(), // ANALYTICS
        api.getAnalyticsFailed(), // ANALYTICS
      ]); // ANALYTICS
      setOverview(ov); // ANALYTICS
      setPerDay(pd); // ANALYTICS
      setTopArtists(ta); // ANALYTICS
      setSourceBreakdown(sb); // ANALYTICS
      setTaggingBreakdown(tb); // ANALYTICS
      setRecentDownloads(rd); // ANALYTICS
      setFailedDownloads(fd); // ANALYTICS
    } catch { // ANALYTICS
      // silent — data just won't update // ANALYTICS
    } finally { // ANALYTICS
      setLoading(false); // ANALYTICS
    } // ANALYTICS
  }, [dayRange]); // ANALYTICS

  // ANALYTICS — Initial load + auto refresh every 60s
  useEffect(() => { // ANALYTICS
    fetchAll(); // ANALYTICS
    const interval = setInterval(fetchAll, 60000); // ANALYTICS
    return () => clearInterval(interval); // ANALYTICS
  }, [fetchAll]); // ANALYTICS

  // ANALYTICS — Re-fetch when dayRange changes
  useEffect(() => { // ANALYTICS
    api.getAnalyticsDownloadsPerDay(dayRange).then(setPerDay).catch(() => {}); // ANALYTICS
  }, [dayRange]); // ANALYTICS

  // ANALYTICS — Retry a failed download
  async function handleRetry(trackId, idx) { // ANALYTICS
    setRetrying((prev) => ({ ...prev, [idx]: true })); // ANALYTICS
    try { // ANALYTICS
      await api.retryDownload(trackId); // ANALYTICS
      addToast({ type: 'success', title: 'Retry Queued', description: 'Track re-queued for download' }); // ANALYTICS
      setFailedDownloads((prev) => prev.filter((_, i) => i !== idx)); // ANALYTICS
    } catch { // ANALYTICS
      addToast({ type: 'error', title: 'Retry Failed', description: 'Could not re-queue the track' }); // ANALYTICS
    } finally { // ANALYTICS
      setRetrying((prev) => ({ ...prev, [idx]: false })); // ANALYTICS
    } // ANALYTICS
  } // ANALYTICS

  // ANALYTICS — Format relative timestamp
  function formatTime(isoStr) { // ANALYTICS
    if (!isoStr) return '—'; // ANALYTICS
    try { // ANALYTICS
      const d = new Date(isoStr); // ANALYTICS
      const now = new Date(); // ANALYTICS
      const diff = Math.floor((now - d) / 1000); // ANALYTICS
      if (diff < 60) return `${diff}s ago`; // ANALYTICS
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`; // ANALYTICS
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`; // ANALYTICS
      return d.toLocaleDateString(); // ANALYTICS
    } catch { // ANALYTICS
      return isoStr; // ANALYTICS
    } // ANALYTICS
  } // ANALYTICS

  return ( // ANALYTICS
    <div className="max-w-5xl mx-auto space-y-6"> {/* ANALYTICS */}
      {/* ANALYTICS — Page header */}
      <motion.div // ANALYTICS
        initial={{ opacity: 0, y: 20 }} // ANALYTICS
        animate={{ opacity: 1, y: 0 }} // ANALYTICS
        className="flex items-center justify-between" // ANALYTICS
      > {/* ANALYTICS */}
        <div className="flex items-center gap-3"> {/* ANALYTICS */}
          <div className="p-2 rounded-xl bg-primary/10"> {/* ANALYTICS */}
            <BarChart3 className="w-5 h-5 text-primary" /> {/* ANALYTICS */}
          </div> {/* ANALYTICS */}
          <div> {/* ANALYTICS */}
            <h1 className="text-xl font-semibold">Analytics</h1> {/* ANALYTICS */}
            <p className="text-sm text-gray-500">Library statistics & insights</p> {/* ANALYTICS */}
          </div> {/* ANALYTICS */}
        </div> {/* ANALYTICS */}
        <Button variant="ghost" size="sm" onClick={() => { setLoading(true); fetchAll(); }}> {/* ANALYTICS */}
          <RotateCw className={cn('w-4 h-4', loading && 'animate-spin')} /> {/* ANALYTICS */}
          Refresh {/* ANALYTICS */}
        </Button> {/* ANALYTICS */}
      </motion.div> {/* ANALYTICS */}

      {/* ANALYTICS — Row 1: Overview stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3"> {/* ANALYTICS */}
        <StatCard // ANALYTICS
          icon={Download} // ANALYTICS
          label="Total Downloads" // ANALYTICS
          value={overview?.total_downloads ?? '—'} // ANALYTICS
          color="text-emerald-400" // ANALYTICS
          bg="bg-emerald-400/10" // ANALYTICS
          loading={loading} // ANALYTICS
        /> {/* ANALYTICS */}
        <StatCard // ANALYTICS
          icon={TrendingUp} // ANALYTICS
          label="Success Rate" // ANALYTICS
          value={overview ? `${overview.success_rate}%` : '—'} // ANALYTICS
          color="text-blue-400" // ANALYTICS
          bg="bg-blue-400/10" // ANALYTICS
          loading={loading} // ANALYTICS
        /> {/* ANALYTICS */}
        <StatCard // ANALYTICS
          icon={HardDrive} // ANALYTICS
          label="MB Cached" // ANALYTICS
          value={overview?.musicbrainz_cached ?? '—'} // ANALYTICS
          color="text-yellow-400" // ANALYTICS
          bg="bg-yellow-400/10" // ANALYTICS
          loading={loading} // ANALYTICS
        /> {/* ANALYTICS */}
        <StatCard // ANALYTICS
          icon={Users} // ANALYTICS
          label="Total Artists" // ANALYTICS
          value={overview?.total_artists ?? '—'} // ANALYTICS
          color="text-purple-400" // ANALYTICS
          bg="bg-purple-400/10" // ANALYTICS
          loading={loading} // ANALYTICS
        /> {/* ANALYTICS */}
      </div> {/* ANALYTICS */}

      {/* ANALYTICS — Row 2: Downloads per day chart */}
      <Card> {/* ANALYTICS */}
        <CardHeader> {/* ANALYTICS */}
          <div className="flex items-center justify-between"> {/* ANALYTICS */}
            <CardTitle className="text-sm font-medium text-gray-300">Downloads Per Day</CardTitle> {/* ANALYTICS */}
            <div className="flex gap-1 bg-surface-light/50 rounded-lg p-0.5"> {/* ANALYTICS */}
              {[7, 30, 90].map((d) => ( // ANALYTICS
                <button // ANALYTICS
                  key={d} // ANALYTICS
                  onClick={() => setDayRange(d)} // ANALYTICS
                  className={cn( // ANALYTICS
                    'px-2.5 py-1 rounded-md text-xs font-medium transition-all', // ANALYTICS
                    dayRange === d // ANALYTICS
                      ? 'bg-primary/15 text-primary' // ANALYTICS
                      : 'text-gray-500 hover:text-gray-300' // ANALYTICS
                  )} // ANALYTICS
                > {/* ANALYTICS */}
                  {d}d {/* ANALYTICS */}
                </button> // ANALYTICS
              ))} {/* ANALYTICS */}
            </div> {/* ANALYTICS */}
          </div> {/* ANALYTICS */}
        </CardHeader> {/* ANALYTICS */}
        <CardContent> {/* ANALYTICS */}
          {loading ? ( // ANALYTICS
            <Skeleton className="h-64 w-full" /> // ANALYTICS
          ) : perDay.length === 0 ? ( // ANALYTICS
            <div className="flex flex-col items-center justify-center h-64 text-gray-600"> {/* ANALYTICS */}
              <BarChart3 className="w-10 h-10 mb-3" /> {/* ANALYTICS */}
              <p className="text-sm">No download data yet</p> {/* ANALYTICS */}
            </div> // ANALYTICS
          ) : ( // ANALYTICS
            <ResponsiveContainer width="100%" height={260}> {/* ANALYTICS */}
              <LineChart data={perDay}> {/* ANALYTICS */}
                <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" /> {/* ANALYTICS */}
                <XAxis // ANALYTICS
                  dataKey="date" // ANALYTICS
                  tick={{ fill: '#6b7280', fontSize: 11 }} // ANALYTICS
                  tickFormatter={(v) => v.slice(5)} // ANALYTICS
                  stroke="#1f2937" // ANALYTICS
                /> {/* ANALYTICS */}
                <YAxis // ANALYTICS
                  tick={{ fill: '#6b7280', fontSize: 11 }} // ANALYTICS
                  stroke="#1f2937" // ANALYTICS
                  allowDecimals={false} // ANALYTICS
                /> {/* ANALYTICS */}
                <Tooltip content={<CustomTooltip />} /> {/* ANALYTICS */}
                <Line // ANALYTICS
                  type="monotone" // ANALYTICS
                  dataKey="count" // ANALYTICS
                  name="Downloads" // ANALYTICS
                  stroke="#22c55e" // ANALYTICS
                  strokeWidth={2} // ANALYTICS
                  dot={{ fill: '#22c55e', r: 3 }} // ANALYTICS
                  activeDot={{ r: 5, fill: '#22c55e' }} // ANALYTICS
                /> {/* ANALYTICS */}
              </LineChart> {/* ANALYTICS */}
            </ResponsiveContainer> // ANALYTICS
          )} {/* ANALYTICS */}
        </CardContent> {/* ANALYTICS */}
      </Card> {/* ANALYTICS */}

      {/* ANALYTICS — Row 3: Top artists + Source breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4"> {/* ANALYTICS */}
        {/* ANALYTICS — Top Artists bar chart */}
        <Card> {/* ANALYTICS */}
          <CardHeader> {/* ANALYTICS */}
            <CardTitle className="text-sm font-medium text-gray-300">Top Artists</CardTitle> {/* ANALYTICS */}
          </CardHeader> {/* ANALYTICS */}
          <CardContent> {/* ANALYTICS */}
            {loading ? ( // ANALYTICS
              <Skeleton className="h-64 w-full" /> // ANALYTICS
            ) : topArtists.length === 0 ? ( // ANALYTICS
              <div className="flex flex-col items-center justify-center h-64 text-gray-600"> {/* ANALYTICS */}
                <Music className="w-10 h-10 mb-3" /> {/* ANALYTICS */}
                <p className="text-sm">No artist data yet</p> {/* ANALYTICS */}
              </div> // ANALYTICS
            ) : ( // ANALYTICS
              <ResponsiveContainer width="100%" height={260}> {/* ANALYTICS */}
                <BarChart data={topArtists} layout="vertical" margin={{ left: 10 }}> {/* ANALYTICS */}
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" horizontal={false} /> {/* ANALYTICS */}
                  <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 11 }} stroke="#1f2937" allowDecimals={false} /> {/* ANALYTICS */}
                  <YAxis // ANALYTICS
                    type="category" // ANALYTICS
                    dataKey="artist" // ANALYTICS
                    tick={{ fill: '#9ca3af', fontSize: 11 }} // ANALYTICS
                    width={100} // ANALYTICS
                    stroke="#1f2937" // ANALYTICS
                  /> {/* ANALYTICS */}
                  <Tooltip content={<CustomTooltip />} /> {/* ANALYTICS */}
                  <Bar dataKey="count" name="Downloads" fill="#22c55e" radius={[0, 4, 4, 0]} /> {/* ANALYTICS */}
                </BarChart> {/* ANALYTICS */}
              </ResponsiveContainer> // ANALYTICS
            )} {/* ANALYTICS */}
          </CardContent> {/* ANALYTICS */}
        </Card> {/* ANALYTICS */}

        {/* ANALYTICS — Source breakdown pie chart */}
        <Card> {/* ANALYTICS */}
          <CardHeader> {/* ANALYTICS */}
            <CardTitle className="text-sm font-medium text-gray-300">Source Breakdown</CardTitle> {/* ANALYTICS */}
          </CardHeader> {/* ANALYTICS */}
          <CardContent> {/* ANALYTICS */}
            {loading ? ( // ANALYTICS
              <Skeleton className="h-64 w-full" /> // ANALYTICS
            ) : sourceBreakdown.length === 0 ? ( // ANALYTICS
              <div className="flex flex-col items-center justify-center h-64 text-gray-600"> {/* ANALYTICS */}
                <BarChart3 className="w-10 h-10 mb-3" /> {/* ANALYTICS */}
                <p className="text-sm">No source data yet</p> {/* ANALYTICS */}
              </div> // ANALYTICS
            ) : ( // ANALYTICS
              <div className="flex flex-col items-center"> {/* ANALYTICS */}
                <ResponsiveContainer width="100%" height={220}> {/* ANALYTICS */}
                  <PieChart> {/* ANALYTICS */}
                    <Pie // ANALYTICS
                      data={sourceBreakdown} // ANALYTICS
                      dataKey="count" // ANALYTICS
                      nameKey="platform" // ANALYTICS
                      cx="50%" // ANALYTICS
                      cy="50%" // ANALYTICS
                      outerRadius={80} // ANALYTICS
                      innerRadius={45} // ANALYTICS
                      strokeWidth={0} // ANALYTICS
                      paddingAngle={3} // ANALYTICS
                    > {/* ANALYTICS */}
                      {sourceBreakdown.map((entry, i) => ( // ANALYTICS
                        <Cell // ANALYTICS
                          key={entry.platform} // ANALYTICS
                          fill={PLATFORM_COLORS[entry.platform] || PIE_COLORS[i % PIE_COLORS.length]} // ANALYTICS
                        /> // ANALYTICS
                      ))} {/* ANALYTICS */}
                    </Pie> {/* ANALYTICS */}
                    <Tooltip content={<CustomTooltip />} /> {/* ANALYTICS */}
                    <Legend // ANALYTICS
                      formatter={(value) => <span className="text-xs text-gray-400">{value}</span>} // ANALYTICS
                    /> {/* ANALYTICS */}
                  </PieChart> {/* ANALYTICS */}
                </ResponsiveContainer> {/* ANALYTICS */}
              </div> // ANALYTICS
            )} {/* ANALYTICS */}
          </CardContent> {/* ANALYTICS */}
        </Card> {/* ANALYTICS */}
      </div> {/* ANALYTICS */}

      {/* ANALYTICS — Row 4: Recent downloads + Failed downloads tables */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4"> {/* ANALYTICS */}
        {/* ANALYTICS — Recent downloads table */}
        <Card> {/* ANALYTICS */}
          <CardHeader> {/* ANALYTICS */}
            <CardTitle className="text-sm font-medium text-gray-300">Recent Downloads</CardTitle> {/* ANALYTICS */}
          </CardHeader> {/* ANALYTICS */}
          <CardContent className="p-0"> {/* ANALYTICS */}
            <ScrollArea className="max-h-[360px]"> {/* ANALYTICS */}
              {loading ? ( // ANALYTICS
                <div className="p-5 space-y-3"> {/* ANALYTICS */}
                  {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-12 w-full" />)} {/* ANALYTICS */}
                </div> // ANALYTICS
              ) : recentDownloads.length === 0 ? ( // ANALYTICS
                <div className="flex flex-col items-center justify-center py-12 text-gray-600"> {/* ANALYTICS */}
                  <Download className="w-8 h-8 mb-2" /> {/* ANALYTICS */}
                  <p className="text-sm">No downloads yet</p> {/* ANALYTICS */}
                </div> // ANALYTICS
              ) : ( // ANALYTICS
                <table className="w-full text-xs"> {/* ANALYTICS */}
                  <thead> {/* ANALYTICS */}
                    <tr className="border-b border-border text-gray-500"> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium">Title</th> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium">Artist</th> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium hidden sm:table-cell">Platform</th> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium hidden sm:table-cell">Tagged</th> {/* ANALYTICS */}
                      <th className="text-right px-4 py-2.5 font-medium">Time</th> {/* ANALYTICS */}
                    </tr> {/* ANALYTICS */}
                  </thead> {/* ANALYTICS */}
                  <tbody className="divide-y divide-border"> {/* ANALYTICS */}
                    {recentDownloads.map((item, i) => ( // ANALYTICS
                      <tr key={item._id || i} className="hover:bg-surface-light/30 transition-colors"> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 truncate max-w-[140px] text-gray-200">{item.track_title || '—'}</td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 truncate max-w-[100px] text-gray-400">{item.artist || '—'}</td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 hidden sm:table-cell"> {/* ANALYTICS */}
                          <Badge variant="secondary" className="text-[10px]">{item.source_platform || '—'}</Badge> {/* ANALYTICS */}
                        </td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 hidden sm:table-cell"> {/* ANALYTICS */}
                          {item.tagging_report ? ( // ANALYTICS
                            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> // ANALYTICS
                          ) : ( // ANALYTICS
                            <XCircle className="w-3.5 h-3.5 text-gray-600" /> // ANALYTICS
                          )} {/* ANALYTICS */}
                        </td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 text-right text-gray-500 font-mono whitespace-nowrap"> {/* ANALYTICS */}
                          {formatTime(item.downloaded_at)} {/* ANALYTICS */}
                        </td> {/* ANALYTICS */}
                      </tr> // ANALYTICS
                    ))} {/* ANALYTICS */}
                  </tbody> {/* ANALYTICS */}
                </table> // ANALYTICS
              )} {/* ANALYTICS */}
            </ScrollArea> {/* ANALYTICS */}
          </CardContent> {/* ANALYTICS */}
        </Card> {/* ANALYTICS */}

        {/* ANALYTICS — Failed downloads table */}
        <Card> {/* ANALYTICS */}
          <CardHeader> {/* ANALYTICS */}
            <div className="flex items-center justify-between"> {/* ANALYTICS */}
              <CardTitle className="text-sm font-medium text-gray-300">Failed Downloads</CardTitle> {/* ANALYTICS */}
              {failedDownloads.length > 0 && ( // ANALYTICS
                <Badge variant="danger" className="text-[10px]">{failedDownloads.length}</Badge> // ANALYTICS
              )} {/* ANALYTICS */}
            </div> {/* ANALYTICS */}
          </CardHeader> {/* ANALYTICS */}
          <CardContent className="p-0"> {/* ANALYTICS */}
            <ScrollArea className="max-h-[360px]"> {/* ANALYTICS */}
              {loading ? ( // ANALYTICS
                <div className="p-5 space-y-3"> {/* ANALYTICS */}
                  {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-12 w-full" />)} {/* ANALYTICS */}
                </div> // ANALYTICS
              ) : failedDownloads.length === 0 ? ( // ANALYTICS
                <div className="flex flex-col items-center justify-center py-12 text-gray-600"> {/* ANALYTICS */}
                  <CheckCircle2 className="w-8 h-8 mb-2" /> {/* ANALYTICS */}
                  <p className="text-sm">No failures — great!</p> {/* ANALYTICS */}
                </div> // ANALYTICS
              ) : ( // ANALYTICS
                <table className="w-full text-xs"> {/* ANALYTICS */}
                  <thead> {/* ANALYTICS */}
                    <tr className="border-b border-border text-gray-500"> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium">Title</th> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium">Artist</th> {/* ANALYTICS */}
                      <th className="text-left px-4 py-2.5 font-medium hidden sm:table-cell">Error</th> {/* ANALYTICS */}
                      <th className="text-right px-4 py-2.5 font-medium">Time</th> {/* ANALYTICS */}
                      <th className="text-right px-4 py-2.5 font-medium w-16"></th> {/* ANALYTICS */}
                    </tr> {/* ANALYTICS */}
                  </thead> {/* ANALYTICS */}
                  <tbody className="divide-y divide-border"> {/* ANALYTICS */}
                    {failedDownloads.map((item, i) => ( // ANALYTICS
                      <tr key={item._id || i} className="hover:bg-surface-light/30 transition-colors"> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 truncate max-w-[120px] text-gray-200">{item.title || '—'}</td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 truncate max-w-[80px] text-gray-400">{item.artist || '—'}</td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 truncate max-w-[140px] text-red-400/70 hidden sm:table-cell">{item.error || '—'}</td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 text-right text-gray-500 font-mono whitespace-nowrap"> {/* ANALYTICS */}
                          {formatTime(item.timestamp)} {/* ANALYTICS */}
                        </td> {/* ANALYTICS */}
                        <td className="px-4 py-2.5 text-right"> {/* ANALYTICS */}
                          <Button // ANALYTICS
                            variant="ghost" // ANALYTICS
                            size="sm" // ANALYTICS
                            className="h-6 px-2 text-[10px]" // ANALYTICS
                            disabled={retrying[i]} // ANALYTICS
                            onClick={() => handleRetry(item.track_id, i)} // ANALYTICS
                          > {/* ANALYTICS */}
                            {retrying[i] ? ( // ANALYTICS
                              <Loader2 className="w-3 h-3 animate-spin" /> // ANALYTICS
                            ) : ( // ANALYTICS
                              <RotateCw className="w-3 h-3" /> // ANALYTICS
                            )} {/* ANALYTICS */}
                            Retry {/* ANALYTICS */}
                          </Button> {/* ANALYTICS */}
                        </td> {/* ANALYTICS */}
                      </tr> // ANALYTICS
                    ))} {/* ANALYTICS */}
                  </tbody> {/* ANALYTICS */}
                </table> // ANALYTICS
              )} {/* ANALYTICS */}
            </ScrollArea> {/* ANALYTICS */}
          </CardContent> {/* ANALYTICS */}
        </Card> {/* ANALYTICS */}
      </div> {/* ANALYTICS */}

      {/* ANALYTICS — Tagging breakdown summary */}
      <Card> {/* ANALYTICS */}
        <CardHeader> {/* ANALYTICS */}
          <CardTitle className="text-sm font-medium text-gray-300">Tagging Breakdown</CardTitle> {/* ANALYTICS */}
        </CardHeader> {/* ANALYTICS */}
        <CardContent> {/* ANALYTICS */}
          {loading ? ( // ANALYTICS
            <Skeleton className="h-16 w-full" /> // ANALYTICS
          ) : ( // ANALYTICS
            <div className="grid grid-cols-3 gap-4"> {/* ANALYTICS */}
              {taggingBreakdown.map((item) => ( // ANALYTICS
                <div // ANALYTICS
                  key={item.source} // ANALYTICS
                  className="flex flex-col items-center rounded-xl border border-border bg-surface-light/30 p-4" // ANALYTICS
                > {/* ANALYTICS */}
                  <span className="text-2xl font-bold font-mono">{item.count}</span> {/* ANALYTICS */}
                  <span className="text-xs text-gray-500 mt-1">{item.source}</span> {/* ANALYTICS */}
                </div> // ANALYTICS
              ))} {/* ANALYTICS */}
            </div> // ANALYTICS
          )} {/* ANALYTICS */}
        </CardContent> {/* ANALYTICS */}
      </Card> {/* ANALYTICS */}
    </div> // ANALYTICS
  ); // ANALYTICS
} // ANALYTICS
