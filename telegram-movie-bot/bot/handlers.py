"""Pyrogram bot command handlers, auto-indexer, and interactive selection flows."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import Settings
from bot.firebase_client import FirebaseClient, FirebaseError
from bot.link_generator import SpeedLinkGenerator
from bot.parser import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_QUALITIES,
    ParsedMedia,
    extract_direct_url,
    extract_telegram_link,
    parse_media_metadata,
)
from bot.tmdb_client import TmdbClient, TmdbMetadata

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingUpload:
    token: str
    admin_id: int
    chat_id: int
    parsed: ParsedMedia
    tmdb: TmdbMetadata
    file_id: str
    message_id: int
    direct_url: Optional[str] = None
    language: Optional[str] = None
    quality: Optional[str] = None
    created_ts: float = field(default_factory=time.monotonic)


def _format_uptime(seconds: float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or parts:
        parts.append(f"{hours}h")
    if minutes or parts:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


_LANG_HEADER_RE = re.compile(r"^🌐\s*Language:[^\n]*(?:\n\n|\n)?")


def _quality_badge(quality: str) -> str:
    return {
        "480p": "SD",
        "720p": "HD",
        "1080p": "FHD",
        "4K": "4K",
    }.get(quality, "HD")


def format_apk_badge(language: str, quality: str) -> str:
    """Format poster card badge so APK users immediately see Language + Quality."""
    return f"{language} • {quality} • {_quality_badge(quality)}"


def format_apk_description(base_description: str, language: str, quality: str) -> str:
    """Prefix Language & Quality at the top of description right under movie title in the APK."""
    clean_desc = _LANG_HEADER_RE.sub("", (base_description or "").strip()).strip()
    if clean_desc == "No description available.":
        clean_desc = ""
    header = f"🌐 Language: {language} | 🎞 Quality: {quality} ({_quality_badge(quality)})"
    if not clean_desc:
        return header
    return f"{header}\n\n{clean_desc}"


def build_movie_record(
    *,
    tmdb: TmdbMetadata,
    language: str,
    quality: str,
    file_id: str,
    message_id: int,
    base_url: str,
    direct_url: Optional[str] = None,
    file_name: Optional[str] = None,
    link_generator: Optional[SpeedLinkGenerator] = None,
    schema_profile: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the Firebase `/movies` schema record aligned with existing DB types."""
    if link_generator is not None:
        links = link_generator.generate(
            message_id=message_id,
            file_name=file_name or f"{tmdb.title}.{tmdb.year or 'movie'}.mp4",
            direct_url=direct_url,
        )
        watch_url = links.watch_url
        download_url = links.direct_download_url
    elif direct_url:
        watch_url = direct_url
        download_url = direct_url
    else:
        watch_url = f"{base_url}/watch/{message_id}"
        download_url = f"{base_url}/download/{message_id}"

    watch480 = watch_url if quality == "480p" else ""
    watch780 = watch_url if quality == "720p" else ""
    watch1080 = watch_url if quality in ("1080p", "4K") else ""

    download480 = download_url if quality == "480p" else ""
    download780 = download_url if quality == "720p" else ""
    download1080 = download_url if quality in ("1080p", "4K") else ""

    profile = schema_profile or {}
    created_mode = profile.get("createdAt_type", "int_ms")
    now_ms = int(time.time() * 1000)
    if created_mode == "iso_str":
        created_at: Any = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    elif created_mode == "int_sec":
        created_at = int(time.time())
    else:
        created_at = now_ms

    year_val: Any = tmdb.year
    if profile.get("year_type") == "int" and str(tmdb.year).isdigit():
        year_val = int(tmdb.year)

    rating_val: Any = tmdb.rating
    if profile.get("rating_type") == "str":
        rating_val = str(tmdb.rating)

    msg_id_val: Any = str(message_id) if profile.get("messageId_type") == "str" else message_id

    cast_val: Any = (
        list(tmdb.cast)
        if profile.get("cast_type") == "list"
        else ", ".join(tmdb.cast)
    )
    badge_val = format_apk_badge(language, quality)
    formatted_desc = format_apk_description(tmdb.description, language, quality)

    record: dict[str, Any] = {
        "title": tmdb.title,
        "year": year_val,
        "posterUrl": tmdb.poster_url,
        "category": tmdb.category,
        "rating": rating_val,
        "qualityBadge": badge_val,
        "language": language,
        "quality": quality,
        "cast": cast_val,
        "description": formatted_desc,
        "watch480": watch480,
        "watch780": watch780,
        "watch1080": watch1080,
        "watchLink": watch_url,
        "download480": download480,
        "download780": download780,
        "download1080": download1080,
        "downloadLink": download_url,
        "fileId": file_id,
        "messageId": msg_id_val,
        "createdAt": created_at,
    }

    sample_keys = set(profile.get("sample_keys") or [])
    if "cardBadge" in sample_keys:
        record["cardBadge"] = badge_val
    if "badge" in sample_keys:
        record["badge"] = badge_val
    if "genres" in sample_keys:
        record["genres"] = (
            list(tmdb.genres or [tmdb.category])
            if profile.get("genres_type") == "list"
            else ", ".join(tmdb.genres or [tmdb.category])
        )
    if "watch720" in sample_keys:
        record["watch720"] = watch780
    if "download720" in sample_keys:
        record["download720"] = download780
    if "timestamp" in sample_keys:
        record["timestamp"] = now_ms
    if "poster" in sample_keys:
        record["poster"] = tmdb.poster_url
    if "image" in sample_keys:
        record["image"] = tmdb.poster_url
    if "thumbnail" in sample_keys:
        record["thumbnail"] = tmdb.poster_url
    if "thumb" in sample_keys:
        record["thumb"] = tmdb.poster_url
    if "overview" in sample_keys:
        record["overview"] = formatted_desc
    if "plot" in sample_keys:
        record["plot"] = formatted_desc
    if "genre" in sample_keys:
        record["genre"] = tmdb.category
    if "views" in sample_keys:
        record["views"] = 0
    if "type" in sample_keys:
        record["type"] = "series" if tmdb.media_type == "tv" else "movie"
    if "status" in sample_keys and profile.get("sample_status"):
        record["status"] = profile["sample_status"]

    return record


def _build_selection_keyboard(pending: PendingUpload) -> InlineKeyboardMarkup:
    lang_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if pending.language == lang else ''}{lang}",
            callback_data=f"sel_lang:{pending.token}:{lang}",
        )
        for lang in SUPPORTED_LANGUAGES
    ]
    qual_buttons = [
        InlineKeyboardButton(
            text=f"{'✅ ' if pending.quality == qual else ''}{qual}",
            callback_data=f"sel_qual:{pending.token}:{qual}",
        )
        for qual in SUPPORTED_QUALITIES
    ]

    rows = [
        lang_buttons[:3],
        lang_buttons[3:],
        qual_buttons,
        [
            InlineKeyboardButton("✅ Save to Firebase", callback_data=f"sel_save:{pending.token}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"sel_cancel:{pending.token}"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


def _infer_quality_from_message(msg: Optional[Message], is_series: bool = False) -> Optional[str]:
    """Detect quality from Telegram video resolution or file size when filename lacks tags."""
    if not msg:
        return None
    video = getattr(msg, "video", None)
    if video:
        height = int(getattr(video, "height", 0) or 0)
        width = int(getattr(video, "width", 0) or 0)
        dim = max(height, round(width * 9 / 16)) if (height or width) else 0
        if dim >= 2000:
            return "4K"
        if dim >= 1000:
            return "1080p"
        if dim >= 680:
            return "720p"
        if dim > 0:
            return "480p"

    media = video or getattr(msg, "document", None)
    size_bytes = int(getattr(media, "file_size", 0) or 0) if media else 0
    if size_bytes > 0:
        size_mb = size_bytes / (1024 * 1024)
        if is_series:
            if size_mb >= 1800:
                return "4K"
            if size_mb >= 650:
                return "1080p"
            if size_mb >= 250:
                return "720p"
            return "480p"
        if size_mb >= 3500:
            return "4K"
        if size_mb >= 1400:
            return "1080p"
        if size_mb >= 600:
            return "720p"
        return "480p"
    return None


def _build_user_download_keyboard(watch_url: str, download_url: str) -> Optional[InlineKeyboardMarkup]:
    row: list[InlineKeyboardButton] = []
    if download_url.startswith(("http://", "https://")) and "localhost" not in download_url:
        row.append(InlineKeyboardButton("⚡ Direct Download", url=download_url))
    if watch_url.startswith(("http://", "https://")) and "localhost" not in watch_url:
        row.append(InlineKeyboardButton("▶ Watch Online", url=watch_url))
    return InlineKeyboardMarkup([row]) if row else None


def _build_post_save_keyboard(
    firebase_key: str,
    watch_url: str,
    download_url: str = "",
) -> InlineKeyboardMarkup:
    lang_buttons = [
        InlineKeyboardButton(lang, callback_data=f"upd_lang:{firebase_key}:{lang}")
        for lang in SUPPORTED_LANGUAGES
    ]
    qual_row = [
        InlineKeyboardButton(qual, callback_data=f"upd_qual:{firebase_key}:{qual}")
        for qual in SUPPORTED_QUALITIES
    ]
    links_row: list[InlineKeyboardButton] = []
    if download_url.startswith(("http://", "https://")) and "localhost" not in download_url:
        links_row.append(InlineKeyboardButton("⚡ Direct Download", url=download_url))
    if watch_url.startswith(("http://", "https://")) and "localhost" not in watch_url:
        links_row.append(InlineKeyboardButton("▶ Watch Link", url=watch_url))

    rows = [lang_buttons[:3], lang_buttons[3:], qual_row]
    if links_row:
        rows.append(links_row)
    rows.append([InlineKeyboardButton("🗑 Delete Entry", callback_data=f"del_entry:{firebase_key}")])
    return InlineKeyboardMarkup(rows)


def _format_confirmation_text(
    record: dict[str, Any],
    firebase_key: str,
    tmdb_matched: bool,
) -> str:
    year_part = f" ({record['year']})" if record["year"] else ""
    tmdb_note = "Matched via TMDB" if tmdb_matched else "Fallback metadata (TMDB no match)"
    raw_desc = str(record.get("description", ""))
    desc = _LANG_HEADER_RE.sub("", raw_desc).strip() or raw_desc
    if len(desc) > 160:
        desc = f"{desc[:157]}..."

    cast_raw = record.get("cast")
    cast_str = ", ".join(cast_raw) if isinstance(cast_raw, list) else str(cast_raw or "")
    cast_line = f"🎭 **Cast:** {cast_str}\n" if cast_str else ""
    short_badge = _quality_badge(str(record.get("quality", "720p")))

    return (
        f"✅ **Cataloged to Firebase**\n\n"
        f"🎬 **Title:** {record['title']}{year_part}\n"
        f"🏷 **Category:** {record['category']} | ⭐ **Rating:** {record['rating']}\n"
        f"🌐 **Language:** {record['language']} | 🎞 **Quality:** {record['quality']} ({short_badge})\n"
        f"{cast_line}"
        f"📝 **Plot:** {desc}\n"
        f"🔑 **Firebase Key:** `{firebase_key}`\n"
        f"📦 **Message ID:** `{record['messageId']}`\n"
        f"📡 **Metadata:** {tmdb_note}\n\n"
        f"Tap a Language or Quality button below to adjust this entry anytime."
    )


def register_handlers(
    app: Client,
    settings: Settings,
    firebase: FirebaseClient,
    tmdb: TmdbClient,
    start_time: float,
    link_generator: Optional[SpeedLinkGenerator] = None,
) -> None:
    """Attach all command, upload, and callback handlers to the Pyrogram client."""

    if link_generator is None:
        link_generator = SpeedLinkGenerator(
            bot_token=settings.bot_token,
            api_hash=settings.api_hash,
            base_url=settings.base_url,
        )

    pending_uploads: dict[str, PendingUpload] = {}

    def _prune_expired_pending() -> None:
        cutoff = time.monotonic() - 1800  # 30 minutes
        expired = [k for k, v in pending_uploads.items() if v.created_ts < cutoff]
        for k in expired:
            pending_uploads.pop(k, None)

    async def _require_admin(message: Message) -> bool:
        uid = message.from_user.id if message.from_user else None
        if firebase.is_admin(uid):
            return True
        if message.chat and message.chat.type.name == "PRIVATE":
            await message.reply_text(
                "⛔ **Access Denied**\nOnly authorized admins can use this bot.",
                quote=True,
            )
        return False

    async def _copy_to_storage(msg: Message) -> tuple[str, int]:
        """Copy media message into STORAGE_CHANNEL_ID and return `(file_id, storage_msg_id)`."""
        media = msg.video or msg.document
        file_id = getattr(media, "file_id", "") if media else ""

        if msg.chat and msg.chat.id == settings.storage_channel_id:
            return file_id, msg.id

        try:
            copied = await msg.copy(settings.storage_channel_id)
            return file_id, copied.id
        except FloodWait as wait_exc:
            await asyncio.sleep(wait_exc.value)
            copied = await msg.copy(settings.storage_channel_id)
            return file_id, copied.id
        except RPCError as exc:
            logger.warning(
                "Could not copy message %s to STORAGE_CHANNEL_ID (%s): %s. "
                "Using original message ID as fallback.",
                msg.id,
                settings.storage_channel_id,
                exc,
            )
            return file_id, msg.id

    async def _finalize_and_push(
        *,
        status_msg: Optional[Message],
        reply_target: Optional[Message],
        tmdb_meta: TmdbMetadata,
        language: str,
        quality: str,
        file_id: str,
        message_id: int,
        direct_url: Optional[str] = None,
        file_name: Optional[str] = None,
        send_confirmation: bool = True,
    ) -> None:
        if tmdb_meta.description == "No description available.":
            tmdb_meta = TmdbMetadata(
                title=tmdb_meta.title,
                year=tmdb_meta.year,
                poster_url=tmdb_meta.poster_url,
                rating=tmdb_meta.rating,
                description="",
                category=tmdb_meta.category,
                original_language=tmdb_meta.original_language,
                tmdb_id=tmdb_meta.tmdb_id,
                media_type=tmdb_meta.media_type,
                matched=tmdb_meta.matched,
                cast=tmdb_meta.cast,
                genres=tmdb_meta.genres,
            )

        try:
            schema_profile = await firebase.detect_schema_profile()
        except FirebaseError:
            schema_profile = None

        record = build_movie_record(
            tmdb=tmdb_meta,
            language=language,
            quality=quality,
            file_id=file_id,
            message_id=message_id,
            base_url=settings.base_url,
            direct_url=direct_url,
            file_name=file_name,
            link_generator=link_generator,
            schema_profile=schema_profile,
        )
        try:
            firebase_key = await firebase.push_movie(record)
            sample_keys = set((schema_profile or {}).get("sample_keys") or [])
            if "id" in sample_keys:
                await firebase.update_movie(firebase_key, {"id": firebase_key})
        except FirebaseError as exc:
            logger.error("Firebase push failed: %s", exc)
            if status_msg:
                await status_msg.edit_text(f"❌ **Firebase Upload Failed**\n`{exc}`")
            return

        if not send_confirmation:
            logger.info("Auto-cataloged %r -> Firebase key %s", record["title"], firebase_key)
            return

        text = _format_confirmation_text(record, firebase_key, tmdb_meta.matched)
        keyboard = _build_post_save_keyboard(
            firebase_key,
            record["watchLink"],
            record["downloadLink"],
        )

        if reply_target and record["posterUrl"].startswith(("http://", "https://")):
            try:
                await reply_target.reply_photo(
                    photo=record["posterUrl"],
                    caption=text[:1024],
                    reply_markup=keyboard,
                    quote=True,
                )
                if status_msg:
                    try:
                        await status_msg.delete()
                    except RPCError:
                        pass
                return
            except RPCError as exc:
                logger.debug("reply_photo fallback to text confirmation: %s", exc)

        if status_msg:
            await status_msg.edit_text(
                text,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )

    async def _send_user_download_card(message: Message, firebase_key: str) -> None:
        try:
            movie = await firebase.get_movie(firebase_key)
        except FirebaseError as exc:
            await message.reply_text(f"❌ Could not fetch movie: `{exc}`", quote=True)
            return

        if not movie:
            await message.reply_text("❌ Movie not found or was removed.", quote=True)
            return

        title = str(movie.get("title") or "Movie")
        year = str(movie.get("year") or "")
        year_part = f" ({year})" if year else ""
        lang = str(movie.get("language") or "Hindi")
        qual = str(movie.get("quality") or "HD")
        rating = movie.get("rating", 0.0)
        desc = str(movie.get("description") or "")
        if len(desc) > 200:
            desc = f"{desc[:197]}..."

        msg_id_raw = movie.get("messageId")
        watch_url = str(movie.get("watchLink") or "")
        download_url = str(movie.get("downloadLink") or "")
        if isinstance(msg_id_raw, int) or (isinstance(msg_id_raw, str) and msg_id_raw.isdigit()):
            mid = int(msg_id_raw)
            if "/dl/" not in download_url and not download_url.startswith(("http://", "https://")):
                links = link_generator.generate(message_id=mid, file_name=f"{title}.{year or 'hd'}.mp4")
                download_url = links.direct_download_url
                watch_url = watch_url or links.watch_url

        caption = (
            f"🎬 **{title}{year_part}**\n"
            f"⭐ **Rating:** `{rating}` | 🌐 **Language:** {lang} | 🎞 **Quality:** {qual}\n\n"
            f"{desc}\n\n"
            f"Tap **⚡ Direct Download** below for high-speed download."
        )
        keyboard = _build_user_download_keyboard(watch_url, download_url)
        poster = str(movie.get("posterUrl") or "")
        if poster.startswith(("http://", "https://")):
            try:
                await message.reply_photo(
                    photo=poster,
                    caption=caption[:1024],
                    reply_markup=keyboard,
                    quote=True,
                )
                return
            except RPCError:
                pass
        await message.reply_text(caption, reply_markup=keyboard, quote=True)

    # ------------------------------------------------------------------
    # /start, /help & /dl
    # ------------------------------------------------------------------

    @app.on_message(filters.command(["start", "help"]))
    async def cmd_start(client: Client, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) == 2 and args[1].strip().startswith("dl_"):
            fkey = args[1].strip()[3:]
            if fkey:
                await _send_user_download_card(message, fkey)
                return

        uid = message.from_user.id if message.from_user else None
        role = (
            "Super Admin (Owner)"
            if firebase.is_owner(uid)
            else ("Admin" if firebase.is_admin(uid) else "User")
        )
        if firebase.is_admin(uid):
            await message.reply_text(
                f"🎬 **SPY MOVIE BOTZ — Catalog Bot**\n"
                f"Your Role: **{role}** (`{uid}`)\n\n"
                f"**Admin Commands:**\n"
                f"• Send or forward any video/document file or link to auto-catalog to Firebase\n"
                f"• `/dl <firebase_key>` — Get Direct Download buttons for any cataloged movie\n"
                f"• `/status` — Bot, Firebase, TMDB & Koyeb server health\n"
                f"• `/inspectdb` — Check Firebase nodes, movie count & APK schema types\n"
                f"• `/addadmin <user_id>` (or reply) — Authorize an admin\n"
                f"• `/removeadmin <user_id>` (or reply) — Revoke an admin\n"
                f"• `/admins` or `/adminlist` — List authorized admins",
                quote=True,
            )
            return

        await message.reply_text(
            "🎬 **SPY MOVIE BOTZ**\n"
            "Open any movie link from our channel or APK to receive **Direct Download** and **Watch Online** buttons.",
            quote=True,
        )

    @app.on_message(filters.command(["dl"]))
    async def cmd_direct_download(client: Client, message: Message) -> None:
        args = (message.text or "").split(maxsplit=1)
        if len(args) < 2 or not args[1].strip():
            await message.reply_text("Usage: `/dl <firebase_key>`", quote=True)
            return
        await _send_user_download_card(message, args[1].strip())

    # ------------------------------------------------------------------
    # /inspectdb
    # ------------------------------------------------------------------

    @app.on_message(filters.command(["inspectdb"]))
    async def cmd_inspectdb(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        msg = await message.reply_text("🔎 Inspecting Firebase database schema...", quote=True)
        try:
            info = await firebase.inspect_database()
        except FirebaseError as exc:
            await msg.edit_text(f"❌ Firebase inspection failed: `{exc}`")
            return

        profile = info["profile"]
        nodes_text = ", ".join(f"`/{n}`" for n in info["root_nodes"]) or "None"
        sample_keys = ", ".join(profile.get("sample_keys") or []) or "Empty"

        oldest_lines: list[str] = []
        for k, v in info["oldest"].items():
            oldest_lines.append(
                f"• `{k}`: `{v.get('title')}` (createdAt={type(v.get('createdAt')).__name__}:{v.get('createdAt')})"
            )
        newest_lines: list[str] = []
        for k, v in info["newest"].items():
            newest_lines.append(
                f"• `{k}`: `{v.get('title')}` (createdAt={type(v.get('createdAt')).__name__}:{v.get('createdAt')})"
            )

        await msg.edit_text(
            f"🗄 **Firebase DB Inspection**\n\n"
            f"🌐 **DB URL:** `{settings.firebase_db_url}`\n"
            f"📁 **Root Nodes:** {nodes_text}\n"
            f"🎬 **/movies Count:** `{info['movie_count']}`\n"
            f"🧬 **Detected Types:** `createdAt={profile.get('createdAt_type')}`, "
            f"`year={profile.get('year_type')}`, `rating={profile.get('rating_type')}`\n"
            f"🔑 **Existing Keys:** `{sample_keys}`\n\n"
            f"**Oldest Sample:**\n" + ("\n".join(oldest_lines) or "• None") + "\n\n"
            f"**Newest Samples:**\n" + ("\n".join(newest_lines) or "• None")
        )

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

    @app.on_message(filters.command(["status"]))
    async def cmd_status(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        checking_msg = await message.reply_text("⏳ Checking bot, Firebase, and TMDB status...", quote=True)

        (fb_ok, fb_ms, fb_msg, movie_count), (tmdb_ok, tmdb_ms, tmdb_msg) = await asyncio.gather(
            firebase.health_check(),
            tmdb.health_check(),
        )

        uptime = _format_uptime(time.monotonic() - start_time)
        fb_badge = f"🟢 Connected ({fb_ms}ms)" if fb_ok else f"🔴 Error ({fb_msg})"
        tmdb_badge = f"🟢 Connected ({tmdb_ms}ms)" if tmdb_ok else f"🔴 Error ({tmdb_msg})"

        await checking_msg.edit_text(
            f"📊 **System Status**\n\n"
            f"⏱ **Bot Uptime:** `{uptime}`\n"
            f"🔥 **Firebase RTDB:** {fb_badge}\n"
            f"🎞 **TMDB API:** {tmdb_badge}\n"
            f"👮 **Total Admins:** `{firebase.total_admins_count()}`\n"
            f"🎬 **Movies in Firebase:** `{movie_count}`\n"
            f"🌐 **HTTP Server:** `0.0.0.0:{settings.port}` (Active)\n"
            f"🔗 **Stream Base URL:** `{settings.base_url}`"
        )

    # ------------------------------------------------------------------
    # /addadmin, /removeadmin, /admins, /adminlist
    # ------------------------------------------------------------------

    def _resolve_target_user(message: Message) -> tuple[Optional[int], Optional[str]]:
        if message.reply_to_message and message.reply_to_message.from_user:
            target = message.reply_to_message.from_user
            uname = f"@{target.username}" if target.username else target.first_name
            return target.id, uname

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            return None, None
        arg = parts[1].strip()
        try:
            return int(arg), None
        except ValueError:
            return None, None

    @app.on_message(filters.command(["addadmin"]))
    async def cmd_addadmin(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        target_id, username = _resolve_target_user(message)
        if target_id is None:
            await message.reply_text(
                "Usage: `/addadmin <numeric_user_id>` or reply to a user's message with `/addadmin`.",
                quote=True,
            )
            return

        if target_id == settings.owner_id:
            await message.reply_text("ℹ️ `OWNER_ID` already has permanent super-admin privileges.", quote=True)
            return

        actor_id = message.from_user.id if message.from_user else settings.owner_id
        try:
            await firebase.add_admin(target_id, added_by=actor_id, username=username)
        except FirebaseError as exc:
            await message.reply_text(f"❌ Failed to add admin to Firebase: `{exc}`", quote=True)
            return

        label = f"{target_id} ({username})" if username else str(target_id)
        await message.reply_text(
            f"✅ Admin added: `{label}`\nTotal admins: `{firebase.total_admins_count()}`",
            quote=True,
        )

    @app.on_message(filters.command(["removeadmin"]))
    async def cmd_removeadmin(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        target_id, _ = _resolve_target_user(message)
        if target_id is None:
            await message.reply_text(
                "Usage: `/removeadmin <numeric_user_id>` or reply to a user's message with `/removeadmin`.",
                quote=True,
            )
            return

        if target_id == settings.owner_id:
            await message.reply_text(
                "⛔ Cannot remove `OWNER_ID`. The owner has permanent super-admin access.",
                quote=True,
            )
            return

        try:
            existed = await firebase.remove_admin(target_id)
        except FirebaseError as exc:
            await message.reply_text(f"❌ Failed to remove admin from Firebase: `{exc}`", quote=True)
            return

        if existed:
            await message.reply_text(
                f"🗑 Removed admin `{target_id}`.\nTotal admins: `{firebase.total_admins_count()}`",
                quote=True,
            )
        else:
            await message.reply_text(f"ℹ️ User `{target_id}` was not in `/admins`.", quote=True)

    @app.on_message(filters.command(["admins", "adminlist"]))
    async def cmd_adminlist(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        try:
            admins = await firebase.sync_admins()
        except FirebaseError as exc:
            logger.warning("Failed to refresh admins from Firebase: %s", exc)
            admins = firebase.get_cached_admins()

        lines = [f"👑 **Owner:** `{settings.owner_id}` (Permanent)"]
        extra_ids = sorted(uid for uid in admins if uid != settings.owner_id)
        if extra_ids:
            lines.append("\n👮 **Authorized Admins:**")
            for uid in extra_ids:
                meta = admins.get(uid, {})
                uname = meta.get("username")
                suffix = f" — {uname}" if uname else ""
                lines.append(f"• `{uid}`{suffix}")
        else:
            lines.append("\n_No additional admins added under `/admins`._")

        await message.reply_text("\n".join(lines), quote=True)

    # ------------------------------------------------------------------
    # Media & Link Auto-Indexing
    # ------------------------------------------------------------------

    async def _process_incoming_media(
        *,
        reply_target: Optional[Message],
        media_message: Optional[Message],
        raw_filename: Optional[str],
        raw_caption: Optional[str],
        direct_url: Optional[str] = None,
        silent_channel_mode: bool = False,
    ) -> None:
        _prune_expired_pending()
        parsed = parse_media_metadata(filename=raw_filename, caption=raw_caption)

        status_msg: Optional[Message] = None
        if reply_target and not silent_channel_mode:
            status_msg = await reply_target.reply_text(
                f"🔎 Parsing `{parsed.title}`{f' ({parsed.year})' if parsed.year else ''} & fetching TMDB metadata...",
                quote=True,
            )

        tmdb_meta = await tmdb.lookup(
            parsed.title,
            year=parsed.year,
            prefer_tv=parsed.is_series,
        )

        if media_message is not None:
            file_id, storage_msg_id = await _copy_to_storage(media_message)
        else:
            file_id, storage_msg_id = "", (reply_target.id if reply_target else 0)

        resolved_language = parsed.language or tmdb_meta.original_language or "Hindi"
        resolved_quality = (
            parsed.quality
            or _infer_quality_from_message(media_message, is_series=parsed.is_series)
            or "720p"
        )

        # Immediately push to Firebase so poster, description, rating, and language appear on the APK automatically
        await _finalize_and_push(
            status_msg=status_msg,
            reply_target=reply_target,
            tmdb_meta=tmdb_meta,
            language=resolved_language,
            quality=resolved_quality,
            file_id=file_id,
            message_id=storage_msg_id,
            direct_url=direct_url,
            file_name=raw_filename,
            send_confirmation=not silent_channel_mode,
        )

    @app.on_message((filters.video | filters.document) & ~filters.channel)
    async def handle_media_upload(client: Client, message: Message) -> None:
        if not await _require_admin(message):
            return

        media = message.video or message.document
        file_name = getattr(media, "file_name", None)
        await _process_incoming_media(
            reply_target=message,
            media_message=message,
            raw_filename=file_name,
            raw_caption=message.caption,
        )

    @app.on_message(
        filters.channel
        & filters.chat(settings.storage_channel_id)
        & (filters.video | filters.document)
        & ~filters.outgoing
    )
    async def handle_storage_channel_post(client: Client, message: Message) -> None:
        media = message.video or message.document
        file_name = getattr(media, "file_name", None)
        await _process_incoming_media(
            reply_target=None,
            media_message=message,
            raw_filename=file_name,
            raw_caption=message.caption,
            silent_channel_mode=True,
        )

    @app.on_message(
        filters.text
        & ~filters.command(["start", "help", "dl", "status", "inspectdb", "addadmin", "removeadmin", "admins", "adminlist"])
    )
    async def handle_link_upload(client: Client, message: Message) -> None:
        text = (message.text or "").strip()
        tg_link = extract_telegram_link(text)
        direct_url = extract_direct_url(text) if not tg_link else None

        if not tg_link and not direct_url:
            return

        if not await _require_admin(message):
            return

        if tg_link:
            chat_ref, target_msg_id = tg_link
            try:
                fetched = await client.get_messages(chat_ref, target_msg_id)
            except RPCError as exc:
                await message.reply_text(
                    f"❌ Could not read linked Telegram message (`{chat_ref}/{target_msg_id}`): `{exc}`",
                    quote=True,
                )
                return

            if not fetched or getattr(fetched, "empty", False):
                await message.reply_text("❌ Linked Telegram message is empty or inaccessible.", quote=True)
                return

            media = fetched.video or fetched.document
            if media:
                await _process_incoming_media(
                    reply_target=message,
                    media_message=fetched,
                    raw_filename=getattr(media, "file_name", None),
                    raw_caption=fetched.caption or text,
                )
                return

            await _process_incoming_media(
                reply_target=message,
                media_message=None,
                raw_filename=None,
                raw_caption=fetched.text or fetched.caption or text,
                direct_url=text,
            )
            return

        # Direct external URL with optional title/quality/language caption
        await _process_incoming_media(
            reply_target=message,
            media_message=None,
            raw_filename=None,
            raw_caption=text,
            direct_url=direct_url,
        )

    # ------------------------------------------------------------------
    # Interactive Inline Button Callbacks
    # ------------------------------------------------------------------

    @app.on_callback_query()
    async def handle_callbacks(client: Client, query: CallbackQuery) -> None:
        uid = query.from_user.id if query.from_user else None
        if not firebase.is_admin(uid):
            await query.answer("Access denied: Admins only.", show_alert=True)
            return

        data = query.data or ""
        parts = data.split(":")
        action = parts[0]

        # Pre-save selection flow
        if action in {"sel_lang", "sel_qual", "sel_save", "sel_cancel"} and len(parts) >= 2:
            token = parts[1]
            pending = pending_uploads.get(token)
            if not pending:
                await query.answer("This upload session expired.", show_alert=True)
                return

            if action == "sel_lang" and len(parts) == 3:
                pending.language = parts[2]
                await query.answer(f"Language set to {pending.language}")
            elif action == "sel_qual" and len(parts) == 3:
                pending.quality = parts[2]
                await query.answer(f"Quality set to {pending.quality}")
            elif action == "sel_cancel":
                pending_uploads.pop(token, None)
                await query.answer("Canceled")
                await query.message.edit_text("❌ Upload cataloging canceled.")
                return
            elif action == "sel_save":
                pending_uploads.pop(token, None)
                await query.answer("Saving to Firebase...")
                await _finalize_and_push(
                    status_msg=query.message,
                    reply_target=None,
                    tmdb_meta=pending.tmdb,
                    language=pending.language or "Hindi",
                    quality=pending.quality or "720p",
                    file_id=pending.file_id,
                    message_id=pending.message_id,
                    direct_url=pending.direct_url,
                )
                return

            year_part = f" ({pending.tmdb.year})" if pending.tmdb.year else ""
            await query.message.edit_text(
                f"🎬 **{pending.tmdb.title}{year_part}**\n"
                f"⭐ Rating: `{pending.tmdb.rating}` | 🏷 Category: `{pending.tmdb.category}`\n\n"
                f"Select **Language** and **Quality** below, then tap **Save to Firebase**:\n"
                f"• Current Language: **{pending.language}**\n"
                f"• Current Quality: **{pending.quality}**",
                reply_markup=_build_selection_keyboard(pending),
            )
            return

        # Post-save live Firebase patch/delete callbacks
        if action in {"upd_lang", "upd_qual"} and len(parts) == 3:
            firebase_key, new_value = parts[1], parts[2]
            existing = await firebase.get_movie(firebase_key)
            if not existing:
                await query.answer("Movie record not found in Firebase.", show_alert=True)
                return

            patch: dict[str, Any] = {}
            current_lang = str(existing.get("language") or "Hindi")
            current_qual = str(existing.get("quality") or "720p")
            existing_desc = str(existing.get("description") or "")

            if action == "upd_lang":
                current_lang = new_value
                updated_badge = format_apk_badge(current_lang, current_qual)
                updated_desc = format_apk_description(existing_desc, current_lang, current_qual)
                patch = {
                    "language": current_lang,
                    "qualityBadge": updated_badge,
                    "description": updated_desc,
                }
            else:
                current_qual = new_value
                updated_badge = format_apk_badge(current_lang, current_qual)
                updated_desc = format_apk_description(existing_desc, current_lang, current_qual)
                watch_url = existing.get("watchLink", "")
                download_url = existing.get("downloadLink", "")
                patch = {
                    "quality": current_qual,
                    "qualityBadge": updated_badge,
                    "description": updated_desc,
                    "watch480": watch_url if current_qual == "480p" else "",
                    "watch780": watch_url if current_qual == "720p" else "",
                    "watch1080": watch_url if current_qual in ("1080p", "4K") else "",
                    "download480": download_url if current_qual == "480p" else "",
                    "download780": download_url if current_qual == "720p" else "",
                    "download1080": download_url if current_qual in ("1080p", "4K") else "",
                }
                if "download720" in existing:
                    patch["download720"] = download_url if current_qual == "720p" else ""
                if "watch720" in existing:
                    patch["watch720"] = watch_url if current_qual == "720p" else ""

            if "cardBadge" in existing:
                patch["cardBadge"] = updated_badge
            if "badge" in existing:
                patch["badge"] = updated_badge
            if "overview" in existing:
                patch["overview"] = updated_desc
            if "plot" in existing:
                patch["plot"] = updated_desc

            try:
                await firebase.update_movie(firebase_key, patch)
            except FirebaseError as exc:
                await query.answer(f"Update failed: {exc}", show_alert=True)
                return

            updated = {**existing, **patch}
            await query.answer(f"Updated to {new_value}")
            new_text = _format_confirmation_text(updated, firebase_key, True)
            new_markup = _build_post_save_keyboard(
                firebase_key,
                updated.get("watchLink", ""),
                updated.get("downloadLink", ""),
            )
            if query.message.photo:
                await query.message.edit_caption(caption=new_text[:1024], reply_markup=new_markup)
            else:
                await query.message.edit_text(
                    new_text,
                    reply_markup=new_markup,
                    disable_web_page_preview=False,
                )
            return

        if action == "del_entry" and len(parts) == 2:
            firebase_key = parts[1]
            try:
                await firebase.delete_movie(firebase_key)
            except FirebaseError as exc:
                await query.answer(f"Delete failed: {exc}", show_alert=True)
                return
            await query.answer("Deleted from Firebase")
            deleted_msg = f"🗑 Deleted Firebase record `{firebase_key}`."
            if query.message.photo:
                await query.message.edit_caption(caption=deleted_msg)
            else:
                await query.message.edit_text(deleted_msg)
