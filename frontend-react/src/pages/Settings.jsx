import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderSearch, Plus, Trash2, Save, Bell, CheckCircle2, AlertTriangle,
  Loader2, FolderOpen, Eye, EyeOff, Music2, Key, Radio, ChevronRight, BookOpen,
  Layers,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import { ease } from '@/lib/motion';

// ── Helpers ───────────────────────────────────────────────────────────────────

function SecretInput({ value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="pr-9"
      />
      <button
        type="button"
        onClick={() => setShow(v => !v)}
        className="absolute right-2.5 top-1/2 -translate-y-1/2 transition-colors cursor-pointer focus-ring rounded"
        style={{ color: 'var(--text-muted)' }}
        onMouseEnter={e => e.currentTarget.style.color = 'var(--text-tertiary)'}
        onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
      >
        {show ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

function SectionCard({ icon: Icon, accent, title, desc, children, delay = 0 }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...ease, delay }}
      style={{
        background: 'var(--surface-0)', border: '1px solid var(--border-subtle)',
        borderLeft: `3px solid ${accent}`, borderRadius: 4, padding: '16px 18px',
        display: 'flex', flexDirection: 'column', gap: 16,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <Icon style={{ width: 14, height: 14, color: accent, flexShrink: 0, marginTop: 2 }} />
        <div>
          <h2 style={{ fontSize: 12, fontWeight: 700, letterSpacing: '0.07em', textTransform: 'uppercase', fontFamily: 'ui-monospace, monospace', color: 'var(--text-secondary)' }}>{title}</h2>
          <p style={{ fontSize: 11, marginTop: 2, color: 'var(--text-tertiary)' }}>{desc}</p>
        </div>
      </div>
      {children}
    </motion.div>
  );
}

function Label({ children }) {
  return (
    <label className="text-11 font-medium block mb-1.5" style={{ color: 'var(--text-tertiary)' }}>
      {children}
    </label>
  );
}

function DenseModeCard() {
  const [compact, setCompact] = useState(
    () => (localStorage.getItem('ui-density') || 'default') === 'compact'
  );

  function toggle() {
    const next = compact ? 'default' : 'compact';
    setCompact(!compact);
    localStorage.setItem('ui-density', next);
    document.documentElement.setAttribute('data-density', next);
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...ease, delay: 0.22 }}
      style={{ background: 'var(--surface-0)', border: '1px solid var(--border-subtle)', borderRadius: 4, padding: '16px 18px' }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-start gap-3">
          <Layers style={{ width: 14, height: 14, color: 'var(--accent-slate)', flexShrink: 0, marginTop: 2 }} />
          <div>
            <h2 className="text-14 font-semibold" style={{ color: 'var(--text-primary)' }}>Dense Mode</h2>
            <p className="text-11 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
              Reduce padding by 25% to show more content on screen. Toggle with <kbd className="font-mono text-10 px-1 py-0.5 rounded" style={{ background: 'var(--surface-2)', color: 'var(--text-secondary)' }}>D</kbd>
            </p>
          </div>
        </div>
        <div
          onClick={toggle}
          className="w-10 h-6 rounded-full relative transition-colors duration-200 cursor-pointer flex-shrink-0"
          style={{ background: compact ? 'var(--accent-violet)' : 'var(--surface-2)' }}
        >
          <div
            className="absolute top-1 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200"
            style={{ transform: compact ? 'translateX(20px)' : 'translateX(4px)' }}
          />
        </div>
      </div>
    </motion.div>
  );
}

// ── Main Component ─────────────────────────────────────────────────────────────

export default function Settings() {
  const { addToast } = useToast();

  // App config state
  const [cfg, setCfg] = useState(null);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    api.getAppConfig()
      .then(data => {
        setCfg(data);
        setDraft({
          base_download_dir:     data.base_download_dir || '',
          ingest_playlist_id:    data.ingest_playlist_id || '',
          spotify_client_id:     '',
          spotify_client_secret: '',
          gemini_api_key:        '',
          lastfm_api_key:        '',
          acoustid_api_key:      '',
          fpcalc_path:           data.fpcalc_path || '',
          telegram_bot_token:    '',
          telegram_chat_id:      data.telegram_chat_id || '',
          check_interval:        data.check_interval || 500,
        });
      })
      .catch(() => {})
      .finally(() => setConfigLoaded(true));
  }, []);

  function set(key, value) { setDraft(p => ({ ...p, [key]: value })); }

  async function handleSave() {
    setSaving(true);
    try {
      const payload = {};
      for (const [k, v] of Object.entries(draft)) {
        if (v !== '' && v !== null && v !== undefined) payload[k] = v;
      }
      const result = await api.saveAppConfig(payload);
      setCfg(result.config);
      addToast({ type: 'success', title: 'Settings saved', description: 'Changes applied immediately — no restart needed.', duration: 4000 });
      setDraft(p => ({
        ...p,
        spotify_client_id: '', spotify_client_secret: '',
        gemini_api_key: '', lastfm_api_key: '', acoustid_api_key: '', telegram_bot_token: '',
      }));
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message, duration: 5000 });
    } finally {
      setSaving(false);
    }
  }

  // Telegram test
  const [testingNotif, setTestingNotif] = useState(false);
  const telegramEnabled = cfg?.telegram_bot_token || draft.telegram_bot_token;

  async function handleTestNotification() {
    setTestingNotif(true);
    try {
      await api.testNotification();
      addToast({ type: 'success', title: 'Test notification sent', duration: 4000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Failed', description: err.message, duration: 4000 });
    } finally {
      setTestingNotif(false);
    }
  }

  // Custom folder mappings
  const [scannedFolders, setScannedFolders] = useState([]);
  const [scanning, setScanning] = useState(false);
  const [mappings, setMappings] = useState([]);
  const [loadingMappings, setLoadingMappings] = useState(true);
  const [editRow, setEditRow] = useState({});
  const [savingRow, setSavingRow] = useState({});
  const [deletingRow, setDeletingRow] = useState({});
  const [newFolder, setNewFolder] = useState('');
  const [newGenre, setNewGenre] = useState('');
  const [addingNew, setAddingNew] = useState(false);

  // Genre rules (artist memory)
  const [overrides, setOverrides] = useState([]);
  const [loadingOverrides, setLoadingOverrides] = useState(true);
  const [deletingOverride, setDeletingOverride] = useState({});

  useEffect(() => {
    api.getCustomFolders()
      .then(d => {
        setMappings(d.mappings || []);
        const init = {};
        (d.mappings || []).forEach(m => { init[m.folder_name] = m.genre_label; });
        setEditRow(init);
      })
      .catch(() => {})
      .finally(() => setLoadingMappings(false));
  }, []);

  async function handleScan() {
    setScanning(true);
    try {
      const d = await api.scanFolders();
      setScannedFolders(d.folders || []);
    } catch (err) {
      addToast({ type: 'error', title: 'Scan failed', description: err.message });
    } finally {
      setScanning(false);
    }
  }

  async function handleSaveRow(folderName) {
    const genreLabel = editRow[folderName] || '';
    if (!genreLabel.trim()) return;
    setSavingRow(p => ({ ...p, [folderName]: true }));
    try {
      await api.saveCustomFolder(folderName, genreLabel.trim());
      setMappings(prev => {
        const exists = prev.find(m => m.folder_name === folderName);
        if (exists) return prev.map(m => m.folder_name === folderName ? { ...m, genre_label: genreLabel.trim() } : m);
        return [...prev, { folder_name: folderName, genre_label: genreLabel.trim() }];
      });
      addToast({ type: 'success', title: 'Mapping saved', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message });
    } finally {
      setSavingRow(p => ({ ...p, [folderName]: false }));
    }
  }

  async function handleDeleteRow(folderName) {
    setDeletingRow(p => ({ ...p, [folderName]: true }));
    try {
      await api.deleteCustomFolder(folderName);
      setMappings(prev => prev.filter(m => m.folder_name !== folderName));
      setEditRow(prev => { const n = { ...prev }; delete n[folderName]; return n; });
      addToast({ type: 'success', title: 'Mapping removed', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Delete failed', description: err.message });
    } finally {
      setDeletingRow(p => ({ ...p, [folderName]: false }));
    }
  }

  async function handleAddNew() {
    if (!newFolder.trim() || !newGenre.trim()) return;
    setAddingNew(true);
    try {
      await api.saveCustomFolder(newFolder.trim(), newGenre.trim());
      setMappings(prev => {
        const exists = prev.find(m => m.folder_name === newFolder.trim());
        if (exists) return prev.map(m => m.folder_name === newFolder.trim() ? { ...m, genre_label: newGenre.trim() } : m);
        return [...prev, { folder_name: newFolder.trim(), genre_label: newGenre.trim() }];
      });
      setEditRow(prev => ({ ...prev, [newFolder.trim()]: newGenre.trim() }));
      setNewFolder(''); setNewGenre('');
      addToast({ type: 'success', title: 'Mapping added', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message });
    } finally {
      setAddingNew(false);
    }
  }

  useEffect(() => {
    api.getGenreOverrides()
      .then(d => setOverrides(d.overrides || []))
      .catch(() => {})
      .finally(() => setLoadingOverrides(false));
  }, []);

  async function handleDeleteOverride(artistKey) {
    setDeletingOverride(p => ({ ...p, [artistKey]: true }));
    try {
      await api.deleteGenreOverride(artistKey);
      setOverrides(prev => prev.filter(o => o.artist_key !== artistKey));
      addToast({ type: 'success', title: 'Rule removed', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Delete failed', description: err.message });
    } finally {
      setDeletingOverride(p => ({ ...p, [artistKey]: false }));
    }
  }

  const mappedNames = new Set(mappings.map(m => m.folder_name));

  if (!configLoaded) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: 'var(--accent-violet)' }} />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-16">

      {/* Header */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={ease}>
        <h1 className="font-display text-22 font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.02em' }}>
          Settings
        </h1>
        <p className="text-12 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>
          Configure everything from here — no file editing needed.
        </p>
      </motion.div>

      {/* Setup incomplete warning */}
      {cfg && !cfg._setup_complete && (
        <motion.div
          initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 px-4 py-3"
          style={{ borderRadius: 4, background: 'var(--accent-amber-dim)', border: '1px solid rgba(245,158,11,0.25)' }}
        >
          <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: 'var(--accent-amber)' }} />
          <p className="text-13" style={{ color: 'var(--text-secondary)' }}>
            <strong style={{ color: 'var(--accent-amber)' }}>Setup not complete.</strong>{' '}
            Fill in your Music Folder and Spotify credentials below to get started.
          </p>
        </motion.div>
      )}

      {/* Music Library */}
      <SectionCard icon={Music2} accent="var(--accent-violet)" dim="var(--accent-violet-dim)"
        title="Music Library" delay={0.04}
        desc="The folder on your computer where all downloaded tracks will be saved and sorted by genre.">
        <div className="space-y-1.5">
          <Label>Music folder path</Label>
          <Input
            value={draft.base_download_dir}
            onChange={e => set('base_download_dir', e.target.value)}
            placeholder="C:\Users\You\DJ Music"
          />
          {cfg?.base_download_dir && (
            <p className="text-10" style={{ color: 'var(--text-muted)' }}>
              Current: <span className="font-mono" style={{ color: 'var(--text-tertiary)' }}>{cfg.base_download_dir}</span>
            </p>
          )}
        </div>
      </SectionCard>

      {/* Spotify */}
      <SectionCard icon={Radio} accent="var(--accent-emerald)" dim="var(--accent-emerald-dim)"
        title="Spotify" delay={0.07}
        desc="Create an app at developer.spotify.com → Dashboard to get your Client ID and Secret.">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <Label>Client ID</Label>
            <SecretInput
              value={draft.spotify_client_id}
              onChange={e => set('spotify_client_id', e.target.value)}
              placeholder={cfg?.spotify_client_id ? 'Saved (enter to update)' : 'Paste Client ID'}
            />
          </div>
          <div>
            <Label>Client Secret</Label>
            <SecretInput
              value={draft.spotify_client_secret}
              onChange={e => set('spotify_client_secret', e.target.value)}
              placeholder={cfg?.spotify_client_secret ? 'Saved (enter to update)' : 'Paste Client Secret'}
            />
          </div>
        </div>
        <div className="text-11 rounded-lg px-3 py-2"
             style={{ background: 'var(--surface-1)', color: 'var(--text-muted)' }}>
          In Spotify Dashboard → Edit Settings → add{' '}
          <code className="font-mono" style={{ color: 'var(--text-accent)' }}>http://127.0.0.1:8888/callback</code>
          {' '}as a Redirect URI.
        </div>
      </SectionCard>

      {/* Auto-Sync Playlist */}
      <SectionCard icon={Radio} accent="var(--accent-cyan)" dim="var(--accent-cyan-dim)"
        title="Auto-Sync Playlist" delay={0.10}
        desc="The app monitors this Spotify playlist and automatically downloads any new tracks added to it.">
        <div>
          <Label>
            Playlist ID{' '}
            <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>
              (from the Spotify URL: open.spotify.com/playlist/<strong>ID</strong>)
            </span>
          </Label>
          <Input
            value={draft.ingest_playlist_id}
            onChange={e => set('ingest_playlist_id', e.target.value)}
            placeholder="6wP8mXlaYeFrbPzEAVSEf8"
          />
          {cfg?.ingest_playlist_id && (
            <p className="text-10 mt-1" style={{ color: 'var(--text-muted)' }}>
              Current: <span className="font-mono" style={{ color: 'var(--text-tertiary)' }}>{cfg.ingest_playlist_id}</span>
            </p>
          )}
        </div>
      </SectionCard>

      {/* API Keys */}
      <SectionCard icon={Key} accent="var(--accent-amber)" dim="var(--accent-amber-dim)"
        title="API Keys" delay={0.13}
        desc="These services help the app detect the correct genre for each track. All are free.">
        <div className="space-y-3">
          <div>
            <Label>Gemini AI Key <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— Used to detect genre when other methods fail. Free at aistudio.google.com</span></Label>
            <SecretInput
              value={draft.gemini_api_key}
              onChange={e => set('gemini_api_key', e.target.value)}
              placeholder={cfg?.gemini_api_key ? 'Saved (enter to update)' : 'Paste Gemini key'}
            />
          </div>
          <div>
            <Label>Last.fm Key <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— Looks up artist genre tags. Free at last.fm/api</span></Label>
            <SecretInput
              value={draft.lastfm_api_key}
              onChange={e => set('lastfm_api_key', e.target.value)}
              placeholder={cfg?.lastfm_api_key ? 'Saved (enter to update)' : 'Paste Last.fm key'}
            />
          </div>
        </div>
      </SectionCard>

      {/* Telegram Notifications */}
      <SectionCard icon={Bell} accent="var(--accent-cyan)" dim="var(--accent-cyan-dim)"
        title="Telegram Notifications" delay={0.16}
        desc="Get alerts when downloads complete or storage is running low. Optional.">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <Label>Bot Token <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(from @BotFather)</span></Label>
            <SecretInput
              value={draft.telegram_bot_token}
              onChange={e => set('telegram_bot_token', e.target.value)}
              placeholder={cfg?.telegram_bot_token ? 'Saved (enter to update)' : '8631466984:AAEz...'}
            />
          </div>
          <div>
            <Label>Chat ID <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>(your Telegram user ID)</span></Label>
            <Input
              value={draft.telegram_chat_id}
              onChange={e => set('telegram_chat_id', e.target.value)}
              placeholder="7438454756"
            />
          </div>
        </div>
        <Button size="sm" variant="outline" disabled={testingNotif || !telegramEnabled} onClick={handleTestNotification}>
          {testingNotif ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Bell className="w-3.5 h-3.5" />}
          Send Test Notification
        </Button>
      </SectionCard>

      {/* Save button */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.18 }}>
        <Button onClick={handleSave} disabled={saving} className="w-full h-11 text-13 font-semibold">
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save All Settings'}
        </Button>
        <p className="text-11 text-center mt-2" style={{ color: 'var(--text-muted)' }}>
          Changes apply immediately — no restart needed.
        </p>
      </motion.div>

      {/* Advanced toggle */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.19 }}>
        <button
          onClick={() => setAdvancedOpen(v => !v)}
          className="flex items-center gap-2 text-11 transition-colors cursor-pointer focus-ring rounded w-full"
          style={{ color: 'var(--text-muted)' }}
          onMouseEnter={e => e.currentTarget.style.color = 'var(--text-tertiary)'}
          onMouseLeave={e => e.currentTarget.style.color = 'var(--text-muted)'}
        >
          <motion.div animate={{ rotate: advancedOpen ? 90 : 0 }} transition={{ duration: 0.15 }}>
            <ChevronRight className="w-3.5 h-3.5" />
          </motion.div>
          Advanced settings (audio fingerprinting, sync interval)
        </button>

        <AnimatePresence>
          {advancedOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="overflow-hidden mt-3"
            >
              <div style={{ borderRadius: 4, padding: '14px 16px', background: 'var(--surface-0)', border: '1px solid var(--border-subtle)' }}
                   className="space-y-4">
                <div>
                  <Label>AcoustID Key <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— Audio fingerprint matching. Free at acoustid.org/api-key</span></Label>
                  <SecretInput
                    value={draft.acoustid_api_key}
                    onChange={e => set('acoustid_api_key', e.target.value)}
                    placeholder={cfg?.acoustid_api_key ? 'Saved (enter to update)' : 'Paste AcoustID key'}
                  />
                </div>
                <div>
                  <Label>fpcalc.exe path <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— Required for audio fingerprinting. Download from acoustid.org/chromaprint</span></Label>
                  <Input
                    value={draft.fpcalc_path}
                    onChange={e => set('fpcalc_path', e.target.value)}
                    placeholder="C:\path\to\fpcalc.exe"
                  />
                </div>
                <div>
                  <Label>Playlist check interval <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>— How often to check for new tracks (ms, default 500)</span></Label>
                  <Input
                    type="number"
                    value={draft.check_interval}
                    onChange={e => set('check_interval', parseInt(e.target.value) || 500)}
                    className="w-36"
                  />
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* My Genre Rules */}
      <SectionCard icon={BookOpen} accent="var(--accent-amber)" dim="var(--accent-amber-dim)"
        title="My Genre Rules" delay={0.19}
        desc="Artists the app has learned from your 'Move & Remember' corrections. These rules auto-route future downloads.">
        {loadingOverrides ? (
          <div className="flex items-center gap-2 py-2">
            <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />
            <span className="text-12" style={{ color: 'var(--text-tertiary)' }}>Loading…</span>
          </div>
        ) : overrides.length === 0 ? (
          <div className="flex items-start gap-3 py-3">
            <BookOpen className="w-8 h-8 flex-shrink-0 mt-0.5" style={{ color: 'var(--text-muted)' }} />
            <div>
              <p className="text-13 font-medium" style={{ color: 'var(--text-tertiary)' }}>No rules yet</p>
              <p className="text-11 mt-0.5" style={{ color: 'var(--text-muted)' }}>
                Use <strong style={{ color: 'var(--text-tertiary)' }}>Move &amp; Remember</strong> on the Library or Review pages.
                Each time you correct a track's genre, the app learns and routes that artist automatically.
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-1.5">
            <p className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>
              {overrides.length} saved rule{overrides.length !== 1 ? 's' : ''}
            </p>
            <div className="max-h-64 overflow-y-auto scrollbar-thin space-y-1 pr-1">
              {overrides.map(o => {
                const confPct = Math.round((o.confidence || 0) * 100);
                return (
                  <div
                    key={o.artist_key}
                    className="flex items-center gap-2 py-2 px-3 rounded-lg transition-colors duration-150 group/rule"
                    style={{ background: 'var(--surface-1)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'var(--surface-1)'}
                  >
                    <div className="flex-1 min-w-0">
                      <span className="text-12 font-medium truncate block" style={{ color: 'var(--text-primary)' }}>
                        {o.artist_key}
                      </span>
                      <div className="flex items-center gap-2 mt-0.5">
                        <div className="w-16 h-1 rounded-full overflow-hidden" style={{ background: 'var(--surface-3)' }}>
                          <div className="h-full rounded-full transition-all duration-300"
                               style={{ width: `${confPct}%`, background: 'var(--accent-violet)' }} />
                        </div>
                        <span className="text-10" style={{ color: 'var(--text-muted)' }}>
                          {confPct}% · {o.move_count ?? 1} move{(o.move_count ?? 1) !== 1 ? 's' : ''}
                        </span>
                      </div>
                    </div>
                    <span className="text-10 font-semibold px-2 py-0.5 rounded-full uppercase tracking-wide flex-shrink-0"
                          style={{ background: 'var(--accent-violet-dim)', color: 'var(--accent-violet)', border: '1px solid rgba(139,92,246,0.2)' }}>
                      {o.genre}
                    </span>
                    <button
                      aria-label={`Remove rule for ${o.artist_key}`}
                      onClick={() => handleDeleteOverride(o.artist_key)}
                      disabled={deletingOverride[o.artist_key]}
                      className="p-1.5 min-w-[28px] min-h-[28px] sm:opacity-0 sm:group-hover/rule:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 cursor-pointer rounded-lg disabled:opacity-30 flex-shrink-0 focus-ring"
                      style={{ color: 'var(--text-muted)' }}
                      onMouseEnter={e => { e.currentTarget.style.color = 'var(--accent-rose)'; e.currentTarget.style.background = 'var(--accent-rose-dim)'; }}
                      onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}
                    >
                      {deletingOverride[o.artist_key]
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <Trash2 className="w-3.5 h-3.5" />}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </SectionCard>

      {/* Custom Folder Mappings */}
      <SectionCard icon={FolderOpen} accent="var(--accent-violet)" dim="var(--accent-violet-dim)"
        title="Custom Folder Mappings" delay={0.2}
        desc="Map your existing DJ folder names to genre labels so downloads go to the right place.">

        <Button size="sm" variant="outline" disabled={scanning} onClick={handleScan}>
          {scanning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderSearch className="w-3.5 h-3.5" />}
          Scan My Music Folder
        </Button>

        <AnimatePresence>
          {scannedFolders.length > 0 && (
            <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="space-y-2">
              <p className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>Detected folders — click to pre-fill:</p>
              <div className="flex flex-wrap gap-2">
                {scannedFolders.map(f => (
                  <button
                    key={f}
                    onClick={() => { setNewFolder(f); setNewGenre(f); }}
                    className="px-2.5 py-1 text-11 rounded-lg transition-all duration-150 cursor-pointer focus-ring"
                    style={mappedNames.has(f)
                      ? { border: '1px solid rgba(16,185,129,0.3)', color: 'var(--accent-emerald)', background: 'var(--accent-emerald-dim)' }
                      : { border: '1px solid var(--border-default)', color: 'var(--text-tertiary)', background: 'transparent' }
                    }
                    onMouseEnter={e => { if (!mappedNames.has(f)) { e.currentTarget.style.borderColor = 'rgba(139,92,246,0.4)'; e.currentTarget.style.color = 'var(--accent-violet)'; }}}
                    onMouseLeave={e => { if (!mappedNames.has(f)) { e.currentTarget.style.borderColor = 'var(--border-default)'; e.currentTarget.style.color = 'var(--text-tertiary)'; }}}
                  >
                    {mappedNames.has(f) && <CheckCircle2 className="w-3 h-3 inline mr-1" />}{f}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {loadingMappings ? (
          <div className="flex items-center gap-2 py-2">
            <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />
            <span className="text-12" style={{ color: 'var(--text-tertiary)' }}>Loading…</span>
          </div>
        ) : mappings.length > 0 ? (
          <div className="space-y-2">
            <p className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>Saved mappings:</p>
            {mappings.map(m => (
              <div key={m.folder_name} className="flex items-center gap-2">
                <div className="flex-1 text-11 font-mono truncate px-3 py-2 rounded-lg min-w-0"
                     style={{ background: 'var(--surface-1)', color: 'var(--text-secondary)' }}>
                  {m.folder_name}
                </div>
                <span className="text-10 flex-shrink-0" style={{ color: 'var(--text-muted)' }}>→</span>
                <Input
                  value={editRow[m.folder_name] ?? m.genre_label}
                  onChange={e => setEditRow(p => ({ ...p, [m.folder_name]: e.target.value }))}
                  className="w-40 h-8 text-11"
                  placeholder="Genre label"
                />
                <Button size="sm" disabled={savingRow[m.folder_name]} onClick={() => handleSaveRow(m.folder_name)}
                        className="h-8 px-2.5 flex-shrink-0">
                  {savingRow[m.folder_name] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                </Button>
                <Button size="sm" variant="ghost" disabled={deletingRow[m.folder_name]} onClick={() => handleDeleteRow(m.folder_name)}
                        className="h-8 px-2 flex-shrink-0" style={{ color: 'var(--accent-rose)' }}>
                  {deletingRow[m.folder_name] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-11 py-1" style={{ color: 'var(--text-muted)' }}>No custom mappings yet.</p>
        )}

        <div className="space-y-2 pt-3" style={{ borderTop: '1px solid var(--border-subtle)' }}>
          <p className="text-11 font-medium" style={{ color: 'var(--text-tertiary)' }}>Add manually:</p>
          <div className="flex items-center gap-2">
            <Input
              value={newFolder}
              onChange={e => setNewFolder(e.target.value)}
              placeholder="Folder name (e.g. Drum and Bass)"
              className="flex-1 h-8 text-11"
            />
            <span className="text-10 flex-shrink-0" style={{ color: 'var(--text-muted)' }}>→</span>
            <Input
              value={newGenre}
              onChange={e => setNewGenre(e.target.value)}
              placeholder="Genre label"
              className="w-36 h-8 text-11"
              onKeyDown={e => e.key === 'Enter' && handleAddNew()}
            />
            <Button size="sm" disabled={addingNew || !newFolder.trim() || !newGenre.trim()} onClick={handleAddNew}
                    className="h-8 px-3 flex-shrink-0">
              {addingNew ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
            </Button>
          </div>
        </div>
      </SectionCard>

      {/* Interface */}
      <DenseModeCard />
    </div>
  );
}
