export const SUPPORTED_LANGUAGES = ['Hindi', 'English', 'Tamil', 'Telugu', 'Bangla', 'Multi'] as const;
export const SUPPORTED_QUALITIES = ['480p', '720p', '1080p', '4K'] as const;

export interface ParsedPreview {
  title: string;
  year: string | null;
  language: string | null;
  quality: string | null;
  qualityBadge: string;
  isSeries: boolean;
  needsSelection: boolean;
}

const LANGUAGE_PATTERNS: Array<[RegExp, string]> = [
  [/\b(?:multi[\s._-]*audio|multi[\s._-]*lang|multi)\b/i, 'Multi'],
  [/\b(?:dual[\s._-]*audio|hindi[\s._+-]+eng(?:lish)?|hin[\s._+-]+eng)\b/i, 'Multi'],
  [/\b(?:hindi|hin)\b/i, 'Hindi'],
  [/\b(?:english|eng)\b/i, 'English'],
  [/\b(?:tamil|tam)\b/i, 'Tamil'],
  [/\b(?:telugu|tel)\b/i, 'Telugu'],
  [/\b(?:bangla|bengali|ben)\b/i, 'Bangla'],
  [/\b(?:malayalam|mal)\b/i, 'Malayalam'],
  [/\b(?:kannada|kan)\b/i, 'Kannada'],
];

const QUALITY_PATTERNS: Array<[RegExp, string]> = [
  [/\b(?:2160p|4k|uhd)\b/i, '4K'],
  [/\b1080p?\b/i, '1080p'],
  [/\b720p?\b/i, '720p'],
  [/\b480p?\b/i, '480p'],
];

const VIDEO_EXT_RE = /\.(?:mkv|mp4|avi|mov|webm|m4v|flv|wmv|ts|m2ts|iso|zip|rar|7z)$/i;
const YEAR_RE = /(?<!\d)(19\d{2}|20\d{2})(?!\d)/g;
const SERIES_RE = /\bS\d{1,2}(?:E\d{1,2})?\b|\bSeason[\s._-]*\d+\b/i;
const URL_RE = /https?:\/\/\S+/gi;
const HANDLE_RE = /@\w+/g;
const SIZE_RE = /\b\d+(?:\.\d+)?\s*(?:gb|mb|kb)\b/gi;
const BRACKET_RE = /[[({].*?[\])}]/g;
const NOISE_CUT_RE =
  /\b(?:2160p|1080p|720p|480p|360p|4k|uhd|hd|fhd|sd|web[\s._-]*dl|webrip|bluray|blu[\s._-]*ray|brrip|bdrip|hdrip|dvdrip|hdcam|camrip|predvd|telesync|hdts|ts|amzn|nf|netflix|dsnp|hotstar|x264|x265|h[\s._-]*264|h[\s._-]*265|hevc|avc|10bit|8bit|aac|ac3|eac3|ddp?5[\s._-]*1|atmos|dts|esub|esubs|msub|hc|uncut|unrated|extended|remastered|proper|repack|dual[\s._-]*audio|multi[\s._-]*audio|multi|hindi|english|tamil|telugu|bangla|bengali|malayalam|kannada|hin|eng|tam|tel|ben|yify|yts|rarbg|psa|ettv|eztv|vegamovies|moviesmod|katmoviehd|filmyzilla|s\d{1,2}e\d{1,2}|s\d{1,2}|season[\s._-]*\d+|ep[\s._-]*\d+)\b/i;

export function qualityBadgeFor(quality: string | null): string {
  switch (quality) {
    case '480p':
      return 'SD';
    case '720p':
      return 'HD';
    case '1080p':
      return 'FHD';
    case '4K':
      return '4K';
    default:
      return 'HD';
  }
}

export function parseMediaPreview(filename: string, caption: string): ParsedPreview {
  let primary = filename.trim();
  const secondary = caption.trim();

  if (!primary && secondary) {
    primary = secondary.split('\n')[0] || '';
  }

  const cleanedSecondary = secondary.replace(URL_RE, ' ').trim();
  const combined = `${primary} ${cleanedSecondary}`.trim();
  const isSeries = SERIES_RE.test(combined);

  const detectedLangs: string[] = [];
  let language: string | null = null;
  for (const [pattern, label] of LANGUAGE_PATTERNS) {
    if (pattern.test(combined)) {
      if (label === 'Multi') {
        language = 'Multi';
        break;
      }
      if (!detectedLangs.includes(label)) {
        detectedLangs.push(label);
      }
    }
  }
  if (!language) {
    if (detectedLangs.length > 1) {
      language = 'Multi';
    } else if (detectedLangs.length === 1) {
      language = detectedLangs[0];
    }
  }

  let quality: string | null = null;
  for (const [pattern, label] of QUALITY_PATTERNS) {
    if (pattern.test(combined)) {
      quality = label;
      break;
    }
  }

  const yearMatches = combined.match(YEAR_RE);
  const year = yearMatches && yearMatches.length > 0 ? yearMatches[yearMatches.length - 1] : null;

  let working = (primary || secondary)
    .replace(URL_RE, ' ')
    .replace(HANDLE_RE, ' ')
    .trim()
    .replace(VIDEO_EXT_RE, '')
    .replace(SIZE_RE, ' ')
    .replace(/[._]/g, ' ');

  let cutIndex = working.length;
  if (year) {
    const yearIdx = working.search(new RegExp(`\\b${year}\\b`));
    if (yearIdx > 0) {
      cutIndex = Math.min(cutIndex, yearIdx);
    }
  }
  const noiseMatch = NOISE_CUT_RE.exec(working);
  if (noiseMatch && noiseMatch.index > 0) {
    cutIndex = Math.min(cutIndex, noiseMatch.index);
  }

  working = working
    .slice(0, cutIndex)
    .replace(BRACKET_RE, ' ')
    .replace(new RegExp(NOISE_CUT_RE.source, 'gi'), ' ')
    .replace(/[[\](){}|/\\]+/g, ' ')
    .replace(/[-–—:]+$/, '')
    .replace(/\s{2,}/g, ' ')
    .replace(/^[\s\-_.]+|[\s\-_.]+$/g, '');

  const title = working || 'Untitled Movie';

  return {
    title,
    year,
    language,
    quality,
    qualityBadge: qualityBadgeFor(quality),
    isSeries,
    needsSelection: language === null || quality === null,
  };
}

export function buildFirebasePayloadPreview(params: {
  title: string;
  year: string;
  language: string;
  quality: string;
  isSeries: boolean;
  baseUrl: string;
  messageId: number;
}): Record<string, string | number> {
  const cleanBase = params.baseUrl.replace(/\/+$/, '') || 'https://your-app.koyeb.app';
  const watchUrl = `${cleanBase}/watch/${params.messageId}`;
  const downloadUrl = `${cleanBase}/download/${params.messageId}`;

  return {
    title: params.title,
    year: params.year || '2024',
    posterUrl: 'https://image.tmdb.org/t/p/w500/example_poster.jpg',
    category: params.isSeries ? 'Web Series' : 'Sci-Fi',
    rating: 8.4,
    qualityBadge: qualityBadgeFor(params.quality),
    language: params.language,
    quality: params.quality,
    cast: 'Actor One, Actor Two, Actor Three',
    description: 'Overview retrieved automatically from TMDB v3 API.',
    watch480: params.quality === '480p' ? watchUrl : '',
    watch780: params.quality === '720p' ? watchUrl : '',
    watch1080: params.quality === '1080p' || params.quality === '4K' ? watchUrl : '',
    watchLink: watchUrl,
    download480: params.quality === '480p' ? downloadUrl : '',
    download780: params.quality === '720p' ? downloadUrl : '',
    download1080: params.quality === '1080p' || params.quality === '4K' ? downloadUrl : '',
    downloadLink: downloadUrl,
    fileId: 'BAACAgUAAxkBAAIB...',
    messageId: params.messageId,
    createdAt: Date.now(),
  };
}
