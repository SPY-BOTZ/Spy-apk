"""Unit tests for parser, config, TMDB fallback, Firebase REST logic, and schema builder."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from bot.config import ConfigError, Settings
from bot.firebase_client import FirebaseClient
from bot.link_generator import SpeedLinkGenerator, sanitize_download_filename
from bot.parser import extract_direct_url, extract_telegram_link, parse_media_metadata
from bot.tmdb_client import TmdbClient, TmdbMetadata


class ParserTests(unittest.TestCase):
    def test_movie_filename_full_extraction(self) -> None:
        parsed = parse_media_metadata(
            filename="Inception.2010.1080p.BluRay.Dual.Audio.Hindi.Eng.x264-YIFY.mkv"
        )
        self.assertEqual(parsed.title, "Inception")
        self.assertEqual(parsed.year, "2010")
        self.assertEqual(parsed.quality, "1080p")
        self.assertEqual(parsed.language, "Multi")
        self.assertEqual(parsed.quality_badge, "FHD")
        self.assertFalse(parsed.is_series)
        self.assertFalse(parsed.needs_selection)

    def test_regional_movie_missing_tags_triggers_selection(self) -> None:
        parsed = parse_media_metadata(filename="Jawan (2023) HDRip.mp4")
        self.assertEqual(parsed.title, "Jawan")
        self.assertEqual(parsed.year, "2023")
        self.assertIsNone(parsed.language)
        self.assertIsNone(parsed.quality)
        self.assertTrue(parsed.needs_selection)

    def test_series_detection_and_4k_normalization(self) -> None:
        parsed = parse_media_metadata(
            filename="Mirzapur.S03E01.2024.2160p.WEB-DL.Hindi.HEVC.mkv"
        )
        self.assertEqual(parsed.title, "Mirzapur")
        self.assertEqual(parsed.year, "2024")
        self.assertEqual(parsed.quality, "4K")
        self.assertEqual(parsed.language, "Hindi")
        self.assertTrue(parsed.is_series)

    def test_telegram_and_direct_link_extractors(self) -> None:
        self.assertEqual(
            extract_telegram_link("https://t.me/c/1987654321/45"),
            (-1001987654321, 45),
        )
        self.assertEqual(
            extract_telegram_link("https://t.me/movies_archive/102"),
            ("movies_archive", 102),
        )
        self.assertEqual(
            extract_direct_url("Dune Part Two 2024 1080p English https://cdn.example.com/dune2.mp4"),
            "https://cdn.example.com/dune2.mp4",
        )


class ConfigTests(unittest.TestCase):
    def test_missing_env_raises_config_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch("bot.config.load_dotenv"):
                with self.assertRaises(ConfigError):
                    Settings.from_env()

    def test_valid_env_loads_with_koyeb_domain(self) -> None:
        env = {
            "API_ID": "12345",
            "API_HASH": "hash_abc",
            "BOT_TOKEN": "123:token",
            "OWNER_ID": "999999",
            "STORAGE_CHANNEL_ID": "-100111222333",
            "TMDB_API_KEY": "tmdb_key",
            "FIREBASE_DB_URL": "https://demo-default-rtdb.firebaseio.com/",
            "FIREBASE_DATABASE_SECRET": "secret_123",
            "PORT": "8080",
            "KOYEB_PUBLIC_DOMAIN": "my-bot.koyeb.app",
        }
        with patch.dict(os.environ, env, clear=True):
            with patch("bot.config.load_dotenv"):
                s = Settings.from_env()
                self.assertEqual(s.port, 8080)
                self.assertEqual(s.base_url, "https://my-bot.koyeb.app")
                self.assertEqual(s.firebase_db_url, "https://demo-default-rtdb.firebaseio.com")


class AsyncClientsTests(unittest.IsolatedAsyncioTestCase):
    async def test_tmdb_movie_then_tv_fallback_and_poster_fallback(self) -> None:
        client = TmdbClient(
            api_key="test",
            fallback_poster_url="https://fallback.local/poster.png",
        )

        async def fake_search(endpoint: str, query: str, year=None):
            if endpoint == "movie":
                return []
            return [
                {
                    "id": 777,
                    "name": "Dark",
                    "first_air_date": "2017-12-01",
                    "poster_path": None,
                    "vote_average": 8.43,
                    "overview": "A family saga with a supernatural twist.",
                    "genre_ids": [10765],
                }
            ]

        client._search = fake_search  # type: ignore[method-assign]
        meta = await client.lookup("Dark", year="2017", prefer_tv=False)
        self.assertTrue(meta.matched)
        self.assertEqual(meta.title, "Dark")
        self.assertEqual(meta.year, "2017")
        self.assertEqual(meta.poster_url, "https://fallback.local/poster.png")
        self.assertEqual(meta.rating, 8.4)
        self.assertEqual(meta.category, "Sci-Fi & Fantasy")

    async def test_firebase_multi_admin_sync_and_owner_protection(self) -> None:
        fb = FirebaseClient(
            db_url="https://demo.firebaseio.com",
            database_secret="sec",
            owner_id=1001,
        )
        fb._request = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {"2002": {"userId": 2002, "username": "@alice"}},  # sync_admins
                {"userId": 3003},  # add_admin PUT
                None,  # remove_admin DELETE
            ]
        )

        await fb.sync_admins()
        self.assertTrue(fb.is_admin(1001))
        self.assertTrue(fb.is_admin(2002))
        self.assertFalse(fb.is_admin(3003))

        await fb.add_admin(3003, added_by=1001, username="@bob")
        self.assertTrue(fb.is_admin(3003))
        self.assertEqual(fb.total_admins_count(), 3)

        with self.assertRaises(ValueError):
            await fb.remove_admin(1001)

        removed = await fb.remove_admin(2002)
        self.assertTrue(removed)
        self.assertFalse(fb.is_admin(2002))


class LinkGeneratorTests(unittest.TestCase):
    def test_signed_direct_download_link_and_verification(self) -> None:
        gen = SpeedLinkGenerator(
            bot_token="123:token",
            api_hash="hash_xyz",
            base_url="https://bot.koyeb.app",
        )
        links = gen.generate(message_id=151, file_name="Black Doves S01E01 1080p.mkv")
        self.assertEqual(links.watch_url, "https://bot.koyeb.app/watch/151")
        self.assertTrue(links.direct_download_url.startswith(f"https://bot.koyeb.app/dl/{links.signature}/151/"))
        self.assertTrue(links.direct_download_url.endswith("Black.Doves.S01E01.1080p.mkv"))
        self.assertTrue(gen.verify(151, links.signature))
        self.assertFalse(gen.verify(152, links.signature))
        self.assertFalse(gen.verify(151, "tampered00000000"))
        self.assertEqual(sanitize_download_filename("no_ext", 9), "no_ext.mp4")


class ApkLanguageFormattingTests(unittest.TestCase):
    def test_badge_and_description_prefix_without_duplication(self) -> None:
        from bot.handlers import format_apk_badge, format_apk_description

        self.assertEqual(format_apk_badge("Multi", "480p"), "MULTI • SD")
        desc1 = format_apk_description("After his father is murdered...", "Multi", "480p")
        self.assertTrue(desc1.startswith("🌐 Language: Multi | 🎞 Quality: 480p (SD)\n\n"))

        desc2 = format_apk_description(desc1, "Hindi", "720p")
        self.assertEqual(
            desc2,
            "🌐 Language: Hindi | 🎞 Quality: 720p (HD)\n\nAfter his father is murdered...",
        )
        self.assertEqual(
            format_apk_description("No description available.", "Hindi", "720p"),
            "🌐 Language: Hindi | 🎞 Quality: 720p (HD)",
        )


if __name__ == "__main__":
    unittest.main()
