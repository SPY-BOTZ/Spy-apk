"""Main entrypoint for the Telegram Movie Catalog Bot and Koyeb HTTP server."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

from pyrogram import Client

from bot.config import ConfigError, Settings
from bot.firebase_client import FirebaseClient
from bot.handlers import register_handlers
from bot.link_generator import SpeedLinkGenerator
from bot.tmdb_client import TmdbClient
from bot.web_server import BotWebServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("movie_catalog_bot")
logging.getLogger("pyrogram").setLevel(logging.WARNING)


async def run_bot() -> None:
    start_time = time.monotonic()

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    firebase = FirebaseClient(
        db_url=settings.firebase_db_url,
        database_secret=settings.firebase_database_secret,
        owner_id=settings.owner_id,
    )
    tmdb = TmdbClient(
        api_key=settings.tmdb_api_key,
        fallback_poster_url=settings.fallback_poster_url,
    )
    link_generator = SpeedLinkGenerator(
        bot_token=settings.bot_token,
        api_hash=settings.api_hash,
        base_url=settings.base_url,
    )
    web_server = BotWebServer(
        port=settings.port,
        storage_channel_id=settings.storage_channel_id,
        start_time=start_time,
        link_generator=link_generator,
    )

    # Start HTTP server immediately so Koyeb health checks pass during Telegram handshake
    await web_server.start()

    try:
        await firebase.sync_admins()
    except Exception as exc:
        logger.warning("Initial Firebase admin sync failed (will retry on demand): %s", exc)

    tg_client = Client(
        name="movie_catalog_bot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        in_memory=True,
        workers=16,
        max_concurrent_transmissions=8,
    )
    register_handlers(
        app=tg_client,
        settings=settings,
        firebase=firebase,
        tmdb=tmdb,
        start_time=start_time,
        link_generator=link_generator,
    )
    web_server.attach_telegram_client(tg_client)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(sig_name: str) -> None:
        logger.info("Received %s, initiating graceful shutdown...", sig_name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig.name)
        except NotImplementedError:
            # Windows fallback
            signal.signal(sig, lambda *_: stop_event.set())

    try:
        await tg_client.start()
        me = await tg_client.get_me()
        logger.info("Telegram bot started as @%s (id=%s)", me.username, me.id)
        await stop_event.wait()
    finally:
        logger.info("Shutting down services...")
        try:
            if getattr(tg_client, "is_connected", False):
                await tg_client.stop()
        except Exception as exc:
            logger.warning("Error stopping Telegram client: %s", exc)

        await asyncio.gather(
            web_server.stop(),
            firebase.close(),
            tmdb.close(),
            return_exceptions=True,
        )
        logger.info("Shutdown complete.")


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
