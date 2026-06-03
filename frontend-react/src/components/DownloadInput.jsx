import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Loader2, Music2, Disc3, Play, AlertCircle, AlertTriangle } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { api } from '@/services/api';
import { formatDuration } from '@/lib/utils';

export default function DownloadInput() {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [metadata, setMetadata] = useState(null);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');

  const SPOTIFY_URL_RE = /open\.spotify\.com\/(track|album|playlist)\/[A-Za-z0-9]+/;

  async function handleFetch() {
    if (!url.trim()) return;
    if (!SPOTIFY_URL_RE.test(url.trim())) {
      setError('Please enter a valid Spotify track, album, or playlist URL');
      return;
    }
    setLoading(true);
    setError('');
    setMetadata(null);

    try {
      const data = await api.fetchMetadata(url.trim());
      setMetadata(data);
    } catch (err) {
      setError(err.message || 'Failed to fetch metadata');
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload() {
    if (downloading) return;
    setDownloading(true);
    setError('');

    try {
      // Streaming download — file saves directly to user's ~/Downloads folder
      const res = await api.downloadStream(url.trim());
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.error || `Download failed (${res.status})`);
      }
      // Trigger browser file save
      const cd = res.headers.get('Content-Disposition') || '';
      const match = cd.match(/filename[^;=\n]*=['"]?(.*?)['"]?(?:;|$)/);
      const filename = match ? match[1] : `${metadata?.title || 'track'}.mp3`;
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objUrl;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objUrl);
      setUrl('');
      setMetadata(null);
    } catch (err) {
      setError(err.message || 'Download failed');
    } finally {
      setDownloading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') handleFetch();
  }

  return (
    <div className="space-y-5">
      {/* Hero Input */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="relative"
      >
        <div className="glass rounded-2xl p-6 glow-green">
          <div className="text-center mb-4">
            <h2 className="text-xl font-semibold mb-1">
              Download from Spotify
            </h2>
            <p className="text-sm text-gray-400">
              Supports tracks, albums, and playlists
            </p>
            <div className="flex items-center justify-center gap-2 mt-2">
              {['Track', 'Album', 'Playlist'].map((t) => (
                <span key={t} className="text-[11px] px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-gray-400">
                  {t}
                </span>
              ))}
            </div>
          </div>

          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Paste Spotify URL — track, album, or playlist"
                className="pl-10 h-12 bg-background/60 border-white/10 text-base"
                disabled={loading}
              />
            </div>
            <Button
              onClick={handleFetch}
              disabled={loading || !url.trim()}
              size="lg"
              className="h-12 px-5 gap-2 active:scale-95 transition-all duration-150"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin flex-shrink-0" />
                  <span className="hidden sm:inline">Fetching…</span>
                </>
              ) : 'Fetch'}
            </Button>
          </div>
        </div>
      </motion.div>

      {/* Error */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="flex items-center gap-2 px-4 py-3 rounded-xl bg-danger-muted border border-red-500/20"
          >
            <AlertCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
            <span className="text-sm text-red-400">{error}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Metadata Result */}
      <AnimatePresence>
        {metadata && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.3 }}
          >
            <Card className="overflow-hidden">
              <div className="p-5">
                <div className="flex items-start gap-4">
                  <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center flex-shrink-0">
                    {metadata.type === 'album' ? (
                      <Disc3 className="w-7 h-7 text-primary" />
                    ) : (
                      <Music2 className="w-7 h-7 text-primary" />
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-lg truncate">
                        {metadata.type === 'album'
                          ? metadata.name
                          : metadata.title}
                      </h3>
                      <Badge
                        variant={
                          metadata.source === 'cache' ? 'info' : 'default'
                        }
                      >
                        {metadata.source === 'cache'
                          ? 'Cached'
                          : 'Spotify'}
                      </Badge>
                    </div>
                    <p className="text-sm text-gray-400">
                      {metadata.artist}
                      {metadata.type === 'track' && metadata.album && (
                        <span> · {metadata.album}</span>
                      )}
                      {metadata.type === 'track' &&
                        metadata.duration > 0 && (
                          <span>
                            {' '}
                            · {formatDuration(metadata.duration)}
                          </span>
                        )}
                      {metadata.type === 'album' && (
                        <span> · {metadata.total_tracks} tracks</span>
                      )}
                    </p>
                  </div>
                </div>

                {/* Duplicate warning */}
                {metadata.type === 'track' && metadata.already_in_library && (
                  <div className="mt-3 flex items-start gap-2 px-3 py-2.5 rounded-xl"
                       style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)' }}>
                    <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#f59e0b' }} />
                    <div>
                      <p className="text-12 font-medium" style={{ color: '#f59e0b' }}>
                        Already in library
                      </p>
                      {metadata.existing_folder && (
                        <p className="text-11 mt-0.5" style={{ color: 'rgba(245,158,11,0.7)' }}>
                          {metadata.existing_folder}
                        </p>
                      )}
                    </div>
                  </div>
                )}

                {/* Album tracks list */}
                {metadata.type === 'album' && metadata.tracks && (
                  <div className="mt-4 max-h-60 overflow-y-auto scrollbar-thin space-y-0.5">
                    {metadata.tracks.map((t, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-surface-light/50 transition-colors group"
                      >
                        <span className="w-6 text-right text-xs text-gray-500 font-mono">
                          {i + 1}
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm truncate">{t.title}</div>
                          <div className="text-xs text-gray-500 truncate">
                            {t.artist}
                          </div>
                        </div>
                        <span className="text-xs text-gray-500 font-mono">
                          {formatDuration(t.duration || 0)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                <div className="mt-4 flex gap-2">
                  <Button
                    onClick={handleDownload}
                    disabled={downloading}
                    className="flex-1"
                  >
                    {downloading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Downloading... (30–60s)
                      </>
                    ) : (
                      <>
                        <Play className="w-4 h-4" />
                        {metadata.type === 'album'
                          ? 'Download All'
                          : metadata.already_in_library
                            ? 'Download Again'
                            : 'Download'}
                      </>
                    )}
                  </Button>
                </div>
              </div>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
