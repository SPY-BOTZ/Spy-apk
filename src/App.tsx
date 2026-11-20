import { useMemo, useState } from 'react';
import { Check, Copy, Download, FileCode2, FolderTree, Languages, Search } from 'lucide-react';
import { BOT_FILES, type BotFileEntry } from './lib/botFiles';
import {
  SUPPORTED_LANGUAGES,
  SUPPORTED_QUALITIES,
  buildFirebasePayloadPreview,
  parseMediaPreview,
} from './lib/mediaParser';

type WorkspaceTab = 'files' | 'simulator' | 'deploy';

const COMMON_TOGGLE_LANGUAGES = ['Hindi', 'English', 'Tamil', 'Telugu', 'Malayalam'] as const;

const PRESET_SAMPLES = [
  {
    label: 'Inception (Dual Audio 1080p)',
    filename: 'Inception.2010.1080p.BluRay.Dual.Audio.Hindi.Eng.x264-YIFY.mkv',
    caption: '',
  },
  {
    label: 'Jawan (Needs Interactive Selection)',
    filename: 'Jawan (2023) HDRip.mp4',
    caption: '',
  },
  {
    label: 'Mirzapur S03 (4K Hindi Series)',
    filename: 'Mirzapur.S03E01.2024.2160p.WEB-DL.Hindi.HEVC.mkv',
    caption: '',
  },
  {
    label: 'Tamil Caption Override',
    filename: 'Leo_2023_Master.mkv',
    caption: 'Leo 2023 Tamil 720p WEB-DL',
  },
];

const ENV_VARS_SPEC = [
  { key: 'API_ID', required: true, example: '12345678', note: 'Numeric Telegram App ID from my.telegram.org' },
  { key: 'API_HASH', required: true, example: '0123456789abcdef...', note: 'Telegram App API Hash from my.telegram.org' },
  { key: 'BOT_TOKEN', required: true, example: '123456:ABC-DEF...', note: 'Bot token issued by @BotFather' },
  { key: 'OWNER_ID', required: true, example: '987654321', note: 'Permanent super-admin numeric Telegram user ID' },
  { key: 'STORAGE_CHANNEL_ID', required: true, example: '-1001234567890', note: 'Channel ID where bot is an administrator' },
  { key: 'TMDB_API_KEY', required: true, example: 'a1b2c3d4...', note: 'TMDB v3 API key for movie/TV metadata & posters' },
  { key: 'FIREBASE_DB_URL', required: true, example: 'https://proj-default-rtdb.firebaseio.com', note: 'Firebase Realtime Database root URL' },
  { key: 'FIREBASE_DATABASE_SECRET', required: true, example: 'your_rtdb_secret', note: 'Firebase RTDB secret passed via ?auth=' },
  { key: 'PORT', required: false, example: '8000', note: 'HTTP bind port (automatically injected by Koyeb)' },
  { key: 'BASE_URL', required: false, example: 'https://your-app.koyeb.app', note: 'Optional public URL; auto-uses KOYEB_PUBLIC_DOMAIN' },
];

const DEPLOY_STEPS = [
  {
    id: 'gh-push',
    title: '01. Push repository to GitHub',
    detail:
      'Push this repository to GitHub (either the full workspace with telegram-movie-bot/ and root Dockerfile, or only the contents of telegram-movie-bot/ at repo root).',
  },
  {
    id: 'koyeb-builder',
    title: '02. Configure Koyeb Builder (Fixes Node.js lockfile error)',
    detail:
      'If deploying the full workspace repo, in Koyeb Service Settings -> Build & Deployment either set Buildpack Work directory to "telegram-movie-bot" OR switch Builder to "Dockerfile" (uses root Dockerfile).',
  },
  {
    id: 'tg-channel',
    title: '03. Prepare Telegram Storage Channel and Bot Token',
    detail:
      'Create a private Telegram channel, add your bot as an administrator with post/edit permissions, and record its numeric -100... channel ID for STORAGE_CHANNEL_ID.',
  },
  {
    id: 'koyeb-env',
    title: '04. Configure Environment Variables & Health Check',
    detail:
      'Add all required secrets in Koyeb Environment Variables, set Health Check Path to /health on port 8000, and deploy.',
  },
  {
    id: 'verify-bot',
    title: '05. Verify /health, /status, /addadmin, and File Upload',
    detail:
      'Confirm https://<your-app>.koyeb.app/health returns HTTP 200 JSON, run /status in Telegram as OWNER_ID, test /addadmin <user_id>, and forward a movie file.',
  },
];

function triggerDownload(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export default function App() {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('files');
  const [selectedPath, setSelectedPath] = useState<string>(BOT_FILES[0].path);
  const [fileFilter, setFileFilter] = useState<string>('');
  const [copiedId, setCopiedId] = useState<string | null>(null);

  // Simulator state
  const [simFilename, setSimFilename] = useState<string>(PRESET_SAMPLES[0].filename);
  const [simCaption, setSimCaption] = useState<string>(PRESET_SAMPLES[0].caption);
  const [simBaseUrl, setSimBaseUrl] = useState<string>('https://movie-catalog-bot.koyeb.app');
  const [simMessageId, setSimMessageId] = useState<number>(482);
  const [overrideLanguage, setOverrideLanguage] = useState<string | null>(null);
  const [overrideQuality, setOverrideQuality] = useState<string | null>(null);

  // Checklist state
  const [completedSteps, setCompletedSteps] = useState<Record<string, boolean>>({});

  const selectedFile: BotFileEntry = useMemo(
    () => BOT_FILES.find((f) => f.path === selectedPath) ?? BOT_FILES[0],
    [selectedPath]
  );

  const filteredFiles = useMemo(() => {
    const q = fileFilter.trim().toLowerCase();
    if (!q) return BOT_FILES;
    return BOT_FILES.filter(
      (f) => f.path.toLowerCase().includes(q) || f.summary.toLowerCase().includes(q)
    );
  }, [fileFilter]);

  const parsedPreview = useMemo(
    () => parseMediaPreview(simFilename, simCaption),
    [simFilename, simCaption]
  );

  const effectiveLanguage = overrideLanguage ?? parsedPreview.language ?? 'Hindi';
  const effectiveQuality = overrideQuality ?? parsedPreview.quality ?? '720p';

  const firebaseRecord = useMemo(
    () =>
      buildFirebasePayloadPreview({
        title: parsedPreview.title,
        year: parsedPreview.year ?? '2024',
        language: effectiveLanguage,
        quality: effectiveQuality,
        isSeries: parsedPreview.isSeries,
        baseUrl: simBaseUrl,
        messageId: simMessageId,
      }),
    [parsedPreview, effectiveLanguage, effectiveQuality, simBaseUrl, simMessageId]
  );

  const copyText = async (id: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedId(id);
      window.setTimeout(() => {
        setCopiedId((prev) => (prev === id ? null : prev));
      }, 1600);
    } catch {
      // Ignore clipboard permission errors in restricted contexts
    }
  };

  const handleDownloadAllScript = () => {
    const script = [
      '#!/usr/bin/env bash',
      'set -euo pipefail',
      'mkdir -p bot tests',
      ...BOT_FILES.map((file) => {
        const marker = `EOF_${file.path.replace(/[^A-Za-z0-9]/g, '_').toUpperCase()}`;
        return `cat <<'${marker}' > "${file.path}"\n${file.content.replace(/\n$/, '')}\n${marker}`;
      }),
      'echo "Created all telegram-movie-bot files successfully."',
    ].join('\n\n');
    triggerDownload('setup_telegram_movie_bot.sh', script);
  };

  const toggleStep = (id: string) => {
    setCompletedSteps((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const handleCycleCommonLanguage = () => {
    if (overrideLanguage === null) {
      const currentIdx = COMMON_TOGGLE_LANGUAGES.indexOf(
        effectiveLanguage as (typeof COMMON_TOGGLE_LANGUAGES)[number]
      );
      const nextLang =
        currentIdx === -1
          ? COMMON_TOGGLE_LANGUAGES[0]
          : COMMON_TOGGLE_LANGUAGES[(currentIdx + 1) % COMMON_TOGGLE_LANGUAGES.length];
      setOverrideLanguage(nextLang);
      return;
    }

    const idx = COMMON_TOGGLE_LANGUAGES.indexOf(
      overrideLanguage as (typeof COMMON_TOGGLE_LANGUAGES)[number]
    );
    if (idx === -1 || idx === COMMON_TOGGLE_LANGUAGES.length - 1) {
      setOverrideLanguage(null);
      return;
    }
    setOverrideLanguage(COMMON_TOGGLE_LANGUAGES[idx + 1]);
  };

  const fileLines = useMemo(() => selectedFile.content.split('\n'), [selectedFile]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col">
      {/* Three-zone Top Bar Contract */}
      <header className="bg-white border-b border-slate-200 px-6 h-16 flex items-center justify-between gap-8">
        <a
          href="#top"
          onClick={(e) => {
            e.preventDefault();
            setActiveTab('files');
          }}
          className="text-lg font-bold tracking-tight text-slate-900 whitespace-nowrap shrink-0"
        >
          Telegram Movie Catalog Bot
        </a>

        <nav className="flex items-center gap-6 text-sm font-medium">
          <button
            type="button"
            onClick={() => setActiveTab('files')}
            className={`py-1 border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'files'
                ? 'border-blue-600 text-slate-900'
                : 'border-transparent text-slate-500 hover:text-slate-900'
            }`}
          >
            Source Files
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('simulator')}
            className={`py-1 border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'simulator'
                ? 'border-blue-600 text-slate-900'
                : 'border-transparent text-slate-500 hover:text-slate-900'
            }`}
          >
            Parser Simulator
          </button>
          <button
            type="button"
            onClick={() => setActiveTab('deploy')}
            className={`py-1 border-b-2 transition-colors whitespace-nowrap shrink-0 ${
              activeTab === 'deploy'
                ? 'border-blue-600 text-slate-900'
                : 'border-transparent text-slate-500 hover:text-slate-900'
            }`}
          >
            Koyeb Guide
          </button>
        </nav>

        <div className="shrink-0">
          <button
            type="button"
            onClick={handleDownloadAllScript}
            className="inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800 transition-colors whitespace-nowrap shrink-0"
          >
            <Download className="w-3.5 h-3.5" />
            Export Setup Script
          </button>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-[1400px] w-full mx-auto px-6 py-6">
        {activeTab === 'files' && (
          <div className="grid grid-cols-1 lg:grid-cols-[272px_1fr] gap-6 items-start">
            {/* Sidebar File Tree */}
            <aside className="bg-white border border-slate-200 rounded-lg overflow-hidden">
              <div className="p-4 border-b border-slate-200">
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
                    <FolderTree className="w-3.5 h-3.5 text-slate-500" />
                    telegram-movie-bot
                  </span>
                  <span className="text-xs text-slate-500 tabular-nums">
                    {BOT_FILES.length} files
                  </span>
                </div>
                <div className="relative">
                  <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={fileFilter}
                    onChange={(e) => setFileFilter(e.target.value)}
                    placeholder="Filter files..."
                    aria-label="Filter repository files"
                    className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:border-blue-600"
                  />
                </div>
              </div>

              <div className="divide-y divide-slate-100 max-h-[640px] overflow-y-auto">
                {filteredFiles.map((file) => {
                  const isActive = file.path === selectedFile.path;
                  return (
                    <button
                      key={file.path}
                      type="button"
                      onClick={() => setSelectedPath(file.path)}
                      className={`w-full text-left px-4 py-2.5 transition-colors flex items-center justify-between gap-2 ${
                        isActive
                          ? 'bg-slate-900 text-white'
                          : 'hover:bg-slate-50 text-slate-700'
                      }`}
                    >
                      <span className="flex items-center gap-2 min-w-0">
                        <FileCode2
                          className={`w-3.5 h-3.5 shrink-0 ${
                            isActive ? 'text-blue-300' : 'text-slate-400'
                          }`}
                        />
                        <span className="text-xs font-mono truncate">{file.path}</span>
                      </span>
                      <span
                        className={`text-[11px] tabular-nums shrink-0 ${
                          isActive ? 'text-slate-300' : 'text-slate-400'
                        }`}
                      >
                        {file.content.split('\n').length}L
                      </span>
                    </button>
                  );
                })}
                {filteredFiles.length === 0 && (
                  <div className="px-4 py-6 text-xs text-slate-500 text-center">
                    No files match &ldquo;{fileFilter}&rdquo;
                  </div>
                )}
              </div>
            </aside>

            {/* Code Viewer */}
            <section className="bg-white border border-slate-200 rounded-lg overflow-hidden min-w-0">
              <div className="px-5 py-3.5 border-b border-slate-200 flex flex-wrap items-center justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="text-sm font-semibold font-mono text-slate-900 truncate">
                    {selectedFile.path}
                  </h1>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {selectedFile.summary}
                    <span className="mx-1.5" aria-hidden="true">·</span>
                    <span className="tabular-nums">{fileLines.length} lines</span>
                  </p>
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => copyText(selectedFile.path, selectedFile.content)}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-md hover:bg-slate-50 transition-colors whitespace-nowrap shrink-0"
                  >
                    {copiedId === selectedFile.path ? (
                      <>
                        <Check className="w-3.5 h-3.5 text-emerald-600" />
                        Copied
                      </>
                    ) : (
                      <>
                        <Copy className="w-3.5 h-3.5" />
                        Copy File
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerDownload(selectedFile.name, selectedFile.content)}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-white bg-blue-600 rounded-md hover:bg-blue-700 transition-colors whitespace-nowrap shrink-0"
                  >
                    <Download className="w-3.5 h-3.5" />
                    Download
                  </button>
                </div>
              </div>

              <div className="bg-slate-950 text-slate-100 overflow-x-auto max-h-[640px]">
                <table className="w-full border-collapse text-xs font-mono leading-relaxed">
                  <tbody>
                    {fileLines.map((line, idx) => (
                      <tr key={idx} className="hover:bg-slate-900/70">
                        <td className="select-none text-right pr-4 pl-4 py-0.5 text-slate-500 w-12 align-top tabular-nums">
                          {idx + 1}
                        </td>
                        <td className="pr-5 py-0.5 whitespace-pre text-slate-100">
                          {line || ' '}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}

        {activeTab === 'simulator' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
            {/* Left Input & Interactive Selection Controls */}
            <section className="bg-white border border-slate-200 rounded-lg p-6 space-y-5">
              <div>
                <h1 className="text-base font-semibold text-slate-900">
                  Metadata Extraction & Interactive Button Simulator
                </h1>
                <p className="text-xs text-slate-500 mt-1">
                  Test how bot/parser.py extracts title, year, language, and quality before pushing to Firebase.
                </p>
              </div>

              <div>
                <span className="block text-xs font-medium text-slate-700 mb-2">
                  Quick Sample Filenames
                </span>
                <div className="flex flex-wrap gap-2">
                  {PRESET_SAMPLES.map((sample) => (
                    <button
                      key={sample.label}
                      type="button"
                      onClick={() => {
                        setSimFilename(sample.filename);
                        setSimCaption(sample.caption);
                        setOverrideLanguage(null);
                        setOverrideQuality(null);
                      }}
                      className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 rounded-md hover:bg-slate-200 transition-colors whitespace-nowrap"
                    >
                      {sample.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-3">
                <label className="block">
                  <span className="block text-xs font-medium text-slate-700 mb-1">
                    Telegram File Name
                  </span>
                  <input
                    type="text"
                    value={simFilename}
                    onChange={(e) => {
                      setSimFilename(e.target.value);
                      setOverrideLanguage(null);
                      setOverrideQuality(null);
                    }}
                    className="w-full px-3 py-2 text-xs font-mono bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:border-blue-600"
                  />
                </label>

                <label className="block">
                  <span className="block text-xs font-medium text-slate-700 mb-1">
                    Optional Message Caption or Direct URL
                  </span>
                  <input
                    type="text"
                    value={simCaption}
                    onChange={(e) => {
                      setSimCaption(e.target.value);
                      setOverrideLanguage(null);
                      setOverrideQuality(null);
                    }}
                    placeholder="e.g. Hindi 1080p WEB-DL"
                    className="w-full px-3 py-2 text-xs font-mono bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:border-blue-600"
                  />
                </label>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="block">
                    <span className="block text-xs font-medium text-slate-700 mb-1">
                      Koyeb Base URL
                    </span>
                    <input
                      type="text"
                      value={simBaseUrl}
                      onChange={(e) => setSimBaseUrl(e.target.value)}
                      className="w-full px-3 py-2 text-xs font-mono bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:border-blue-600"
                    />
                  </label>
                  <label className="block">
                    <span className="block text-xs font-medium text-slate-700 mb-1">
                      Storage Message ID
                    </span>
                    <input
                      type="number"
                      min={1}
                      value={simMessageId}
                      onChange={(e) => setSimMessageId(Math.max(1, Number(e.target.value) || 1))}
                      className="w-full px-3 py-2 text-xs font-mono tabular-nums bg-slate-50 border border-slate-200 rounded-md focus:outline-none focus:border-blue-600"
                    />
                  </label>
                </div>
              </div>

              <div className="pt-3 border-t border-slate-200 space-y-4">
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-slate-700">
                      Interactive Language Selection
                    </span>
                    <span className="text-xs text-slate-500">
                      {parsedPreview.language
                        ? `Auto-detected: ${parsedPreview.language}`
                        : 'Auto-inferred from TMDB original_language'}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {SUPPORTED_LANGUAGES.map((lang) => {
                      const active = effectiveLanguage === lang;
                      return (
                        <button
                          key={lang}
                          type="button"
                          onClick={() =>
                            setOverrideLanguage((prev) => (prev === lang ? null : lang))
                          }
                          className={`px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                            active
                              ? 'bg-slate-900 text-white border-slate-900'
                              : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
                          }`}
                        >
                          {lang}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-slate-700">
                      Interactive Quality Selection
                    </span>
                    <span className="text-xs text-slate-500">
                      {parsedPreview.quality
                        ? `Auto-detected: ${parsedPreview.quality}`
                        : 'Auto-inferred from Telegram video resolution'}
                    </span>
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    {SUPPORTED_QUALITIES.map((qual) => {
                      const active = effectiveQuality === qual;
                      return (
                        <button
                          key={qual}
                          type="button"
                          onClick={() => setOverrideQuality(qual)}
                          className={`px-3 py-2 text-xs font-medium rounded-md border transition-colors ${
                            active
                              ? 'bg-blue-600 text-white border-blue-600'
                              : 'bg-white text-slate-700 border-slate-200 hover:bg-slate-50'
                          }`}
                        >
                          {qual}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            </section>

            {/* Right Extracted Summary & Firebase Record Output */}
            <section className="bg-white border border-slate-200 rounded-lg overflow-hidden">
              <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between gap-4">
                <div>
                  <h2 className="text-sm font-semibold text-slate-900">
                    Firebase /movies Record Preview
                  </h2>
                  <p className="text-xs text-slate-500 mt-0.5">
                    {parsedPreview.needsSelection
                      ? 'Auto-pushes immediately with TMDB/video fallback + inline edit buttons'
                      : 'All tags detected — bot auto-pushes immediately to /movies.json'}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    copyText('firebase-json', JSON.stringify(firebaseRecord, null, 2))
                  }
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-slate-700 bg-white border border-slate-200 rounded-md hover:bg-slate-50 transition-colors whitespace-nowrap shrink-0"
                >
                  {copiedId === 'firebase-json' ? (
                    <>
                      <Check className="w-3.5 h-3.5 text-emerald-600" />
                      Copied JSON
                    </>
                  ) : (
                    <>
                      <Copy className="w-3.5 h-3.5" />
                      Copy JSON
                    </>
                  )}
                </button>
              </div>

              <div className="px-6 py-4 border-b border-slate-200 flex flex-wrap items-center justify-between gap-3 bg-slate-50/60">
                <div className="flex flex-wrap items-center gap-2.5 min-w-0">
                  <div>
                    <span className="block text-[11px] text-slate-500">Clean Title</span>
                    <span className="text-sm font-semibold text-slate-900">
                      {parsedPreview.title}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={handleCycleCommonLanguage}
                    title="Click to cycle common languages or revert to auto-detect"
                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-md border transition-colors whitespace-nowrap ${
                      overrideLanguage === null
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                        : 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100'
                    }`}
                  >
                    <Languages className="w-3.5 h-3.5 shrink-0" />
                    <span>Auto-Detect Language:</span>
                    <span className="font-semibold">{effectiveLanguage}</span>
                    <span className="text-[11px] opacity-75">
                      ({overrideLanguage === null ? 'Auto' : 'Manual'})
                    </span>
                  </button>
                </div>

                <div
                  className="flex flex-wrap items-center gap-1.5"
                  role="group"
                  aria-label="One-click common language toggles"
                >
                  {COMMON_TOGGLE_LANGUAGES.map((lang) => {
                    const active = effectiveLanguage === lang;
                    return (
                      <button
                        key={lang}
                        type="button"
                        onClick={() =>
                          setOverrideLanguage((prev) => (prev === lang ? null : lang))
                        }
                        aria-pressed={active}
                        className={`px-2.5 py-1 text-[11px] font-medium rounded border transition-colors ${
                          active
                            ? 'bg-slate-900 text-white border-slate-900'
                            : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-100 hover:text-slate-900'
                        }`}
                      >
                        {lang}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="px-6 py-4 border-b border-slate-200 grid grid-cols-2 sm:grid-cols-4 gap-4 text-xs">
                <div>
                  <span className="block text-slate-500">Year</span>
                  <span className="font-semibold text-slate-900 tabular-nums">
                    {parsedPreview.year ?? 'TMDB Lookup'}
                  </span>
                </div>
                <div>
                  <span className="block text-slate-500">Media Mode</span>
                  <span className="font-semibold text-slate-900">
                    {parsedPreview.isSeries ? 'TV / Series' : 'Movie'}
                  </span>
                </div>
                <div>
                  <span className="block text-slate-500">Language</span>
                  <span className="font-semibold text-slate-900">{effectiveLanguage}</span>
                </div>
                <div>
                  <span className="block text-slate-500">Quality Badge</span>
                  <span className="font-semibold text-slate-900">
                    {firebaseRecord.qualityBadge}
                  </span>
                </div>
              </div>

              <pre className="p-6 bg-slate-950 text-slate-100 text-xs font-mono leading-relaxed overflow-x-auto">
                {JSON.stringify(firebaseRecord, null, 2)}
              </pre>
            </section>
          </div>
        )}

        {activeTab === 'deploy' && (
          <div className="space-y-6">
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
              {/* Interactive Deployment Checklist */}
              <section className="bg-white border border-slate-200 rounded-lg p-6">
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <h1 className="text-base font-semibold text-slate-900">
                      GitHub & Koyeb Deployment Checklist
                    </h1>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Track setup steps from repository push to live Telegram verification.
                    </p>
                  </div>
                  <span className="text-xs font-medium text-slate-600 tabular-nums">
                    {Object.values(completedSteps).filter(Boolean).length} / {DEPLOY_STEPS.length} done
                  </span>
                </div>

                <div className="divide-y divide-slate-100">
                  {DEPLOY_STEPS.map((step) => {
                    const checked = Boolean(completedSteps[step.id]);
                    return (
                      <label
                        key={step.id}
                        className="flex items-start gap-3 py-3.5 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleStep(step.id)}
                          className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-600"
                        />
                        <div className="min-w-0">
                          <div
                            className={`text-sm font-medium ${
                              checked ? 'line-through text-slate-400' : 'text-slate-900'
                            }`}
                          >
                            {step.title}
                          </div>
                          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                            {step.detail}
                          </p>
                        </div>
                      </label>
                    );
                  })}
                </div>
              </section>

              {/* Environment Variables Table */}
              <section className="bg-white border border-slate-200 rounded-lg overflow-hidden">
                <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">
                      Koyeb Environment Variables
                    </h2>
                    <p className="text-xs text-slate-500 mt-0.5">
                      All configuration is read strictly from environment variables.
                    </p>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 bg-slate-50 text-slate-600">
                        <th className="py-2.5 px-4 font-semibold">Variable</th>
                        <th className="py-2.5 px-4 font-semibold">Requirement</th>
                        <th className="py-2.5 px-4 font-semibold">Purpose</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {ENV_VARS_SPEC.map((item) => (
                        <tr key={item.key} className="hover:bg-slate-50">
                          <td className="py-2.5 px-4 font-mono font-medium text-slate-900 whitespace-nowrap">
                            {item.key}
                          </td>
                          <td className="py-2.5 px-4 text-slate-600 whitespace-nowrap">
                            {item.required ? 'Required' : 'Optional'}
                          </td>
                          <td className="py-2.5 px-4 text-slate-600">{item.note}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
