import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  Download,
  FolderOpen,
  Music,
  BarChart3,
  AlertTriangle,
  Wrench,
  Settings,
  CheckCircle2,
  ArrowRight,
  Zap,
  History,
  BookOpen,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

const steps = [
  {
    num: '1',
    title: 'Copy .env.example → .env',
    desc: 'Inside the backend/ folder, copy .env.example to .env and fill in your API keys.',
    code: 'backend/.env.example → backend/.env',
  },
  {
    num: '2',
    title: 'Get your API keys',
    desc: 'You need: Spotify (developer.spotify.com), Gemini (aistudio.google.com), Last.fm, AcoustID. All have free tiers.',
  },
  {
    num: '3',
    title: 'Set BASE_DOWNLOAD_DIR',
    desc: 'Point BASE_DOWNLOAD_DIR to your music folder. The app creates genre subfolders (House/, Techno/, etc.) automatically.',
    code: 'BASE_DOWNLOAD_DIR=C:\\Users\\You\\Music',
  },
  {
    num: '4',
    title: 'Run start.bat',
    desc: 'Double-click start.bat. It builds the frontend, starts Redis + Celery (optional), then launches the backend.',
    code: 'start.bat → http://localhost:5000',
  },
];

const pages = [
  { icon: Download, label: 'Download', to: '/', color: 'text-purple-400', desc: 'Paste any Spotify track, album, or playlist URL. Metadata is fetched first so you can confirm before downloading.' },
  { icon: History, label: 'History', to: '/history', color: 'text-blue-400', desc: 'Full log of every download with status, source quality, and error details. Retry failed tracks from here.' },
  { icon: FolderOpen, label: 'Files', to: '/files', color: 'text-teal-400', desc: 'Browse all downloaded MP3 files grouped by genre folder. Delete individual files.' },
  { icon: Music, label: 'Library', to: '/library', color: 'text-green-400', desc: 'Search your music library. See album art thumbnails. Tracks are grouped by genre folder.' },
  { icon: BarChart3, label: 'Analytics', to: '/analytics', color: 'text-amber-400', desc: 'Download stats, top artists, tagging success rates, and genre distribution over time.' },
  { icon: AlertTriangle, label: 'Review', to: '/review', color: 'text-orange-400', desc: 'Unclassified tracks — downloaded but genre could not be detected. Retry to sort them into the right folder.' },
  { icon: Wrench, label: 'Maintenance', to: '/maintenance', color: 'text-red-400', desc: 'Re-sort music into folders, fix library scan, re-classify undetected genres, embed album artwork.' },
  { icon: Settings, label: 'Settings', to: '/settings', color: 'text-gray-400', desc: 'Map your existing DJ folder structure to genres. Configure Telegram notifications.' },
];

const workflow = [
  { label: 'Paste URL', detail: 'Spotify track / album / playlist' },
  { label: 'Fetch metadata', detail: 'Title, artist, album, duration' },
  { label: 'Queue download', detail: 'yt-dlp finds best audio match' },
  { label: 'Tag MP3', detail: 'ID3 tags: title, artist, album art' },
  { label: 'Identify genre', detail: 'Spotify data + AI detection → pick the right folder' },
  { label: 'Route to folder', detail: 'BASE_DOWNLOAD_DIR/Genre/track.mp3' },
];

const faqs = [
  {
    q: 'What are "Unclassified" tracks?',
    a: 'Tracks land in the Unclassified folder when the app can\'t figure out their genre automatically (usually because today\'s AI detection limit was reached). The Review page lets you retry them — or just wait until tomorrow when the limit resets.',
  },
  {
    q: 'How does Auto-Sync work?',
    a: 'Set your Spotify playlist ID in Settings, and the app checks it regularly. New tracks are downloaded and routed to the right folder automatically. Tracks you\'ve already downloaded are skipped.',
  },
  {
    q: 'What if the AI genre limit is hit?',
    a: 'The free AI tier allows 15 genre detections per day, resetting at midnight. Tracks that couldn\'t be classified are saved to the Review page and retried automatically the next day.',
  },
  {
    q: 'Can I use my existing DJ folder structure?',
    a: 'Yes. Go to Settings → Scan My Music Folder. The app detects your existing folders (e.g. "Drum and Bass", "House") and lets you map them to genres. Future downloads go straight into those folders.',
  },
  {
    q: 'Do I need Redis and Celery?',
    a: 'No — both are optional. Without Redis the app works fine for most use cases. With Redis + Celery you get better handling of large playlist downloads. start.bat tries to start Redis automatically if it\'s installed.',
  },
];

export default function GettingStarted() {
  return (
    <div className="max-w-4xl mx-auto space-y-12 pb-16">
      {/* Hero */}
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
        <div className="flex items-center gap-3">
          <BookOpen className="w-8 h-8 text-purple-400" />
          <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent">
            Getting Started
          </h1>
        </div>
        <p className="text-gray-400 text-lg max-w-2xl">
          SpotifyDL downloads tracks from Spotify, tags them with ID3 metadata, and automatically
          organises them into genre-based folders — so your DJ library is always sorted.
        </p>
      </motion.div>

      {/* Setup steps */}
      <motion.section initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.05 }} className="space-y-4">
        <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
          <CheckCircle2 className="w-5 h-5 text-emerald-400" />
          First-Time Setup
        </h2>
        <div className="space-y-3">
          {steps.map((s, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: 0.08 + i * 0.05 }}
            >
              <Card className="border border-gray-700/50 bg-white/5 p-4 flex gap-4">
                <div className="w-7 h-7 rounded-full bg-purple-500/20 flex items-center justify-center text-sm font-bold text-purple-400 shrink-0 mt-0.5">
                  {s.num}
                </div>
                <div className="space-y-1 min-w-0">
                  <div className="font-semibold text-gray-200">{s.title}</div>
                  <p className="text-sm text-gray-400">{s.desc}</p>
                  {s.code && (
                    <code className="text-xs font-mono text-purple-300 bg-purple-500/10 px-2 py-0.5 rounded">
                      {s.code}
                    </code>
                  )}
                </div>
              </Card>
            </motion.div>
          ))}
        </div>
      </motion.section>

      {/* Download workflow */}
      <motion.section initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="space-y-4">
        <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
          <Zap className="w-5 h-5 text-amber-400" />
          The Download Workflow
        </h2>
        <div className="flex flex-wrap gap-2 items-center">
          {workflow.map((step, i) => (
            <div key={i} className="flex items-center gap-2">
              <div className="space-y-0.5 text-center">
                <Badge className="bg-white/5 border-gray-700/50 text-gray-200 text-xs px-3 py-1">{step.label}</Badge>
                <div className="text-[10px] text-gray-600 text-center max-w-28">{step.detail}</div>
              </div>
              {i < workflow.length - 1 && <ArrowRight className="w-3.5 h-3.5 text-gray-700 shrink-0 mb-3" />}
            </div>
          ))}
        </div>
      </motion.section>

      {/* Pages at a glance */}
      <motion.section initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="space-y-4">
        <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
          <FolderOpen className="w-5 h-5 text-blue-400" />
          Pages at a Glance
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {pages.map(({ icon: Icon, label, to, color, desc }) => (
            <Link key={to} to={to}>
              <Card className="border border-gray-700/50 bg-white/5 hover:bg-white/[0.07] transition-colors p-4 h-full space-y-2">
                <div className="flex items-center gap-2">
                  <Icon className={`w-4 h-4 ${color}`} />
                  <span className="font-semibold text-gray-200 text-sm">{label}</span>
                  <ArrowRight className="w-3 h-3 text-gray-600 ml-auto" />
                </div>
                <p className="text-xs text-gray-500 leading-relaxed">{desc}</p>
              </Card>
            </Link>
          ))}
        </div>
      </motion.section>

      {/* Custom folders */}
      <motion.section initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="space-y-4">
        <h2 className="text-xl font-bold text-gray-100 flex items-center gap-2">
          <Settings className="w-5 h-5 text-gray-400" />
          Using Your Existing DJ Folder Structure
        </h2>
        <Card className="border border-gray-700/50 bg-white/5 p-5 space-y-3">
          <p className="text-sm text-gray-400">
            If you already have an organised music folder (e.g. <code className="text-purple-300">D:\Music\House\</code>,{' '}
            <code className="text-purple-300">D:\Music\Drum and Bass\</code>), point{' '}
            <code className="text-purple-300">BASE_DOWNLOAD_DIR</code> to it and set up custom folder mappings:
          </p>
          <ol className="space-y-2 text-sm text-gray-400 list-none">
            {[
              'Open Settings → click "Scan My Music Folder"',
              'The app detects your existing subfolders and lists them',
              'Click any folder name to pre-fill the mapping form',
              'Set a genre label (e.g. "Drum and Bass") and save',
              'New downloads for that genre will go into your folder automatically',
            ].map((t, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="w-5 h-5 rounded-full bg-purple-500/20 text-purple-400 text-xs flex items-center justify-center shrink-0 mt-0.5">{i + 1}</span>
                <span>{t}</span>
              </li>
            ))}
          </ol>
          <Link to="/settings" className="inline-flex items-center gap-1.5 text-sm text-purple-400 hover:text-purple-300 transition-colors mt-1">
            Open Settings <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </Card>
      </motion.section>

      {/* FAQ */}
      <motion.section initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} className="space-y-4">
        <h2 className="text-xl font-bold text-gray-100">FAQ</h2>
        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <Card key={i} className="border border-gray-700/50 bg-white/5 p-4 space-y-1.5">
              <div className="font-semibold text-gray-200 text-sm">{faq.q}</div>
              <p className="text-xs text-gray-400 leading-relaxed">{faq.a}</p>
            </Card>
          ))}
        </div>
      </motion.section>
    </div>
  );
}
