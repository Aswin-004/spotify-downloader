import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Search, Loader2, Music2, Disc3, Play, AlertCircle } from 'lucide-react';
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

  async function handleFetch() {
    if (!url.trim()) return;
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
      const res = await api.startDownload(url.trim());
      if (res.status !== 202) {
        throw new Error('Download request failed');
      }
      // Progress comes via WebSocket
    } catch (err) {
      setError(err.message || 'Download failed');
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
              Paste a track, album, or playlist link to get started
            </p>
          </div>

          <div className="flex gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="https://open.spotify.com/track/..."
                className="pl-10 h-12 bg-background/60 border-white/10 text-base"
                disabled={loading}
              />
            </div>
            <Button
              onClick={handleFetch}
              disabled={loading || !url.trim()}
              size="lg"
              className="h-12 px-6"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                'Fetch'
              )}
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
                        Downloading...
                      </>
                    ) : (
                      <>
                        <Play className="w-4 h-4" />
                        {metadata.type === 'album'
                          ? 'Download All'
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
