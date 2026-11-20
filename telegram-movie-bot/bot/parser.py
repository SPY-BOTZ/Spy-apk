"""Filename, caption, and link metadata parser for movie/file cataloging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

SUPPORTED_LANGUAGES = ("Hindi", "English", "Tamil", "Telugu", "Bangla", "Multi")
SUPPORTED_QUALITIES = ("480p", "720p", "1080p", "4K")

_LANGUAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:multi[\s._-]*audio|multi[\s._-]*lang|multi)\b", re.I), "Multi"),
    (re.compile(r"\b(?:dual[\s._-]*audio|hindi[\s._+-]+eng(?:lish)?|hin[\s._+-]+eng)\b", re.I), "Multi"),
    (re.compile(r"\b(?:hindi|hin)\b", re.I), "Hindi"),
    (re.compile(r"\b(?:english|eng)\b", re.I), "English"),
    (re.compile(r"\b(?:tamil|tam)\b", re.I), "Tamil"),
    (re.compile(r"\b(?:telugu|tel)\b", re.I), "Telugu"),
    (re.compile(r"\b(?:bangla|bengali|ben)\b", re.I), "Bangla"),
    (re.compile(r"\b(?:malayalam|mal)\b", re.I), "Malayalam"),
    (re.compile(r"\b(?:kannada|kan)\b", re.I), "Kannada"),
]

_QUALITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:2160p|4k|uhd)\b", re.I), "4K"),
    (re.compile(r"\b1080p?\b", re.I), "1080p"),
    (re.compile(r"\b720p?\b", re.I), "720p"),
    (re.compile(r"\b480p?\b", re.I), "480p"),
]

_VIDEO_EXTENSIONS_RE = re.compile(
    r"\.(?:mkv|mp4|avi|mov|webm|m4v|flv|wmv|ts|m2ts|iso|zip|rar|7z)$",
    re.I,
)

_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_SERIES_RE = re.compile(r"\bS\d{1,2}(?:E\d{1,2})?\b|\bSeason[\s._-]*\d+\b", re.I)

# Tokens that signal release metadata rather than part of the movie title
_NOISE_CUT_RE = re.compile(
    r"\b(?:"
    r"2160p|1080p|720p|480p|360p|4k|uhd|hd|fhd|sd|"
    r"web[\s._-]*dl|webrip|bluray|blu[\s._-]*ray|brrip|bdrip|hdrip|dvdrip|"
    r"hdcam|camrip|predvd|telesync|hdts|ts|amzn|nf|netflix|dsnp|hotstar|"
    r"x264|x265|h[\s._-]*264|h[\s._-]*265|hevc|avc|10bit|8bit|"
    r"aac|ac3|eac3|ddp?5[\s._-]*1|atmos|dts|"
    r"esub|esubs|msub|hc|uncut|unrated|extended|remastered|proper|repack|"
    r"dual[\s._-]*audio|multi[\s._-]*audio|multi|hindi|english|tamil|telugu|bangla|bengali|"
    r"malayalam|kannada|hin|eng|tam|tel|ben|"
    r"yify|yts|rarbg|psa|ettv|eztv|vegamovies|moviesmod|katmoviehd|filmyzilla|"
    r"s\d{1,2}e\d{1,2}|s\d{1,2}|season[\s._-]*\d+|ep[\s._-]*\d+"
    r")\b",
    re.I,
)

_SIZE_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:gb|mb|kb)\b", re.I)
_BRACKET_RE = re.compile(r"[\[\(\{].*?[\]\)\}]")
_HANDLE_RE = re.compile(r"@\w+")
_URL_RE = re.compile(r"https?://\S+", re.I)


@dataclass(slots=True)
class ParsedMedia:
    raw_name: str
    title: str
    year: Optional[str]
    language: Optional[str]
    quality: Optional[str]
    is_series: bool

    @property
    def quality_badge(self) -> str:
        mapping = {
            "480p": "SD",
            "720p": "HD",
            "1080p": "FHD",
            "4K": "4K",
        }
        return mapping.get(self.quality or "", "HD")

    @property
    def needs_selection(self) -> bool:
        return self.language is None or self.quality is None


def extract_telegram_link(text: str) -> Optional[tuple[int | str, int]]:
    """Parse https://t.me/c/<internal_id>/<msg_id> or https://t.me/<username>/<msg_id>."""
    if not text:
        return None

    private_match = re.search(r"https?://t\.me/c/(\d+)/(\d+)", text)
    if private_match:
        chat_id = int(f"-100{private_match.group(1)}")
        msg_id = int(private_match.group(2))
        return chat_id, msg_id

    public_match = re.search(r"https?://t\.me/([A-Za-z0-9_]{4,})/(\d+)", text)
    if public_match:
        username = public_match.group(1)
        if username.lower() not in {"c", "s", "joinchat", "addstickers"}:
            return username, int(public_match.group(2))

    return None


def extract_direct_url(text: str) -> Optional[str]:
    """Return first non-t.me HTTP/HTTPS URL if present in text."""
    if not text:
        return None
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,);]")
        parsed = urlparse(url)
        if parsed.netloc and "t.me" not in parsed.netloc.lower():
            return url
    return None


def _detect_language(text: str) -> Optional[str]:
    found: list[str] = []
    for pattern, label in _LANGUAGE_PATTERNS:
        if pattern.search(text):
            if label == "Multi":
                return "Multi"
            if label not in found:
                found.append(label)
    if len(found) > 1:
        return "Multi"
    return found[0] if found else None


def _detect_quality(text: str) -> Optional[str]:
    for pattern, label in _QUALITY_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _clean_title(source: str, year: Optional[str]) -> str:
    working = _URL_RE.sub(" ", source)
    working = _HANDLE_RE.sub(" ", working)
    working = _VIDEO_EXTENSIONS_RE.sub("", working.strip())
    working = _SIZE_RE.sub(" ", working)

    # Preserve year position before stripping brackets so "(2023)" still acts as cut anchor
    working = working.replace(".", " ").replace("_", " ")

    cut_index = len(working)
    if year:
        year_match = re.search(rf"\b{re.escape(year)}\b", working)
        if year_match and year_match.start() > 0:
            cut_index = min(cut_index, year_match.start())

    noise_match = _NOISE_CUT_RE.search(working)
    if noise_match and noise_match.start() > 0:
        cut_index = min(cut_index, noise_match.start())

    working = working[:cut_index]
    working = _BRACKET_RE.sub(" ", working)
    working = _NOISE_CUT_RE.sub(" ", working)
    working = re.sub(r"[\[\]\(\)\{\}|/\\]+", " ", working)
    working = re.sub(r"[-–—:]+$", "", working.strip())
    working = re.sub(r"\s{2,}", " ", working).strip(" -_.")

    if not working:
        # Fallback: clean original string minimally
        fallback = _VIDEO_EXTENSIONS_RE.sub("", source)
        fallback = re.sub(r"[._]+", " ", fallback)
        fallback = re.sub(r"\s{2,}", " ", fallback).strip()
        return fallback or "Untitled Movie"

    return working


def parse_media_metadata(
    filename: Optional[str] = None,
    caption: Optional[str] = None,
) -> ParsedMedia:
    """Extract clean movie/series title, year, language, and quality from filename and/or caption."""
    primary_source = (filename or "").strip()
    secondary_source = (caption or "").strip()

    if not primary_source and secondary_source:
        direct_url = extract_direct_url(secondary_source)
        if direct_url:
            path_tail = unquote(urlparse(direct_url).path.rsplit("/", 1)[-1])
            primary_source = path_tail or secondary_source.splitlines()[0]
        else:
            primary_source = secondary_source.splitlines()[0]

    cleaned_secondary = _URL_RE.sub(" ", secondary_source).strip()
    combined = f"{primary_source} {cleaned_secondary}".strip()
    is_series = bool(_SERIES_RE.search(combined))

    language = _detect_language(combined)
    quality = _detect_quality(combined)

    # Find the most plausible release year (prefer year after title words, avoid 1080/2160)
    year_matches = _YEAR_RE.findall(combined)
    year: Optional[str] = year_matches[-1] if year_matches else None

    title = _clean_title(primary_source or secondary_source, year)

    return ParsedMedia(
        raw_name=primary_source or secondary_source or "Unknown",
        title=title,
        year=year,
        language=language,
        quality=quality,
        is_series=is_series,
    )
