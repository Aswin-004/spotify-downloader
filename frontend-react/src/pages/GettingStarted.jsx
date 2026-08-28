import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import {
  Download, FolderOpen, Music, BarChart3, AlertTriangle,
  Wrench, Settings, CheckCircle2, ArrowRight, Zap, History, BookOpen,
} from 'lucide-react';
import { ease } from '@/lib/motion';

const steps = [
  {
    num: '1',
    title: 'Paste a Spotify URL',
    desc: 'Copy any Spotify track, album, or playlist link and paste it into the search box on the Download page.',
    code: 'open.spotify.com/track/...',
  },
  {
    num: '2',
    title: 'Fetch & confirm',
    desc: 'Click Fetch to see the track details — title, artist, album art, and duration. Confirm it\'s the right one before downloading.',
  },
  {
    num: '3',
    title: 'Download to your computer',
    desc: 'Click Download. The MP3 saves directly to your Downloads folder in 30–60 seconds. This requires running the app locally — cloud deployments cannot download due to YouTube\'s server IP restrictions.',
    code: '~/Downloads/Artist - Title.mp3',
  },
  {
    num: '4',
    title: 'Manage your library',
    desc: 'Use the Analytics, Review, and Library pages to track your downloads, fix genre routing, and manage your DJ collection.',
  },
];

const pages = [
  { icon: Download,      label: 'Download',    to: '/',            accent: 'var(--accent-violet)', desc: 'Paste any Spotify track, album, or playlist URL. Metadata is fetched first so you can confirm before downloading.' },
  { icon: History,       label: 'History',     to: '/history',     accent: 'var(--accent-cyan)',   desc: 'Full log of every download with status, source quality, and error details. Retry failed tracks from here.' },
  { icon: Music,         label: 'Library',     to: '/library',     accent: 'var(--accent-emerald)', desc: 'Browse all downloaded MP3 files grouped by genre folder. Preview tracks, move to different genres, export to Rekordbox.' },
  { icon: BarChart3,     label: 'Analytics',   to: '/analytics',   accent: 'var(--accent-amber)',  desc: 'Download stats, top artists, tagging success rates, and genre distribution over time.' },
  { icon: AlertTriangle, label: 'Review',      to: '/review',      accent: 'var(--accent-amber)',  desc: 'Unclassified tracks — downloaded but genre could not be detected. Retry to sort them into the right folder.' },
  { icon: Wrench,        label: 'Maintenance', to: '/maintenance', accent: 'var(--accent-rose)',   desc: 'Re-sort music into folders, fix library scan, re-classify undetected genres, embed album artwork.' },
  { icon: Settings,      label: 'Settings',    to: '/settings',    accent: 'var(--text-tertiary)', desc: 'Map your existing DJ folder structure to genres. Configure Telegram notifications.' },
];

const workflow = [
  { label: 'Paste URL',       detail: 'Spotify track / album / playlist' },
  { label: 'Fetch metadata',  detail: 'Title, artist, album, duration' },
  { label: 'Find audio',      detail: 'Searches YouTube for best audio match' },
  { label: 'Download MP3',    detail: '192kbps MP3 saved to your Downloads folder' },
  { label: 'Tag & embed',     detail: 'ID3 tags: title, artist, album art' },
  { label: 'Track history',   detail: 'Analytics, genre stats, download log' },
];

const faqs = [
  {
    q: 'Does downloading work on the cloud version?',
    a: 'No — downloads only work when you run the app locally on your own machine. YouTube blocks audio downloads from cloud server IPs (a permanent restriction, not a bug). The cloud version is read-only: you can browse your library, view analytics, manage genres, and preview tracks — but new downloads require local setup.',
  },
  {
    q: 'What are "Unclassified" tracks?',
    a: "Tracks land in the Unclassified folder when the app can't figure out their genre automatically (usually because today's AI detection limit was reached). The Review page lets you retry them — or just wait until tomorrow when the limit resets.",
  },
  {
    q: 'Is this completely free?',
    a: 'Yes — completely free. No account needed, no subscription, no limits on how many tracks you download.',
  },
  {
    q: 'What quality are the downloads?',
    a: 'MP3 at 192kbps. The app finds the best available audio match on YouTube for each Spotify track.',
  },
  {
    q: 'Can I download a full playlist?',
    a: 'Yes — paste a Spotify playlist URL, fetch it, and download all tracks. Each track saves to your Downloads folder in sequence.',
  },
];

function Section({ children, delay = 0 }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...ease, delay }}
      className="space-y-4"
    >
      {children}
    </motion.section>
  );
}

function SectionTitle({ icon: Icon, color, children }) {
  return (
    <h2 className="font-display text-18 font-bold flex items-center gap-2.5" style={{ color: 'var(--text-primary)' }}>
      <Icon className="w-5 h-5" style={{ color }} />
      {children}
    </h2>
  );
}

export default function GettingStarted() {
  return (
    <div className="max-w-4xl mx-auto space-y-12 pb-16">

      {/* Hero */}
      <Section>
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
               style={{ background: 'var(--accent-violet-dim)' }}>
            <BookOpen className="w-5 h-5" style={{ color: 'var(--accent-violet)' }} />
          </div>
          <h1 className="font-display text-28 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
            Getting Started
          </h1>
        </div>
        <p className="text-14 leading-relaxed max-w-2xl" style={{ color: 'var(--text-secondary)' }}>
          SpotifyDL downloads tracks from Spotify, tags them with ID3 metadata, and automatically
          organises them into genre-based folders — so your DJ library is always sorted.
        </p>
      </Section>

      {/* Setup steps */}
      <Section delay={0.05}>
        <SectionTitle icon={CheckCircle2} color="var(--accent-emerald)">First-Time Setup</SectionTitle>
        <div className="space-y-3">
          {steps.map((s, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ ...ease, delay: 0.08 + i * 0.05 }}
              className="step-card"
            >
              <div className="step-num">
                {s.num}
              </div>
              <div className="space-y-1 min-w-0">
                <p className="step-title">{s.title}</p>
                <p className="step-desc">{s.desc}</p>
                {s.code && (
                  <code className="text-11 font-mono px-2 py-0.5 rounded-md inline-block"
                        style={{ background: 'var(--accent-violet-dim)', color: 'var(--text-accent)' }}>
                    {s.code}
                  </code>
                )}
              </div>
            </motion.div>
          ))}
        </div>
      </Section>

      {/* Download workflow */}
      <Section delay={0.15}>
        <SectionTitle icon={Zap} color="var(--accent-amber)">The Download Workflow</SectionTitle>
        <div className="glass-card flex flex-wrap gap-2 items-center">
          {workflow.map((step, i) => (
            <div key={i} className="flex items-center gap-2">
              <div className="space-y-0.5 text-center">
                <span className="inline-block text-11 font-semibold px-2.5 py-1 rounded-lg"
                      style={{ background: 'var(--surface-1)', color: 'var(--text-secondary)', border: '1px solid var(--border-default)' }}>
                  {step.label}
                </span>
                <div className="text-10 text-center max-w-28" style={{ color: 'var(--text-muted)' }}>
                  {step.detail}
                </div>
              </div>
              {i < workflow.length - 1 && (
                <ArrowRight className="w-3.5 h-3.5 flex-shrink-0 mb-3" style={{ color: 'var(--border-default)' }} />
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* Pages at a glance */}
      <Section delay={0.2}>
        <SectionTitle icon={FolderOpen} color="var(--accent-cyan)">Pages at a Glance</SectionTitle>
        <div className="grid-2">
          {pages.map(({ icon: Icon, label, to, accent, desc }) => (
            <Link key={to} to={to} className="block">
              <div className="glass-card hoverable h-full space-y-2">
                <div className="flex items-center gap-2">
                  <Icon className="w-4 h-4" style={{ color: accent }} />
                  <span className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>{label}</span>
                  <ArrowRight className="w-3 h-3 ml-auto" style={{ color: 'var(--text-muted)' }} />
                </div>
                <p className="text-11 leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>{desc}</p>
              </div>
            </Link>
          ))}
        </div>
      </Section>

      {/* Custom folders */}
      <Section delay={0.25}>
        <SectionTitle icon={Settings} color="var(--text-tertiary)">Using Your Existing DJ Folder Structure</SectionTitle>
        <div className="glass-card space-y-3">
          <p className="text-12" style={{ color: 'var(--text-tertiary)' }}>
            If you already have an organised music folder (e.g.{' '}
            <code className="text-11 font-mono px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-violet-dim)', color: 'var(--text-accent)' }}>D:\Music\House\</code>
            ,{' '}
            <code className="text-11 font-mono px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-violet-dim)', color: 'var(--text-accent)' }}>D:\Music\Drum and Bass\</code>
            ), point{' '}
            <code className="text-11 font-mono px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-violet-dim)', color: 'var(--text-accent)' }}>BASE_DOWNLOAD_DIR</code>
            {' '}to it and set up custom folder mappings:
          </p>
          <ol className="space-y-2">
            {[
              'Open Settings → click "Scan My Music Folder"',
              'The app detects your existing subfolders and lists them',
              'Click any folder name to pre-fill the mapping form',
              'Set a genre label (e.g. "Drum and Bass") and save',
              'New downloads for that genre will go into your folder automatically',
            ].map((t, i) => (
              <li key={i} className="flex items-start gap-2.5">
                <span className="w-5 h-5 rounded-full flex items-center justify-center text-10 font-bold flex-shrink-0 mt-0.5"
                      style={{ background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)' }}>
                  {i + 1}
                </span>
                <span className="text-12" style={{ color: 'var(--text-secondary)' }}>{t}</span>
              </li>
            ))}
          </ol>
          <Link
            to="/settings"
            className="inline-flex items-center gap-1.5 text-12 transition-colors"
            style={{ color: 'var(--accent-violet)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text-accent)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--accent-violet)'}
          >
            Open Settings <ArrowRight className="w-3.5 h-3.5" />
          </Link>
        </div>
      </Section>

      {/* FAQ */}
      <Section delay={0.3}>
        <SectionTitle icon={BookOpen} color="var(--accent-violet)">FAQ</SectionTitle>
        <div className="space-y-3">
          {faqs.map((faq, i) => (
            <div key={i} className="glass-card space-y-1.5">
              <p className="text-13 font-semibold" style={{ color: 'var(--text-primary)' }}>{faq.q}</p>
              <p className="text-12 leading-relaxed" style={{ color: 'var(--text-tertiary)' }}>{faq.a}</p>
            </div>
          ))}
        </div>
      </Section>
    </div>
  );
}
