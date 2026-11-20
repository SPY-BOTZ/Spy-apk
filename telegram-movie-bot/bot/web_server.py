"""Koyeb-compatible async HTTP health and Telegram media streaming server."""

from __future__ import annotations

import html
import logging
import mimetypes
import re
import time
from typing import TYPE_CHECKING, Any, Optional

from aiohttp import web

if TYPE_CHECKING:
    from pyrogram import Client

logger = logging.getLogger(__name__)

TELEGRAM_CHUNK_SIZE = 1024 * 1024  # Pyrogram stream_media streams in 1 MiB chunks
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _extract_media_meta(message: Any) -> tuple[Optional[str], int, str]:
    """Return `(file_name, file_size, mime_type)` for a Pyrogram message."""
    media = message.video or message.document or message.audio
    if not media:
        return None, 0, "application/octet-stream"

    file_name = getattr(media, "file_name", None) or f"media_{message.id}.mp4"
    file_size = int(getattr(media, "file_size", 0) or 0)
    mime_type = (
        getattr(media, "mime_type", None)
        or mimetypes.guess_type(file_name)[0]
        or "video/mp4"
    )
    return file_name, file_size, mime_type


class BotWebServer:
    """Async aiohttp server providing Koyeb health endpoints and optional range streaming."""

    def __init__(
        self,
        port: int,
        storage_channel_id: int,
        start_time: float,
    ) -> None:
        self._port = port
        self._storage_channel_id = storage_channel_id
        self._start_time = start_time
        self._tg_client: Optional["Client"] = None
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._register_routes()

    def attach_telegram_client(self, client: "Client") -> None:
        self._tg_client = client

    def _register_routes(self) -> None:
        self._app.router.add_get("/", self.handle_root)
        self._app.router.add_get("/health", self.handle_health)
        self._app.router.add_get("/watch/{message_id}", self.handle_watch_page)
        self._app.router.add_get("/stream/{message_id}", self.handle_stream)
        self._app.router.add_get("/download/{message_id}", self.handle_download)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await self._site.start()
        logger.info("HTTP server listening on 0.0.0.0:%d", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("HTTP server stopped cleanly")

    async def handle_root(self, request: web.Request) -> web.Response:
        uptime = int(time.monotonic() - self._start_time)
        return web.json_response(
            {
                "status": "ok",
                "service": "telegram-movie-catalog-bot",
                "telegram_connected": bool(
                    self._tg_client and getattr(self._tg_client, "is_connected", False)
                ),
                "uptime_seconds": uptime,
            },
            status=200,
        )

    async def handle_health(self, request: web.Request) -> web.Response:
        uptime = int(time.monotonic() - self._start_time)
        return web.json_response(
            {
                "status": "healthy",
                "port": self._port,
                "uptime_seconds": uptime,
            },
            status=200,
        )

    async def handle_watch_page(self, request: web.Request) -> web.StreamResponse:
        """Serve HTML5 player for browser tabs, or raw byte-range stream for video players."""
        accept = request.headers.get("Accept", "")
        has_range = "Range" in request.headers
        wants_player = request.query.get("player") == "1" or (
            "text/html" in accept and not has_range and request.query.get("raw") != "1"
        )
        if not wants_player:
            return await self._serve_telegram_media(request, as_attachment=False)

        raw_id = request.match_info["message_id"]
        if not raw_id.isdigit():
            return web.json_response({"error": "Invalid message ID"}, status=400)

        msg_id = int(raw_id)
        safe_title = f"Stream #{msg_id}"
        if self._tg_client and getattr(self._tg_client, "is_connected", False):
            try:
                msg = await self._tg_client.get_messages(self._storage_channel_id, msg_id)
                file_name, _, _ = _extract_media_meta(msg)
                if file_name:
                    safe_title = html.escape(file_name)
            except Exception as exc:
                logger.debug("Could not fetch metadata for watch page %s: %s", msg_id, exc)

        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      background: #0b0f19;
      color: #f8fafc;
      font-family: system-ui, -apple-system, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 16px;
      box-sizing: border-box;
    }}
    .wrap {{ width: 100%; max-width: 960px; }}
    h1 {{ font-size: 1rem; font-weight: 600; margin: 0 0 12px; color: #e2e8f0; }}
    video {{
      width: 100%;
      max-height: 80vh;
      background: #000;
      border-radius: 8px;
    }}
    .actions {{ margin-top: 12px; }}
    a.btn {{
      display: inline-block;
      padding: 10px 16px;
      background: #2563eb;
      color: #fff;
      text-decoration: none;
      border-radius: 6px;
      font-size: 0.875rem;
      font-weight: 500;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{safe_title}</h1>
    <video controls playsinline preload="metadata" src="/stream/{msg_id}"></video>
    <div class="actions">
      <a class="btn" href="/download/{msg_id}">Download File</a>
    </div>
  </div>
</body>
</html>"""
        return web.Response(text=page, content_type="text/html")

    async def handle_stream(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_telegram_media(request, as_attachment=False)

    async def handle_download(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_telegram_media(request, as_attachment=True)

    async def _serve_telegram_media(
        self,
        request: web.Request,
        as_attachment: bool,
    ) -> web.StreamResponse:
        if not self._tg_client or not getattr(self._tg_client, "is_connected", False):
            return web.json_response({"error": "Telegram client not ready"}, status=503)

        raw_id = request.match_info["message_id"]
        if not raw_id.isdigit():
            return web.json_response({"error": "Invalid message ID"}, status=400)
        msg_id = int(raw_id)

        try:
            message = await self._tg_client.get_messages(self._storage_channel_id, msg_id)
        except Exception as exc:
            logger.error("Failed to fetch message %s from storage channel: %s", msg_id, exc)
            return web.json_response({"error": "Failed to retrieve media"}, status=502)

        if not message or getattr(message, "empty", False):
            return web.json_response({"error": "Media not found"}, status=404)

        file_name, file_size, mime_type = _extract_media_meta(message)
        if not file_name or file_size <= 0:
            return web.json_response({"error": "Message contains no streamable media"}, status=404)

        range_header = request.headers.get("Range")
        start_byte = 0
        end_byte = file_size - 1
        status_code = 200

        if range_header:
            match = _RANGE_RE.match(range_header.strip())
            if not match:
                return web.Response(
                    status=416,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            start_str, end_str = match.groups()
            if start_str:
                start_byte = int(start_str)
                end_byte = int(end_str) if end_str else file_size - 1
            elif end_str:
                suffix_len = int(end_str)
                start_byte = max(0, file_size - suffix_len)
                end_byte = file_size - 1

            if start_byte > end_byte or start_byte >= file_size:
                return web.Response(
                    status=416,
                    headers={"Content-Range": f"bytes */{file_size}"},
                )
            end_byte = min(end_byte, file_size - 1)
            status_code = 206

        content_length = (end_byte - start_byte) + 1
        disposition = "attachment" if as_attachment else "inline"
        safe_filename = file_name.replace('"', "")

        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"

        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)

        start_chunk = start_byte // TELEGRAM_CHUNK_SIZE
        first_chunk_skip = start_byte % TELEGRAM_CHUNK_SIZE
        remaining = content_length

        try:
            async for chunk in self._tg_client.stream_media(message, offset=start_chunk):
                if not chunk or remaining <= 0:
                    break
                if first_chunk_skip:
                    chunk = chunk[first_chunk_skip:]
                    first_chunk_skip = 0
                if len(chunk) > remaining:
                    chunk = chunk[:remaining]
                if chunk:
                    await response.write(chunk)
                    remaining -= len(chunk)
                if remaining <= 0:
                    break
        except (ConnectionResetError, BrokenPipeError):
            logger.debug("Client disconnected during stream of message %s", msg_id)
        except Exception as exc:
            logger.error("Error streaming message %s: %s", msg_id, exc)

        try:
            await response.write_eof()
        except Exception:
            pass
        return response
