"""High-speed parallel Telegram MTProto ByteStreamer with persistent DC sessions."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncIterator, Optional

from pyrogram import raw
from pyrogram.errors import AuthBytesInvalid, FileMigrate, FileReferenceExpired, FloodWait
from pyrogram.file_id import PHOTO_TYPES, FileId
from pyrogram.session import Auth, Session

if TYPE_CHECKING:
    from pyrogram import Client

logger = logging.getLogger(__name__)

# Telegram requires limit to divide 1 MiB cleanly and offset % limit == 0.
# 512 KiB chunks with 6 concurrent in-flight requests gives fast first-byte
# latency for video seeking and high sustained throughput for downloads.
STREAM_CHUNK_SIZE = 512 * 1024  # 512 KiB
PARALLEL_CHUNK_WORKERS = 6


class FastByteStreamer:
    """Streams Telegram media using parallel `upload.GetFile` calls over pooled DC sessions."""

    def __init__(self, client: "Client", storage_channel_id: int) -> None:
        self._client = client
        self._storage_channel_id = storage_channel_id
        self._dc_sessions: dict[int, Session] = {}
        self._dc_locks: dict[int, asyncio.Lock] = {}

    def _get_dc_lock(self, dc_id: int) -> asyncio.Lock:
        lock = self._dc_locks.get(dc_id)
        if lock is None:
            lock = asyncio.Lock()
            self._dc_locks[dc_id] = lock
        return lock

    async def _get_media_session(self, dc_id: int, force_recreate: bool = False) -> Session:
        lock = self._get_dc_lock(dc_id)
        async with lock:
            existing = self._dc_sessions.get(dc_id)
            if existing is not None and not force_recreate:
                if getattr(existing, "is_started", True):
                    return existing

            if existing is not None:
                try:
                    await existing.stop()
                except Exception:
                    pass
                self._dc_sessions.pop(dc_id, None)

            storage_dc = await self._client.storage.dc_id()
            test_mode = await self._client.storage.test_mode()

            if dc_id != storage_dc:
                auth_key = await Auth(self._client, dc_id, test_mode).create()
                session = Session(self._client, dc_id, auth_key, test_mode, is_media=True)
                await session.start()
                for _ in range(3):
                    exported = await self._client.invoke(
                        raw.functions.auth.ExportAuthorization(dc_id=dc_id)
                    )
                    try:
                        await session.invoke(
                            raw.functions.auth.ImportAuthorization(
                                id=exported.id,
                                bytes=exported.bytes,
                            )
                        )
                        break
                    except AuthBytesInvalid:
                        continue
            else:
                auth_key = await self._client.storage.auth_key()
                session = Session(self._client, dc_id, auth_key, test_mode, is_media=True)
                await session.start()

            self._dc_sessions[dc_id] = session
            logger.info("Initialized persistent media session for DC %d", dc_id)
            return session

    async def close(self) -> None:
        for dc_id, session in list(self._dc_sessions.items()):
            try:
                await session.stop()
            except Exception:
                pass
            self._dc_sessions.pop(dc_id, None)

    @staticmethod
    def _build_location(file_id: str) -> tuple[FileId, Any]:
        decoded = FileId.decode(file_id)
        if decoded.file_type in PHOTO_TYPES:
            location = raw.types.InputPhotoFileLocation(
                id=decoded.media_id,
                access_hash=decoded.access_hash,
                file_reference=decoded.file_reference,
                thumb_size=decoded.thumbnail_size or "",
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=decoded.media_id,
                access_hash=decoded.access_hash,
                file_reference=decoded.file_reference,
                thumb_size="",
            )
        return decoded, location

    async def _refresh_file_id(self, message_id: int) -> Optional[str]:
        msg = await self._client.get_messages(self._storage_channel_id, message_id)
        if not msg:
            return None
        media = msg.video or msg.document or msg.audio or msg.photo
        return getattr(media, "file_id", None) if media else None

    async def _fetch_single_chunk(
        self,
        *,
        message_id: int,
        state: dict[str, Any],
        offset: int,
    ) -> bytes:
        for attempt in range(4):
            dc_id: int = state["dc_id"]
            location = state["location"]
            session = await self._get_media_session(dc_id, force_recreate=(attempt > 0 and attempt == 2))
            try:
                res = await session.invoke(
                    raw.functions.upload.GetFile(
                        location=location,
                        offset=offset,
                        limit=STREAM_CHUNK_SIZE,
                    ),
                    sleep_threshold=30,
                )
                return bytes(getattr(res, "bytes", b"") or b"")
            except FloodWait as exc:
                await asyncio.sleep(min(float(exc.value), 10.0))
            except FileMigrate as exc:
                state["dc_id"] = exc.value
            except FileReferenceExpired:
                new_file_id = await self._refresh_file_id(message_id)
                if not new_file_id:
                    raise
                decoded, new_loc = self._build_location(new_file_id)
                state["dc_id"] = decoded.dc_id
                state["location"] = new_loc
            except Exception as exc:
                if attempt == 3:
                    raise
                logger.debug("Retrying GetFile offset=%d on DC %d (%s)", offset, dc_id, exc)
                await asyncio.sleep(0.25)
        return b""

    async def stream_range(
        self,
        *,
        message: Any,
        file_id: str,
        start_byte: int,
        end_byte: int,
    ) -> AsyncIterator[bytes]:
        """Yield exact byte slice `[start_byte, end_byte]` in order using parallel chunk fetches."""
        if not file_id:
            return

        decoded, location = self._build_location(file_id)
        state: dict[str, Any] = {
            "dc_id": decoded.dc_id,
            "location": location,
        }

        first_chunk_idx = start_byte // STREAM_CHUNK_SIZE
        last_chunk_idx = end_byte // STREAM_CHUNK_SIZE
        first_skip = start_byte % STREAM_CHUNK_SIZE
        remaining = (end_byte - start_byte) + 1

        next_to_schedule = first_chunk_idx
        in_flight: dict[int, asyncio.Task[bytes]] = {}

        def _schedule_more() -> None:
            nonlocal next_to_schedule
            while (
                next_to_schedule <= last_chunk_idx
                and len(in_flight) < PARALLEL_CHUNK_WORKERS
            ):
                idx = next_to_schedule
                next_to_schedule += 1
                in_flight[idx] = asyncio.create_task(
                    self._fetch_single_chunk(
                        message_id=message.id,
                        state=state,
                        offset=idx * STREAM_CHUNK_SIZE,
                    )
                )

        try:
            _schedule_more()
            for idx in range(first_chunk_idx, last_chunk_idx + 1):
                task = in_flight.pop(idx)
                chunk = await task
                _schedule_more()

                if not chunk:
                    break
                if idx == first_chunk_idx and first_skip:
                    chunk = chunk[first_skip:]
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                if chunk:
                    yield chunk
                    remaining -= len(chunk)
                if remaining <= 0:
                    break
        finally:
            for pending_task in in_flight.values():
                if not pending_task.done():
                    pending_task.cancel()
            if in_flight:
                await asyncio.gather(*in_flight.values(), return_exceptions=True)
