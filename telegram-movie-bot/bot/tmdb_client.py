"""Async TMDB API v3 metadata client with movie/TV fallback and health check."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

TMDB_GENRE_MAP: dict[int, str] = {
    28: "Action",
    12: "Adventure",
    16: "Animation",
    35: "Comedy",
    80: "Crime",
    99: "Documentary",
    18: "Drama",
    10751: "Family",
    14: "Fantasy",
    36: "History",
    27: "Horror",
    10402: "Music",
    9648: "Mystery",
    10749: "Romance",
    878: "Sci-Fi",
    10770: "TV Movie",
    53: "Thriller",
    10752: "War",
    37: "Western",
    10759: "Action & Adventure",
    10762: "Kids",
    10763: "News",
    10764: "Reality",
    10765: "Sci-Fi & Fantasy",
    10766: "Soap",
    10767: "Talk",
    10768: "War & Politics",
}


TMDB_LANGUAGE_MAP: dict[str, str] = {
    "hi": "Hindi",
    "en": "English",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bangla",
    "ml": "Malayalam",
    "kn": "Kannada",
    "pa": "Punjabi",
    "mr": "Marathi",
    "ko": "Korean",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
    "zh": "Chinese",
}


@dataclass(slots=True)
class TmdbMetadata:
    title: str
    year: str
    poster_url: str
    rating: float
    description: str
    category: str
    original_language: Optional[str]
    tmdb_id: Optional[int]
    media_type: str
    matched: bool


class TmdbClient:
    """Async client for The Movie Database (TMDB) v3 API."""

    def __init__(self, api_key: str, fallback_poster_url: str) -> None:
        self._api_key = api_key
        self._fallback_poster_url = fallback_poster_url
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=12)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _search(
        self,
        endpoint: str,
        query: str,
        year: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        session = await self._get_session()
        params: dict[str, str] = {
            "api_key": self._api_key,
            "query": query,
            "include_adult": "false",
            "language": "en-US",
            "page": "1",
        }
        if year:
            if endpoint == "movie":
                params["year"] = year
            elif endpoint == "tv":
                params["first_air_date_year"] = year

        url = f"{TMDB_API_BASE}/search/{endpoint}"
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(
                        "TMDB search/%s returned HTTP %s: %s",
                        endpoint,
                        resp.status,
                        body[:200],
                    )
                    return []
                payload = await resp.json()
                results = payload.get("results")
                return results if isinstance(results, list) else []
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.error("TMDB request failed for %r: %s", query, exc)
            return []

    def _map_result(
        self,
        item: dict[str, Any],
        media_type: str,
        fallback_title: str,
        fallback_year: Optional[str],
    ) -> TmdbMetadata:
        raw_title = (
            item.get("title")
            or item.get("name")
            or item.get("original_title")
            or item.get("original_name")
            or fallback_title
        )
        release_date = item.get("release_date") or item.get("first_air_date") or ""
        year = release_date[:4] if len(release_date) >= 4 else (fallback_year or "")

        poster_path = item.get("poster_path") or item.get("backdrop_path")
        poster_url = (
            f"{TMDB_IMAGE_BASE}{poster_path}"
            if poster_path
            else self._fallback_poster_url
        )

        vote_avg = item.get("vote_average")
        try:
            rating = round(float(vote_avg or 0.0), 1)
        except (TypeError, ValueError):
            rating = 0.0

        description = (item.get("overview") or "").strip() or "No description available."

        orig_lang_code = str(item.get("original_language") or "").strip().lower()
        original_language = TMDB_LANGUAGE_MAP.get(orig_lang_code)

        genre_ids = item.get("genre_ids") or []
        genres = [TMDB_GENRE_MAP[gid] for gid in genre_ids if gid in TMDB_GENRE_MAP]
        if genres:
            category = genres[0]
        elif media_type == "tv":
            category = "Web Series"
        else:
            category = "Movie"

        return TmdbMetadata(
            title=str(raw_title).strip(),
            year=str(year).strip(),
            poster_url=poster_url,
            rating=rating,
            description=description,
            category=category,
            original_language=original_language,
            tmdb_id=item.get("id"),
            media_type=media_type,
            matched=True,
        )

    async def lookup(
        self,
        title: str,
        year: Optional[str] = None,
        prefer_tv: bool = False,
    ) -> TmdbMetadata:
        """Search TMDB movie endpoint (with TV fallback), guaranteeing a safe metadata object."""
        clean_query = (title or "").strip()
        if not clean_query:
            return TmdbMetadata(
                title="Untitled Movie",
                year=year or "",
                poster_url=self._fallback_poster_url,
                rating=0.0,
                description="No description available.",
                category="Web Series" if prefer_tv else "Movie",
                original_language=None,
                tmdb_id=None,
                media_type="tv" if prefer_tv else "movie",
                matched=False,
            )

        order = ("tv", "movie") if prefer_tv else ("movie", "tv")
        queries = [clean_query]
        words = clean_query.split()
        if len(words) > 3:
            queries.append(" ".join(words[:3]))

        for query_variant in queries:
            for media_type in order:
                if year:
                    results = await self._search(media_type, query_variant, year=year)
                    if results:
                        return self._map_result(results[0], media_type, clean_query, year)

                results = await self._search(media_type, query_variant, year=None)
                if results:
                    return self._map_result(results[0], media_type, clean_query, year)

        return TmdbMetadata(
            title=clean_query,
            year=year or "",
            poster_url=self._fallback_poster_url,
            rating=0.0,
            description="No description available.",
            category="Web Series" if prefer_tv else "Movie",
            original_language=None,
            tmdb_id=None,
            media_type="tv" if prefer_tv else "movie",
            matched=False,
        )

    async def health_check(self) -> tuple[bool, int, str]:
        """Verify TMDB API connectivity and key validity."""
        session = await self._get_session()
        start = time.monotonic()
        url = f"{TMDB_API_BASE}/configuration"
        try:
            async with session.get(url, params={"api_key": self._api_key}) as resp:
                latency_ms = int((time.monotonic() - start) * 1000)
                if resp.status == 200:
                    return True, latency_ms, "Connected"
                return False, latency_ms, f"HTTP {resp.status}"
        except (aiohttp.ClientError, TimeoutError) as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return False, latency_ms, str(exc)
