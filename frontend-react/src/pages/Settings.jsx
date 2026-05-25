import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  FolderSearch,
  Plus,
  Trash2,
  Save,
  Bell,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  FolderOpen,
  Eye,
  EyeOff,
  Music2,
  Key,
  Radio,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  BookOpen,
} from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { api } from '@/services/api';
import { useToast } from '@/components/ui/toast';
import { cn } from '@/lib/utils';

// ── Helpers ──────────────────────────────────────────────────────────────────

function SecretInput({ value, onChange, placeholder, className }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <Input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className={cn('pr-9', className)}
      />
      <button
        type="button"
        onClick={() => setShow((v) => !v)}
        className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300 transition-colors"
      >
        {show ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

function SectionCard({ icon: Icon, color, title, desc, children }) {
  return (
    <Card className="border border-gray-700/50 bg-white/5 p-6 space-y-5">
      <div className="flex items-start gap-3">
        <div className={cn('p-2.5 rounded-xl', color)}>
          <Icon className="w-5 h-5" />
        </div>
        <div>
          <h2 className="font-semibold text-gray-100">{title}</h2>
          <p className="text-xs text-gray-500 mt-0.5">{desc}</p>
        </div>
      </div>
      {children}
    </Card>
  );
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function Settings() {
  const { addToast } = useToast();

  // ── App config state ─────────────────────────────────────────────────────
  const [cfg, setCfg] = useState(null);       // loaded from backend
  const [draft, setDraft] = useState({});      // edits before save
  const [saving, setSaving] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => {
    api.getAppConfig()
      .then((data) => {
        setCfg(data);
        // Seed draft with current values so fields show what's set
        setDraft({
          base_download_dir:           data.base_download_dir || '',
          ingest_playlist_id:          data.ingest_playlist_id || '',
          spotify_client_id:           '',  // never pre-fill secrets
          spotify_client_secret:       '',
          gemini_api_key:              '',
          lastfm_api_key:              '',
          acoustid_api_key:            '',
          fpcalc_path:                 data.fpcalc_path || '',
          telegram_bot_token:          '',
          telegram_chat_id:            data.telegram_chat_id || '',
          check_interval:              data.check_interval || 500,
        });
      })
      .catch(() => {})
      .finally(() => setConfigLoaded(true));
  }, []);

  function set(key, value) {
    setDraft((p) => ({ ...p, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      // Only send fields that the user actually typed something into
      const payload = {};
      for (const [k, v] of Object.entries(draft)) {
        // For secrets, only send if the user typed a new value (not the masked placeholder)
        if (v !== '' && v !== null && v !== undefined) {
          payload[k] = v;
        }
      }
      const result = await api.saveAppConfig(payload);
      setCfg(result.config);
      addToast({ type: 'success', title: 'Settings saved', description: 'Changes applied immediately — no restart needed.', duration: 4000 });
      // Clear secret fields after save
      setDraft((p) => ({
        ...p,
        spotify_client_id: '',
        spotify_client_secret: '',
        gemini_api_key: '',
        lastfm_api_key: '',
        acoustid_api_key: '',
        telegram_bot_token: '',
      }));
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message, duration: 5000 });
    } finally {
      setSaving(false);
    }
  }

  // ── Telegram test ────────────────────────────────────────────────────────
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

  // ── Custom folder mappings ───────────────────────────────────────────────
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

  // ── Genre rules (artist memory) ──────────────────────────────────────────
  const [overrides, setOverrides] = useState([]);
  const [loadingOverrides, setLoadingOverrides] = useState(true);
  const [deletingOverride, setDeletingOverride] = useState({});

  useEffect(() => {
    api.getCustomFolders()
      .then((d) => {
        setMappings(d.mappings || []);
        const init = {};
        (d.mappings || []).forEach((m) => { init[m.folder_name] = m.genre_label; });
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
    setSavingRow((p) => ({ ...p, [folderName]: true }));
    try {
      await api.saveCustomFolder(folderName, genreLabel.trim());
      setMappings((prev) => {
        const exists = prev.find((m) => m.folder_name === folderName);
        if (exists) return prev.map((m) => m.folder_name === folderName ? { ...m, genre_label: genreLabel.trim() } : m);
        return [...prev, { folder_name: folderName, genre_label: genreLabel.trim() }];
      });
      addToast({ type: 'success', title: 'Mapping saved', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Save failed', description: err.message });
    } finally {
      setSavingRow((p) => ({ ...p, [folderName]: false }));
    }
  }

  async function handleDeleteRow(folderName) {
    setDeletingRow((p) => ({ ...p, [folderName]: true }));
    try {
      await api.deleteCustomFolder(folderName);
      setMappings((prev) => prev.filter((m) => m.folder_name !== folderName));
      setEditRow((prev) => { const n = { ...prev }; delete n[folderName]; return n; });
      addToast({ type: 'success', title: 'Mapping removed', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Delete failed', description: err.message });
    } finally {
      setDeletingRow((p) => ({ ...p, [folderName]: false }));
    }
  }

  async function handleAddNew() {
    if (!newFolder.trim() || !newGenre.trim()) return;
    setAddingNew(true);
    try {
      await api.saveCustomFolder(newFolder.trim(), newGenre.trim());
      setMappings((prev) => {
        const exists = prev.find((m) => m.folder_name === newFolder.trim());
        if (exists) return prev.map((m) => m.folder_name === newFolder.trim() ? { ...m, genre_label: newGenre.trim() } : m);
        return [...prev, { folder_name: newFolder.trim(), genre_label: newGenre.trim() }];
      });
      setEditRow((prev) => ({ ...prev, [newFolder.trim()]: newGenre.trim() }));
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
      .then((d) => setOverrides(d.overrides || []))
      .catch(() => {})
      .finally(() => setLoadingOverrides(false));
  }, []);

  async function handleDeleteOverride(artistKey) {
    setDeletingOverride((p) => ({ ...p, [artistKey]: true }));
    try {
      await api.deleteGenreOverride(artistKey);
      setOverrides((prev) => prev.filter((o) => o.artist_key !== artistKey));
      addToast({ type: 'success', title: 'Rule removed', duration: 3000 });
    } catch (err) {
      addToast({ type: 'error', title: 'Delete failed', description: err.message });
    } finally {
      setDeletingOverride((p) => ({ ...p, [artistKey]: false }));
    }
  }

  const mappedNames = new Set(mappings.map((m) => m.folder_name));

  const inputCls = 'h-9 text-sm bg-white/5 border-gray-700 text-white placeholder:text-gray-600';

  if (!configLoaded) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-6 h-6 text-purple-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
        <h1 className="text-4xl font-bold bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent">
          Settings
        </h1>
        <p className="text-gray-400 mt-2">Configure everything from here — no file editing needed.</p>
      </motion.div>

      {/* Setup incomplete warning */}
      {cfg && !cfg._setup_complete && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-start gap-3 px-4 py-3 rounded-xl bg-amber-500/10 border border-amber-500/20"
        >
          <AlertTriangle className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" />
          <div className="text-sm text-amber-300">
            <strong>Setup not complete.</strong> Fill in your Music Folder and Spotify credentials below to get started.
          </div>
        </motion.div>
      )}

      {/* ── Music Library ───────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.04 }}>
        <SectionCard icon={Music2} color="bg-purple-500/20 text-purple-400" title="Music Library"
          desc="The folder on your computer where all downloaded tracks will be saved and sorted by genre.">
          <div className="space-y-2">
            <label className="text-xs text-gray-400">Music folder path</label>
            <Input
              value={draft.base_download_dir}
              onChange={(e) => set('base_download_dir', e.target.value)}
              placeholder="C:\Users\You\DJ Music"
              className={inputCls}
            />
            {cfg?.base_download_dir && (
              <p className="text-xs text-gray-600">Current: <span className="text-gray-400 font-mono">{cfg.base_download_dir}</span></p>
            )}
          </div>
        </SectionCard>
      </motion.div>

      {/* ── Spotify ─────────────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.07 }}>
        <SectionCard icon={Radio} color="bg-green-500/20 text-green-400" title="Spotify"
          desc="Create an app at developer.spotify.com → Dashboard to get your Client ID and Secret.">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Client ID</label>
              <SecretInput
                value={draft.spotify_client_id}
                onChange={(e) => set('spotify_client_id', e.target.value)}
                placeholder={cfg?.spotify_client_id ? 'Saved (enter to update)' : 'Paste Client ID'}
                className={inputCls}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Client Secret</label>
              <SecretInput
                value={draft.spotify_client_secret}
                onChange={(e) => set('spotify_client_secret', e.target.value)}
                placeholder={cfg?.spotify_client_secret ? 'Saved (enter to update)' : 'Paste Client Secret'}
                className={inputCls}
              />
            </div>
          </div>
          <div className="text-xs text-gray-600 bg-gray-900/40 rounded-lg px-3 py-2">
            In Spotify Dashboard → Edit Settings → add <code className="text-gray-400">http://127.0.0.1:8888/callback</code> as a Redirect URI.
          </div>
        </SectionCard>
      </motion.div>

      {/* ── Playlists ────────────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.10 }}>
        <SectionCard icon={Radio} color="bg-blue-500/20 text-blue-400" title="Auto-Sync Playlist"
          desc="The app monitors this Spotify playlist and automatically downloads any new tracks added to it.">
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Playlist ID <span className="text-gray-600">(from the Spotify URL: open.spotify.com/playlist/<strong>ID</strong>)</span></label>
              <Input
                value={draft.ingest_playlist_id}
                onChange={(e) => set('ingest_playlist_id', e.target.value)}
                placeholder="6wP8mXlaYeFrbPzEAVSEf8"
                className={inputCls}
              />
              {cfg?.ingest_playlist_id && (
                <p className="text-xs text-gray-600">Current: <span className="text-gray-400 font-mono">{cfg.ingest_playlist_id}</span></p>
              )}
            </div>
          </div>
        </SectionCard>
      </motion.div>

      {/* ── API Keys ────────────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.13 }}>
        <SectionCard icon={Key} color="bg-amber-500/20 text-amber-400" title="API Keys"
          desc="These services help the app detect the correct genre for each track. All are free.">
          <div className="space-y-3">
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">
                Gemini AI Key <span className="text-gray-600">— Used to detect genre when other methods fail. Free at aistudio.google.com</span>
              </label>
              <SecretInput
                value={draft.gemini_api_key}
                onChange={(e) => set('gemini_api_key', e.target.value)}
                placeholder={cfg?.gemini_api_key ? 'Saved (enter to update)' : 'Paste Gemini key'}
                className={inputCls}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">
                Last.fm Key <span className="text-gray-600">— Looks up artist genre tags. Free at last.fm/api</span>
              </label>
              <SecretInput
                value={draft.lastfm_api_key}
                onChange={(e) => set('lastfm_api_key', e.target.value)}
                placeholder={cfg?.lastfm_api_key ? 'Saved (enter to update)' : 'Paste Last.fm key'}
                className={inputCls}
              />
            </div>
          </div>
        </SectionCard>
      </motion.div>

      {/* ── Telegram Notifications ───────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.16 }}>
        <SectionCard icon={Bell} color="bg-sky-500/20 text-sky-400" title="Telegram Notifications"
          desc="Get alerts when downloads complete or storage is running low. Optional.">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Bot Token <span className="text-gray-600">(from @BotFather)</span></label>
              <SecretInput
                value={draft.telegram_bot_token}
                onChange={(e) => set('telegram_bot_token', e.target.value)}
                placeholder={cfg?.telegram_bot_token ? 'Saved (enter to update)' : '8631466984:AAEz...'}
                className={inputCls}
              />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs text-gray-400">Chat ID <span className="text-gray-600">(your Telegram user ID)</span></label>
              <Input
                value={draft.telegram_chat_id}
                onChange={(e) => set('telegram_chat_id', e.target.value)}
                placeholder="7438454756"
                className={inputCls}
              />
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={testingNotif || !telegramEnabled}
            onClick={handleTestNotification}
            className="border-gray-700 text-gray-300 hover:bg-white/5 disabled:opacity-40"
          >
            {testingNotif ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Bell className="w-3.5 h-3.5 mr-1.5" />}
            Send Test Notification
          </Button>
        </SectionCard>
      </motion.div>

      {/* ── Save button ──────────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.18 }}>
        <Button
          onClick={handleSave}
          disabled={saving}
          className="w-full bg-purple-600 hover:bg-purple-700 text-white h-11 text-sm font-semibold disabled:opacity-50"
        >
          {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
          {saving ? 'Saving…' : 'Save All Settings'}
        </Button>
        <p className="text-xs text-gray-600 text-center mt-2">Changes apply immediately — no restart needed.</p>
      </motion.div>

      {/* ── Advanced / Developer Settings ────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.19 }}>
        <button
          onClick={() => setAdvancedOpen((v) => !v)}
          className="flex items-center gap-2 text-xs text-gray-500 hover:text-gray-300 transition-colors w-full"
        >
          {advancedOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
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
              <Card className="border border-gray-700/50 bg-white/5 p-5 space-y-4">
                <div className="space-y-1.5">
                  <label className="text-xs text-gray-400">
                    AcoustID Key <span className="text-gray-600">— Audio fingerprint matching. Free at acoustid.org/api-key</span>
                  </label>
                  <SecretInput
                    value={draft.acoustid_api_key}
                    onChange={(e) => set('acoustid_api_key', e.target.value)}
                    placeholder={cfg?.acoustid_api_key ? 'Saved (enter to update)' : 'Paste AcoustID key'}
                    className={inputCls}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-gray-400">
                    fpcalc.exe path <span className="text-gray-600">— Required for audio fingerprinting. Download from acoustid.org/chromaprint</span>
                  </label>
                  <Input
                    value={draft.fpcalc_path}
                    onChange={(e) => set('fpcalc_path', e.target.value)}
                    placeholder="C:\path\to\fpcalc.exe"
                    className={inputCls}
                  />
                </div>
                <div className="space-y-1.5">
                  <label className="text-xs text-gray-400">
                    Playlist check interval <span className="text-gray-600">— How often to check for new tracks (in milliseconds, default 500)</span>
                  </label>
                  <Input
                    type="number"
                    value={draft.check_interval}
                    onChange={(e) => set('check_interval', parseInt(e.target.value) || 500)}
                    className={cn(inputCls, 'w-36')}
                  />
                </div>
              </Card>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>

      {/* ── My Genre Rules ──────────────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.19 }}>
        <SectionCard icon={BookOpen} color="bg-amber-500/20 text-amber-400" title="My Genre Rules"
          desc="Artists the app has learned from your 'Move & Remember' corrections. These rules auto-route future downloads.">
          {loadingOverrides ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm py-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading…
            </div>
          ) : overrides.length === 0 ? (
            <div className="flex items-start gap-3 py-3 text-left">
              <BookOpen className="w-8 h-8 text-gray-700 shrink-0 mt-0.5" />
              <div>
                <p className="text-sm text-gray-500 font-medium">No rules yet</p>
                <p className="text-xs text-gray-600 mt-0.5">
                  Use <span className="text-gray-400 font-medium">Move &amp; Remember</span> on the Library or Review pages. Each time you correct a track's genre, the app learns and routes that artist automatically from then on.
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-1.5">
              <p className="text-xs text-gray-500 font-medium">{overrides.length} saved rule{overrides.length !== 1 ? 's' : ''}</p>
              <div className="max-h-64 overflow-y-auto space-y-1 pr-1">
                {overrides.map((o) => {
                  const confPct = Math.round((o.confidence || 0) * 100);
                  return (
                    <div key={o.artist_key} className="flex items-center gap-2 py-2 px-3 rounded-lg bg-gray-900/40 hover:bg-gray-900/70 transition-colors duration-150 group/rule">
                      <div className="flex-1 min-w-0">
                        <span className="text-sm text-gray-200 font-medium truncate block">{o.artist_key}</span>
                        <div className="flex items-center gap-2 mt-0.5">
                          {/* confidence mini-bar */}
                          <div className="w-16 h-1 rounded-full bg-gray-700 overflow-hidden">
                            <div
                              className="h-full rounded-full bg-purple-500 transition-all duration-300"
                              style={{ width: `${confPct}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-gray-600">
                            {confPct}% · {o.move_count ?? 1} move{(o.move_count ?? 1) !== 1 ? 's' : ''}
                          </span>
                        </div>
                      </div>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-purple-500/20 text-purple-300 border border-purple-500/20 shrink-0">
                        {o.genre}
                      </span>
                      <button
                        aria-label={`Remove rule for ${o.artist_key}`}
                        onClick={() => handleDeleteOverride(o.artist_key)}
                        disabled={deletingOverride[o.artist_key]}
                        className="p-1.5 min-w-[28px] min-h-[28px] text-gray-600 hover:text-red-400 sm:opacity-0 sm:group-hover/rule:opacity-100 focus:opacity-100 active:scale-95 transition-all duration-150 cursor-pointer rounded-lg hover:bg-red-500/10 disabled:opacity-30 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/50"
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
      </motion.div>

      {/* ── Custom Folder Mappings ───────────────────────────────────────────── */}
      <motion.div initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }}>
        <SectionCard icon={FolderOpen} color="bg-purple-500/20 text-purple-400" title="Custom Folder Mappings"
          desc="Map your existing DJ folder names to genre labels so downloads go to the right place.">

          <Button size="sm" variant="outline" disabled={scanning} onClick={handleScan}
            className="border-gray-700 text-gray-300 hover:bg-white/5">
            {scanning ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <FolderSearch className="w-3.5 h-3.5 mr-1.5" />}
            Scan My Music Folder
          </Button>

          <AnimatePresence>
            {scannedFolders.length > 0 && (
              <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="space-y-2">
                <p className="text-xs text-gray-500 font-medium">Detected folders — click to pre-fill:</p>
                <div className="flex flex-wrap gap-2">
                  {scannedFolders.map((f) => (
                    <button key={f} onClick={() => { setNewFolder(f); setNewGenre(f); }}
                      className={cn('px-2.5 py-1 text-xs rounded-lg border transition-all',
                        mappedNames.has(f) ? 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10'
                          : 'border-gray-700 text-gray-400 hover:border-purple-500/50 hover:text-purple-300')}>
                      {mappedNames.has(f) && <CheckCircle2 className="w-3 h-3 inline mr-1" />}{f}
                    </button>
                  ))}
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {loadingMappings ? (
            <div className="flex items-center gap-2 text-gray-500 text-sm py-2">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading…
            </div>
          ) : mappings.length > 0 ? (
            <div className="space-y-2">
              <p className="text-xs text-gray-500 font-medium">Saved mappings:</p>
              {mappings.map((m) => (
                <div key={m.folder_name} className="flex items-center gap-2">
                  <div className="flex-1 text-xs font-mono text-gray-300 bg-gray-900/40 rounded-lg px-3 py-2 min-w-0 truncate">{m.folder_name}</div>
                  <span className="text-gray-600 text-xs shrink-0">→</span>
                  <Input value={editRow[m.folder_name] ?? m.genre_label}
                    onChange={(e) => setEditRow((p) => ({ ...p, [m.folder_name]: e.target.value }))}
                    className="w-40 h-8 text-xs bg-white/5 border-gray-700 text-white" placeholder="Genre label" />
                  <Button size="sm" disabled={savingRow[m.folder_name]} onClick={() => handleSaveRow(m.folder_name)}
                    className="h-8 px-2.5 bg-purple-600 hover:bg-purple-700 text-white shrink-0">
                    {savingRow[m.folder_name] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                  </Button>
                  <Button size="sm" variant="ghost" disabled={deletingRow[m.folder_name]} onClick={() => handleDeleteRow(m.folder_name)}
                    className="h-8 px-2 text-red-400 hover:text-red-300 hover:bg-red-500/10 shrink-0">
                    {deletingRow[m.folder_name] ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3" />}
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-gray-600 py-1">No custom mappings yet.</p>
          )}

          <div className="space-y-2 pt-2 border-t border-gray-700/50">
            <p className="text-xs text-gray-500 font-medium">Add manually:</p>
            <div className="flex items-center gap-2">
              <Input value={newFolder} onChange={(e) => setNewFolder(e.target.value)}
                placeholder="Folder name (e.g. Drum and Bass)"
                className="flex-1 h-8 text-xs bg-white/5 border-gray-700 text-white placeholder:text-gray-600" />
              <span className="text-gray-600 text-xs shrink-0">→</span>
              <Input value={newGenre} onChange={(e) => setNewGenre(e.target.value)}
                placeholder="Genre label"
                className="w-36 h-8 text-xs bg-white/5 border-gray-700 text-white placeholder:text-gray-600"
                onKeyDown={(e) => e.key === 'Enter' && handleAddNew()} />
              <Button size="sm" disabled={addingNew || !newFolder.trim() || !newGenre.trim()} onClick={handleAddNew}
                className="h-8 px-3 bg-purple-600 hover:bg-purple-700 text-white shrink-0 disabled:opacity-40">
                {addingNew ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
              </Button>
            </div>
          </div>
        </SectionCard>
      </motion.div>
    </div>
  );
}
