import mainPy from '../../telegram-movie-bot/main.py?raw';
import configPy from '../../telegram-movie-bot/bot/config.py?raw';
import parserPy from '../../telegram-movie-bot/bot/parser.py?raw';
import tmdbClientPy from '../../telegram-movie-bot/bot/tmdb_client.py?raw';
import firebaseClientPy from '../../telegram-movie-bot/bot/firebase_client.py?raw';
import webServerPy from '../../telegram-movie-bot/bot/web_server.py?raw';
import streamerPy from '../../telegram-movie-bot/bot/streamer.py?raw';
import handlersPy from '../../telegram-movie-bot/bot/handlers.py?raw';
import linkGeneratorPy from '../../telegram-movie-bot/bot/link_generator.py?raw';
import initPy from '../../telegram-movie-bot/bot/__init__.py?raw';
import testBotPy from '../../telegram-movie-bot/tests/test_bot.py?raw';
import requirementsTxt from '../../telegram-movie-bot/requirements.txt?raw';
import procfile from '../../telegram-movie-bot/Procfile?raw';
import runtimeTxt from '../../telegram-movie-bot/runtime.txt?raw';
import envExample from '../../telegram-movie-bot/.env.example?raw';
import readmeMd from '../../telegram-movie-bot/README.md?raw';
import dockerfile from '../../telegram-movie-bot/Dockerfile?raw';

export interface BotFileEntry {
  path: string;
  name: string;
  folder: string;
  language: string;
  summary: string;
  content: string;
}

export const BOT_FILES: BotFileEntry[] = [
  {
    path: 'main.py',
    name: 'main.py',
    folder: 'root',
    language: 'python',
    summary: 'Async entrypoint, signal handling, and graceful shutdown',
    content: mainPy,
  },
  {
    path: 'bot/config.py',
    name: 'config.py',
    folder: 'bot',
    language: 'python',
    summary: 'Strict environment loader and Koyeb domain auto-detection',
    content: configPy,
  },
  {
    path: 'bot/parser.py',
    name: 'parser.py',
    folder: 'bot',
    language: 'python',
    summary: 'Filename, caption, Telegram link, and URL metadata parser',
    content: parserPy,
  },
  {
    path: 'bot/tmdb_client.py',
    name: 'tmdb_client.py',
    folder: 'bot',
    language: 'python',
    summary: 'Async TMDB v3 movie + TV fallback client with safe poster fallback',
    content: tmdbClientPy,
  },
  {
    path: 'bot/firebase_client.py',
    name: 'firebase_client.py',
    folder: 'bot',
    language: 'python',
    summary: 'Async Firebase RTDB REST client for /movies and synced /admins',
    content: firebaseClientPy,
  },
  {
    path: 'bot/link_generator.py',
    name: 'link_generator.py',
    folder: 'bot',
    language: 'python',
    summary: 'HMAC-signed speed-optimized Direct Download link generator',
    content: linkGeneratorPy,
  },
  {
    path: 'bot/web_server.py',
    name: 'web_server.py',
    folder: 'bot',
    language: 'python',
    summary: 'Koyeb aiohttp health endpoints, branded player, and parallel range streamer',
    content: webServerPy,
  },
  {
    path: 'bot/streamer.py',
    name: 'streamer.py',
    folder: 'bot',
    language: 'python',
    summary: 'High-speed parallel Telegram MTProto ByteStreamer with persistent DC sessions',
    content: streamerPy,
  },
  {
    path: 'bot/handlers.py',
    name: 'handlers.py',
    folder: 'bot',
    language: 'python',
    summary: 'Pyrogram commands, auto-cataloging, and inline selection buttons',
    content: handlersPy,
  },
  {
    path: 'bot/__init__.py',
    name: '__init__.py',
    folder: 'bot',
    language: 'python',
    summary: 'Package marker',
    content: initPy,
  },
  {
    path: 'tests/test_bot.py',
    name: 'test_bot.py',
    folder: 'tests',
    language: 'python',
    summary: 'Async unit tests for parser, config, TMDB, and Firebase',
    content: testBotPy,
  },
  {
    path: 'requirements.txt',
    name: 'requirements.txt',
    folder: 'root',
    language: 'text',
    summary: 'Pyrofork, TgCrypto, aiohttp, and python-dotenv dependencies',
    content: requirementsTxt,
  },
  {
    path: 'Procfile',
    name: 'Procfile',
    folder: 'root',
    language: 'text',
    summary: 'Koyeb web process start command',
    content: procfile,
  },
  {
    path: 'runtime.txt',
    name: 'runtime.txt',
    folder: 'root',
    language: 'text',
    summary: 'Python 3.11.9 runtime specification',
    content: runtimeTxt,
  },
  {
    path: '.env.example',
    name: '.env.example',
    folder: 'root',
    language: 'env',
    summary: 'Required environment variable template',
    content: envExample,
  },
  {
    path: 'Dockerfile',
    name: 'Dockerfile',
    folder: 'root',
    language: 'dockerfile',
    summary: 'Python 3.11 container build for Koyeb deployment',
    content: dockerfile,
  },
  {
    path: 'README.md',
    name: 'README.md',
    folder: 'root',
    language: 'markdown',
    summary: 'Setup and command documentation',
    content: readmeMd,
  },
];
