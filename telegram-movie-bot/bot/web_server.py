"""Koyeb-compatible async HTTP health, branded watch player, and fast range-streaming server."""

from __future__ import annotations

import asyncio
import html
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import quote

from aiohttp import web

from bot.streamer import FastByteStreamer

if TYPE_CHECKING:
    from pyrogram import Client
    from bot.link_generator import SpeedLinkGenerator

logger = logging.getLogger(__name__)

TELEGRAM_CHUNK_SIZE = 1024 * 1024  # Fallback Pyrogram stream_media chunk size (1 MiB)
MESSAGE_CACHE_TTL_SECONDS = 900
TELEGRAM_CHANNEL_URL = "https://t.me/Prime_Movie_YT_Group"
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")

_EXT_MIME_MAP = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".ts": "video/mp2t",
    ".flv": "video/x-flv",
    ".3gp": "video/3gpp",
}

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "spy_movie_botz_logo.png"


def _extract_media_meta(message: Any) -> tuple[Optional[str], int, str, str]:
    """Return `(file_name, file_size, mime_type, file_id)` for a Pyrogram message."""
    media = message.video or message.document or message.audio
    if not media:
        return None, 0, "application/octet-stream", ""

    file_name = getattr(media, "file_name", None) or f"media_{message.id}.mp4"
    file_size = int(getattr(media, "file_size", 0) or 0)
    file_id = str(getattr(media, "file_id", "") or "")

    raw_mime = str(getattr(media, "mime_type", None) or "").strip()
    ext = Path(file_name).suffix.lower()
    ext_mime = _EXT_MIME_MAP.get(ext) or mimetypes.guess_type(file_name)[0]
    if raw_mime and raw_mime != "application/octet-stream":
        mime_type = raw_mime
    else:
        mime_type = ext_mime or "video/mp4"

    return file_name, file_size, mime_type, file_id


def _format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}" if unit in ("GB", "TB") else f"{size:.0f} {unit}"
        size /= 1024
    return ""


class BotWebServer:
    """Async aiohttp server providing Koyeb health endpoints and fast byte-range streaming."""

    def __init__(
        self,
        port: int,
        storage_channel_id: int,
        start_time: float,
        link_generator: Optional["SpeedLinkGenerator"] = None,
    ) -> None:
        self._port = port
        self._storage_channel_id = storage_channel_id
        self._start_time = start_time
        self._link_generator = link_generator
        self._tg_client: Optional["Client"] = None
        self._streamer: Optional[FastByteStreamer] = None
        self._msg_cache: dict[int, tuple[float, Any]] = {}
        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._register_routes()

    def attach_telegram_client(self, client: "Client") -> None:
        self._tg_client = client
        self._streamer = FastByteStreamer(client, self._storage_channel_id)

    def _register_routes(self) -> None:
        self._app.router.add_get("/", self.handle_root)
        self._app.router.add_get("/health", self.handle_health)
        self._app.router.add_get("/assets/logo.png", self.handle_logo)
        self._app.router.add_get("/watch/{message_id}", self.handle_watch_page)
        self._app.router.add_route("GET", "/stream/{message_id}", self.handle_stream)
        self._app.router.add_route("HEAD", "/stream/{message_id}", self.handle_stream)
        self._app.router.add_route("GET", "/download/{message_id}", self.handle_download)
        self._app.router.add_route("HEAD", "/download/{message_id}", self.handle_download)
        self._app.router.add_route("GET", "/dl/{sig}/{message_id}", self.handle_signed_download)
        self._app.router.add_route("HEAD", "/dl/{sig}/{message_id}", self.handle_signed_download)
        self._app.router.add_route("GET", "/dl/{sig}/{message_id}/{filename}", self.handle_signed_download)
        self._app.router.add_route("HEAD", "/dl/{sig}/{message_id}/{filename}", self.handle_signed_download)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self._port)
        await self._site.start()
        logger.info("HTTP server listening on 0.0.0.0:%d", self._port)

    async def stop(self) -> None:
        if self._streamer is not None:
            await self._streamer.close()
            self._streamer = None
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
                "service": "spy-movie-botz-catalog",
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

    async def handle_logo(self, request: web.Request) -> web.StreamResponse:
        if not LOGO_PATH.is_file():
            return web.Response(status=404, text="Logo not found")
        return web.FileResponse(
            LOGO_PATH,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    async def handle_watch_page(self, request: web.Request) -> web.StreamResponse:
        """Serve SPY MOVIE BOTZ player page for browsers, or raw byte-range stream for APKs."""
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
        raw_file_name: Optional[str] = None
        safe_file_name = f"Stream #{msg_id}"
        size_label = ""
        if self._tg_client and getattr(self._tg_client, "is_connected", False):
            try:
                msg = await self._get_cached_message(msg_id)
                if msg:
                    file_name, file_size, _, _ = _extract_media_meta(msg)
                    if file_name:
                        raw_file_name = file_name
                        safe_file_name = html.escape(file_name)
                    if file_size > 0:
                        size_label = _format_size(file_size)
            except Exception as exc:
                logger.debug("Could not fetch metadata for watch page %s: %s", msg_id, exc)

        scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
        host = request.headers.get("X-Forwarded-Host", request.host)
        stream_abs_url = f"{scheme}://{host}/stream/{msg_id}"
        if self._link_generator:
            speed_links = self._link_generator.generate(
                message_id=msg_id,
                file_name=raw_file_name,
            )
            direct_dl_path = f"/dl/{speed_links.signature}/{msg_id}/{speed_links.safe_filename}"
        else:
            direct_dl_path = f"/download/{msg_id}"
        vlc_url = f"vlc://{stream_abs_url}"
        encoded_title = quote(raw_file_name or f"Stream {msg_id}")
        mx_url = f"intent:{stream_abs_url}#Intent;package=com.mxtech.videoplayer.ad;S.title={encoded_title};end"
        meta_line = f"{safe_file_name} &middot; {size_label}" if size_label else safe_file_name

        page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>SPY MOVIE BOTZ — {safe_file_name}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #090d16;
      color: #f8fafc;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      min-height: 100vh;
      padding: 24px 16px 36px;
    }}
    .wrap {{
      width: 100%;
      max-width: 920px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    .brand {{
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      margin-bottom: 18px;
    }}
    .logo {{
      width: 118px;
      height: 118px;
      border-radius: 50%;
      object-fit: cover;
      border: 2px solid rgba(251, 191, 36, 0.55);
      box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
      margin-bottom: 14px;
    }}
    .brand-title {{
      font-size: 1.5rem;
      font-weight: 800;
      letter-spacing: 0.04em;
      margin: 0;
      color: #ffffff;
    }}
    .file-subtitle {{
      font-size: 0.875rem;
      color: #94a3b8;
      margin: 6px 0 0;
      word-break: break-word;
      max-width: 680px;
    }}
    .player-shell {{
      width: 100%;
      background: #000000;
      border: 1px solid #1e293b;
      border-radius: 12px;
      overflow: hidden;
    }}
    video {{
      display: block;
      width: 100%;
      max-height: 72vh;
      background: #000000;
    }}
    .player-error {{
      display: none;
      width: 100%;
      margin-top: 10px;
      padding: 12px 14px;
      border-radius: 8px;
      background: rgba(245, 158, 11, 0.12);
      border: 1px solid rgba(245, 158, 11, 0.35);
      color: #fcd34d;
      font-size: 0.85rem;
      line-height: 1.45;
      text-align: center;
    }}
    .player-error.visible {{
      display: block;
    }}
    .actions {{
      width: 100%;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }}
    a.btn, button.btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 11px 18px;
      border-radius: 8px;
      font-size: 0.875rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: background 0.15s ease;
    }}
    .btn-primary {{
      background: #2563eb;
      color: #ffffff;
    }}
    .btn-primary:hover {{ background: #1d4ed8; }}
    .btn-secondary {{
      background: #141b2d;
      color: #e2e8f0;
      border-color: #26324d;
    }}
    .btn-secondary:hover {{ background: #1e293b; }}
    .notice-card {{
      width: 100%;
      margin-top: 22px;
      padding: 18px 20px;
      background: #101726;
      border: 1px solid #1e293b;
      border-radius: 12px;
      text-align: center;
      line-height: 1.6;
    }}
    .eac3-note {{
      margin: 0 0 14px;
      font-size: 0.9rem;
      font-weight: 600;
      color: #fbbf24;
    }}
    .disclaimer-heading {{
      margin: 0 0 6px;
      font-size: 0.8rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      color: #e2e8f0;
    }}
    .disclaimer-body {{
      margin: 0 0 12px;
      font-size: 0.8125rem;
      color: #94a3b8;
    }}
    .copyright {{
      margin: 0;
      font-size: 0.75rem;
      color: #64748b;
    }}
    .btn-channel {{
      width: 100%;
      margin-top: 16px;
      padding: 14px 20px;
      background: #0088cc;
      color: #ffffff;
      font-size: 0.95rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      border-radius: 10px;
    }}
    .btn-channel:hover {{
      background: #0077b5;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <img class="logo" src="/assets/logo.png" alt="SPY MOVIE BOTZ" />
      <h1 class="brand-title">SPY MOVIE BOTZ</h1>
      <p class="file-subtitle">{meta_line}</p>
    </div>

    <div class="player-shell">
      <video id="videoPlayer" controls playsinline preload="metadata" src="/stream/{msg_id}"></video>
    </div>

    <div id="playerError" class="player-error">
      Browser could not decode this video format directly (common for MKV/HEVC/EAC3 files). Please tap <strong>Open in VLC</strong>, <strong>Open in MX Player</strong>, or <strong>Direct Download</strong> below.
    </div>

    <div class="actions">
      <a class="btn btn-primary" href="{direct_dl_path}">Direct Download</a>
      <a class="btn btn-secondary" href="{vlc_url}">Open in VLC</a>
      <a class="btn btn-secondary" href="{mx_url}">Open in MX Player</a>
    </div>

    <div class="notice-card">
      <p class="eac3-note">Browser Does Not Support EAC3 Audio. If No Sound, Please Use External Players.</p>
      <p class="disclaimer-heading">DISCLAIMER</p>
      <p class="disclaimer-body">
        SpyMovieBotz Does Not Host Any Content. All Videos Are Provided By Third-Party Services.
        We Are Not Responsible For Any Legal Issues.
      </p>
      <p class="copyright">
        2026 SPYMOVIEBOTZ All rights reserved.<br />
        This Service Is For Personal Use Only
      </p>
    </div>

    <a class="btn btn-channel" href="{TELEGRAM_CHANNEL_URL}" target="_blank" rel="noopener noreferrer">JOIN TELEGRAM CHANNEL</a>
  </div>

  <script>
    (function () {{
      var video = document.getElementById('videoPlayer');
      var errBox = document.getElementById('playerError');
      if (video && errBox) {{
        video.addEventListener('error', function () {{
          errBox.classList.add('visible');
        }});
      }}
    }})();
  </script>
</body>
</html>"""
        return web.Response(text=page, content_type="text/html")

    async def _get_cached_message(self, msg_id: int) -> Any:
        """Cache storage channel Message metadata in RAM to accelerate parallel range requests."""
        now = time.monotonic()
        cached = self._msg_cache.get(msg_id)
        if cached and (now - cached[0]) < MESSAGE_CACHE_TTL_SECONDS:
            return cached[1]

        message = await self._tg_client.get_messages(self._storage_channel_id, msg_id)
        if message and not getattr(message, "empty", False):
            if len(self._msg_cache) >= 500:
                oldest_key = min(self._msg_cache, key=lambda k: self._msg_cache[k][0])
                self._msg_cache.pop(oldest_key, None)
            self._msg_cache[msg_id] = (now, message)
        return message

    async def handle_stream(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_telegram_media(request, as_attachment=False)

    async def handle_download(self, request: web.Request) -> web.StreamResponse:
        return await self._serve_telegram_media(request, as_attachment=True)

    async def handle_signed_download(self, request: web.Request) -> web.StreamResponse:
        raw_id = request.match_info["message_id"]
        sig = request.match_info.get("sig", "")
        if not raw_id.isdigit():
            return web.json_response({"error": "Invalid message ID"}, status=400)
        if self._link_generator and not self._link_generator.verify(int(raw_id), sig):
            return web.json_response({"error": "Invalid download signature"}, status=403)
        return await self._serve_telegram_media(request, as_attachment=True)

    async def _stream_via_pyrogram_fallback(
        self,
        *,
        message: Any,
        response: web.StreamResponse,
        start_byte: int,
        content_length: int,
    ) -> None:
        """Sequential Pyrogram stream_media fallback when direct DC streaming cannot start."""
        start_chunk = start_byte // TELEGRAM_CHUNK_SIZE
        first_chunk_skip = start_byte % TELEGRAM_CHUNK_SIZE
        remaining = content_length

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
            message = await self._get_cached_message(msg_id)
        except Exception as exc:
            logger.error("Failed to fetch message %s from storage channel: %s", msg_id, exc)
            return web.json_response({"error": "Failed to retrieve media"}, status=502)

        if not message or getattr(message, "empty", False):
            return web.json_response({"error": "Media not found"}, status=404)

        file_name, file_size, mime_type, file_id = _extract_media_meta(message)
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
            "Cache-Control": "public, max-age=3600",
        }
        if status_code == 206:
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{file_size}"

        if request.method == "HEAD":
            return web.Response(status=status_code, headers=headers)

        response = web.StreamResponse(status=status_code, headers=headers)
        await response.prepare(request)

        bytes_sent = 0
        try:
            if self._streamer is not None and file_id:
                try:
                    async for chunk in self._streamer.stream_range(
                        message=message,
                        file_id=file_id,
                        start_byte=start_byte,
                        end_byte=end_byte,
                    ):
                        await response.write(chunk)
                        bytes_sent += len(chunk)
                except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    if bytes_sent > 0:
                        raise
                    logger.warning(
                        "FastByteStreamer fallback to Pyrogram stream_media for msg %s: %s",
                        msg_id,
                        exc,
                    )
                    await self._stream_via_pyrogram_fallback(
                        message=message,
                        response=response,
                        start_byte=start_byte,
                        content_length=content_length,
                    )
            else:
                await self._stream_via_pyrogram_fallback(
                    message=message,
                    response=response,
                    start_byte=start_byte,
                    content_length=content_length,
                )
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            logger.debug("Client disconnected during stream of message %s", msg_id)
        except Exception as exc:
            logger.error("Error streaming message %s: %s", msg_id, exc)

        try:
            await response.write_eof()
        except Exception:
            pass
        return response

