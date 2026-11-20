"""Speed-optimized signed Direct Download and streaming link generator."""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_download_filename(raw_name: Optional[str], message_id: int) -> str:
    """Create a clean, URL-safe filename preserving the media extension for download managers."""
    candidate = (raw_name or "").strip().replace(" ", ".")
    candidate = _UNSAFE_FILENAME_RE.sub(".", candidate).strip("._-")
    candidate = re.sub(r"\.{2,}", ".", candidate)
    if not candidate:
        return f"SpyMovieBotz_{message_id}.mp4"
    if "." not in candidate:
        return f"{candidate}.mp4"
    return candidate[:120]


@dataclass(frozen=True, slots=True)
class SpeedLinks:
    watch_url: str
    stream_url: str
    direct_download_url: str
    signature: str
    safe_filename: str


class SpeedLinkGenerator:
    """Generates HMAC-signed, filename-preserving direct download links."""

    def __init__(self, *, bot_token: str, api_hash: str, base_url: str) -> None:
        secret_material = f"{bot_token}:{api_hash}".encode("utf-8")
        self._secret_key = hashlib.sha256(secret_material).digest()
        self._base_url = base_url.rstrip("/")

    def sign(self, message_id: int) -> str:
        """Compute a compact 16-char URL-safe HMAC signature for `message_id`."""
        payload = f"dl:{message_id}".encode("utf-8")
        digest = hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()
        return digest[:16]

    def verify(self, message_id: int, signature: str) -> bool:
        """Constant-time verification of a direct download signature."""
        if not signature or len(signature) != 16:
            return False
        expected = self.sign(message_id)
        return hmac.compare_digest(expected, signature)

    def generate(
        self,
        *,
        message_id: int,
        file_name: Optional[str] = None,
        direct_url: Optional[str] = None,
    ) -> SpeedLinks:
        """Generate watch, stream, and speed-optimized direct download URLs."""
        safe_name = sanitize_download_filename(file_name, message_id)
        sig = self.sign(message_id)

        if direct_url:
            return SpeedLinks(
                watch_url=direct_url,
                stream_url=direct_url,
                direct_download_url=direct_url,
                signature=sig,
                safe_filename=safe_name,
            )

        encoded_name = quote(safe_name, safe="._-")
        return SpeedLinks(
            watch_url=f"{self._base_url}/watch/{message_id}",
            stream_url=f"{self._base_url}/stream/{message_id}",
            direct_download_url=f"{self._base_url}/dl/{sig}/{message_id}/{encoded_name}",
            signature=sig,
            safe_filename=safe_name,
        )
