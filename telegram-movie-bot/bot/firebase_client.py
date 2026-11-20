"""Async Firebase Realtime Database REST client for movies and multi-admin sync."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp

logger = logging.getLogger(__name__)


class FirebaseError(RuntimeError):
    """Raised when Firebase Realtime Database returns an error."""


class FirebaseClient:
    """Async REST client for Firebase Realtime Database (`/movies` and `/admins`)."""

    def __init__(
        self,
        db_url: str,
        database_secret: str,
        owner_id: int,
    ) -> None:
        self._db_url = db_url.rstrip("/")
        self._secret = database_secret.strip()
        self._owner_id = owner_id
        self._session: Optional[aiohttp.ClientSession] = None
        self._admin_cache: dict[int, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _endpoint(self, path: str) -> str:
        clean = path.lstrip("/")
        if not clean.endswith(".json"):
            clean = f"{clean}.json"
        return f"{self._db_url}/{clean}"

    def _params(self, extra: Optional[dict[str, str]] = None) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._secret:
            params["auth"] = self._secret
        if extra:
            params.update(extra)
        return params

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Optional[Any] = None,
        extra_params: Optional[dict[str, str]] = None,
    ) -> Any:
        session = await self._get_session()
        url = self._endpoint(path)
        params = self._params(extra_params)

        try:
            async with session.request(method, url, params=params, json=json_data) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise FirebaseError(
                        f"Firebase {method} {path} failed ({resp.status}): {body[:300]}"
                    )
                if resp.status == 204:
                    return None
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise FirebaseError(f"Firebase network error on {method} {path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Multi-Admin Management (/admins)
    # ------------------------------------------------------------------

    async def sync_admins(self) -> dict[int, dict[str, Any]]:
        """Load `/admins` from Firebase into memory cache."""
        data = await self._request("GET", "admins")
        synced: dict[int, dict[str, Any]] = {}
        if isinstance(data, dict):
            for key, value in data.items():
                try:
                    uid = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(value, dict):
                    synced[uid] = value
                elif value is True:
                    synced[uid] = {"userId": uid}

        async with self._lock:
            self._admin_cache = synced
        logger.info("Synced %d admins from Firebase (+ owner %s)", len(synced), self._owner_id)
        return dict(synced)

    def is_owner(self, user_id: Optional[int]) -> bool:
        return user_id is not None and user_id == self._owner_id

    def is_admin(self, user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        if user_id == self._owner_id:
            return True
        return user_id in self._admin_cache

    def get_cached_admins(self) -> dict[int, dict[str, Any]]:
        return dict(self._admin_cache)

    def total_admins_count(self) -> int:
        # Owner always counts even if not explicitly stored under /admins
        ids = set(self._admin_cache.keys()) | {self._owner_id}
        return len(ids)

    async def add_admin(
        self,
        user_id: int,
        added_by: int,
        username: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add an admin under `/admins/{user_id}` and sync in-memory cache."""
        record = {
            "userId": user_id,
            "username": username or "",
            "addedBy": added_by,
            "addedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        await self._request("PUT", f"admins/{user_id}", json_data=record)
        async with self._lock:
            self._admin_cache[user_id] = record
        return record

    async def remove_admin(self, user_id: int) -> bool:
        """Remove an admin from `/admins/{user_id}`. Prevents removing OWNER_ID."""
        if user_id == self._owner_id:
            raise ValueError("Cannot remove OWNER_ID from admins.")

        existed = user_id in self._admin_cache
        await self._request("DELETE", f"admins/{user_id}")
        async with self._lock:
            self._admin_cache.pop(user_id, None)
        return existed

    # ------------------------------------------------------------------
    # Movie Catalog (/movies)
    # ------------------------------------------------------------------

    async def push_movie(self, payload: dict[str, Any]) -> str:
        """Push a new movie record under `/movies` and return the generated Firebase key."""
        result = await self._request("POST", "movies", json_data=payload)
        if not isinstance(result, dict) or not result.get("name"):
            raise FirebaseError(f"Unexpected Firebase POST response: {result!r}")
        return str(result["name"])

    async def update_movie(self, firebase_key: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Patch fields on an existing `/movies/{firebase_key}` record."""
        result = await self._request("PATCH", f"movies/{firebase_key}", json_data=patch)
        return result if isinstance(result, dict) else patch

    async def delete_movie(self, firebase_key: str) -> None:
        """Delete a movie record from `/movies/{firebase_key}`."""
        await self._request("DELETE", f"movies/{firebase_key}")

    async def get_movie(self, firebase_key: str) -> Optional[dict[str, Any]]:
        """Fetch a single movie record by Firebase key."""
        result = await self._request("GET", f"movies/{firebase_key}")
        return result if isinstance(result, dict) else None

    async def count_movies(self) -> int:
        """Count total records under `/movies` using shallow query."""
        result = await self._request("GET", "movies", extra_params={"shallow": "true"})
        if isinstance(result, dict):
            return len(result)
        return 0

    async def health_check(self) -> tuple[bool, int, str, int]:
        """Return `(ok, latency_ms, status_message, movie_count)`."""
        start = time.monotonic()
        try:
            count = await self.count_movies()
            latency_ms = int((time.monotonic() - start) * 1000)
            return True, latency_ms, "Connected", count
        except FirebaseError as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return False, latency_ms, str(exc), 0
