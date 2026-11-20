"""Pyrogram bot command handlers, auto-indexer, and interactive selection flows."""

from __future__ import annotations

import asyncio
import logging
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


def _quality_badge(quality: str) -> str:
    return {
        "480p": "SD",
        "720p": "HD",
        "1080p": "FHD",
        "4K": "4K",
    }.get(quality, "HD")


def build_movie_record(
    *,
    tmdb: TmdbMetadata,
    language: str,
    quality: str,
    file_id: str,
    message_id: int,
    base_url: str,
    direct_url: Optional[str] = None,
) -> dict[str, Any]:
    """Build the Firebase `/movies` schema record."""
    if direct_url:
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

    return {
        "title": tmdb.title,
        "year": tmdb.year,
        "posterUrl": tmdb.poster_url,
        "category": tmdb.category,
        "rating": tmdb.rating,
        "qualityBadge": _quality_badge(quality),
        "language": language,
        "quality": quality,
        "description": tmdb.description,
        "watch480": watch480,
        "watch780": watch780,
        "watch1080": watch1080,
        "watchLink": watch_url,
        "download480": download480,
        "download780": download780,
        "download1080": download1080,
        "downloadLink": download_url,
        "fileId": file_id,
        "messageId": message_id,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


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


def _infer_quality_from_message(msg: Optional[Message]) -> Optional[str]:
    """Detect quality from Telegram video height/width metadata when filename lacks resolution."""
    if not msg:
        return None
    video = getattr(msg, "video", None)
    if not video:
        return None
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
    return None


def _build_post_save_keyboard(firebase_key: str, watch_url: str) -> InlineKeyboardMarkup:
    lang_buttons = [
        InlineKeyboardButton(lang, callback_data=f"upd_lang:{firebase_key}:{lang}")
        for lang in SUPPORTED_LANGUAGES
    ]
    qual_row = [
        InlineKeyboardButton(qual, callback_data=f"upd_qual:{firebase_key}:{qual}")
        for qual in SUPPORTED_QUALITIES
    ]
    action_row = [
        InlineKeyboardButton("🗑 Delete Entry", callback_data=f"del_entry:{firebase_key}"),
    ]
    if watch_url.startswith(("http://", "https://")) and "localhost" not in watch_url:
        action_row.insert(0, InlineKeyboardButton("▶ Watch Link", url=watch_url))
    return InlineKeyboardMarkup([lang_buttons[:3], lang_buttons[3:], qual_row, action_row])


def _format_confirmation_text(
    record: dict[str, Any],
    firebase_key: str,
    tmdb_matched: bool,
) -> str:
    year_part = f" ({record['year']})" if record["year"] else ""
    tmdb_note = "Matched via TMDB" if tmdb_matched else "Fallback metadata (TMDB no match)"
    desc = str(record.get("description", ""))
    if len(desc) > 180:
        desc = f"{desc[:177]}..."

    return (
        f"✅ **Cataloged to Firebase**\n\n"
        f"🎬 **Title:** {record['title']}{year_part}\n"
        f"🏷 **Category:** {record['category']} | ⭐ **Rating:** {record['rating']}\n"
        f"🌐 **Language:** {record['language']} | 🎞 **Quality:** {record['quality']} ({record['qualityBadge']})\n"
        f"🖼 **Poster:** {record['posterUrl']}\n"
        f"📝 **Plot:** {desc}\n"
        f"🔑 **Firebase Key:** `{firebase_key}`\n"
        f"📦 **Message ID:** `{record['messageId']}`\n"
        f"📡 **Metadata:** {tmdb_note}\n\n"
        f"_Tap a Language or Quality button below to adjust this entry anytime._"
    )


def register_handlers(
    app: Client,
    settings: Settings,
    firebase: FirebaseClient,
    tmdb: TmdbClient,
    start_time: float,
) -> None:
    """Attach all command, upload, and callback handlers to the Pyrogram client."""

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
        send_confirmation: bool = True,
    ) -> None:
        if tmdb_meta.description == "No description available.":
            year_suffix = f" ({tmdb_meta.year})" if tmdb_meta.year else ""
            tmdb_meta = TmdbMetadata(
                title=tmdb_meta.title,
                year=tmdb_meta.year,
                poster_url=tmdb_meta.poster_url,
                rating=tmdb_meta.rating,
                description=f"{tmdb_meta.title}{year_suffix} — {language} ({quality})",
                category=tmdb_meta.category,
                original_language=tmdb_meta.original_language,
                tmdb_id=tmdb_meta.tmdb_id,
                media_type=tmdb_meta.media_type,
                matched=tmdb_meta.matched,
            )

        record = build_movie_record(
            tmdb=tmdb_meta,
            language=language,
            quality=quality,
            file_id=file_id,
            message_id=message_id,
            base_url=settings.base_url,
            direct_url=direct_url,
        )
        try:
            firebase_key = await firebase.push_movie(record)
        except FirebaseError as exc:
            logger.error("Firebase push failed: %s", exc)
            if status_msg:
                await status_msg.edit_text(f"❌ **Firebase Upload Failed**\n`{exc}`")
            return

        if not send_confirmation:
            logger.info("Auto-cataloged %r -> Firebase key %s", record["title"], firebase_key)
            return

        text = _format_confirmation_text(record, firebase_key, tmdb_meta.matched)
        keyboard = _build_post_save_keyboard(firebase_key, record["watchLink"])

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

    # ------------------------------------------------------------------
    # /start & /help
    # ------------------------------------------------------------------

    @app.on_message(filters.command(["start", "help"]))
    async def cmd_start(client: Client, message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        role = (
            "Super Admin (Owner)"
            if firebase.is_owner(uid)
            else ("Admin" if firebase.is_admin(uid) else "Unauthorized")
        )
        await message.reply_text(
            f"🎬 **Movie & File Catalog Bot**\n"
            f"Your Role: **{role}** (`{uid}`)\n\n"
            f"**Admin Commands:**\n"
            f"• Send or forward any video/document file or link to auto-catalog to Firebase\n"
            f"• `/status` — Bot, Firebase, TMDB & Koyeb server health\n"
            f"• `/addadmin <user_id>` (or reply) — Authorize an admin\n"
            f"• `/removeadmin <user_id>` (or reply) — Revoke an admin\n"
            f"• `/admins` or `/adminlist` — List authorized admins",
            quote=True,
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
            or _infer_quality_from_message(media_message)
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
        & ~filters.command(["start", "help", "status", "addadmin", "removeadmin", "admins", "adminlist"])
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
            if action == "upd_lang":
                patch["language"] = new_value
            else:
                watch_url = existing.get("watchLink", "")
                download_url = existing.get("downloadLink", "")
                patch = {
                    "quality": new_value,
                    "qualityBadge": _quality_badge(new_value),
                    "watch480": watch_url if new_value == "480p" else "",
                    "watch780": watch_url if new_value == "720p" else "",
                    "watch1080": watch_url if new_value in ("1080p", "4K") else "",
                    "download480": download_url if new_value == "480p" else "",
                    "download780": download_url if new_value == "720p" else "",
                    "download1080": download_url if new_value in ("1080p", "4K") else "",
                }

            try:
                await firebase.update_movie(firebase_key, patch)
            except FirebaseError as exc:
                await query.answer(f"Update failed: {exc}", show_alert=True)
                return

            updated = {**existing, **patch}
            await query.answer(f"Updated to {new_value}")
            new_text = _format_confirmation_text(updated, firebase_key, True)
            new_markup = _build_post_save_keyboard(firebase_key, updated.get("watchLink", ""))
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
