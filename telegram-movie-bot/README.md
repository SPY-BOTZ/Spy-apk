# Telegram Movie & File Catalog Bot

Production-ready async Python Telegram bot (Pyrofork + aiohttp) that automatically indexes movies and media files into Firebase Realtime Database (`/movies`), enriches metadata via TMDB API v3, manages multi-admin access (`/admins`), and exposes a Koyeb-compatible HTTP health & byte-range streaming server.

## Project Layout

```text
telegram-movie-bot/
├── main.py                  # Async entrypoint & graceful shutdown
├── requirements.txt         # Python dependencies
├── Procfile                 # Koyeb/buildpack start command
├── runtime.txt              # Python 3.11.9 runtime pin
├── .env.example             # Environment variable template
├── bot/
│   ├── __init__.py
│   ├── config.py            # Strict environment validation
│   ├── parser.py            # Filename/caption/link metadata extractor
│   ├── tmdb_client.py       # Async TMDB movie + TV fallback client
│   ├── firebase_client.py   # Async Firebase RTDB REST client (/movies & /admins)
│   ├── web_server.py        # aiohttp Koyeb health (/health) & range streamer
│   └── handlers.py          # Pyrogram command, upload & inline button handlers
└── tests/
    └── test_bot.py          # Unit tests for parser, config, TMDB, Firebase & schema
```

## Required Environment Variables

| Variable | Description | Example |
|---|---|---|
| `API_ID` | Telegram API ID from `https://my.telegram.org` | `12345678` |
| `API_HASH` | Telegram API Hash from `https://my.telegram.org` | `0123456789abcdef...` |
| `BOT_TOKEN` | Bot token from `@BotFather` | `123456:ABC-DEF...` |
| `OWNER_ID` | Numeric Telegram User ID of permanent super-admin | `987654321` |
| `STORAGE_CHANNEL_ID` | Channel ID where bot is admin for file storage | `-1001234567890` |
| `TMDB_API_KEY` | TMDB v3 API key | `a1b2c3d4...` |
| `FIREBASE_DB_URL` | Firebase Realtime Database root URL | `https://my-app-default-rtdb.firebaseio.com` |
| `FIREBASE_DATABASE_SECRET` | Firebase RTDB legacy/database secret | `your_secret` |
| `PORT` | HTTP server port (injected by Koyeb, default `8000`) | `8000` |
| `BASE_URL` | Optional public HTTPS URL for watch/download links | `https://my-bot.koyeb.app` |

## Commands

- `/start`, `/help` — Show role status and command guide
- `/status` — Show uptime, Firebase status & movie count, TMDB status, admin count, and HTTP port
- `/addadmin <user_id>` (or reply to a user's message) — Add admin to Firebase `/admins`
- `/removeadmin <user_id>` (or reply to a user's message) — Remove admin from `/admins` (`OWNER_ID` is protected)
- `/admins`, `/adminlist` — List owner and all synced admins
