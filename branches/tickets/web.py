"""Transcript web server — serves saved HTML transcripts over HTTP."""

import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)


class TranscriptServer:
    """Lightweight aiohttp server that serves transcript HTML files."""

    def __init__(self, port: int, base_url: str, transcripts_dir: Path, server_logger: logging.Logger):
        self.port = port
        self.base_url = base_url.rstrip("/")
        self.transcripts_dir = transcripts_dir
        self.log = server_logger
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Create the aiohttp app and start listening."""
        app = web.Application()
        app.router.add_get("/transcripts/{filename}", self._handle_transcript)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()

        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()
        self.log.info(f"Transcript web server listening on port {self.port}")

    async def stop(self) -> None:
        """Gracefully shut down the server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self.log.info("Transcript web server stopped")

    async def _handle_transcript(self, request: web.Request) -> web.Response:
        """Serve a transcript HTML file."""
        filename = request.match_info["filename"]

        # Reject path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            raise web.HTTPForbidden()

        filepath = self.transcripts_dir / filename
        if not filepath.is_file():
            raise web.HTTPNotFound()

        return web.FileResponse(filepath, headers={"Content-Type": "text/html; charset=utf-8"})

    def transcript_url(self, filename: str) -> str:
        """Build the public URL for a transcript file."""
        return f"{self.base_url}/transcripts/{filename}"


async def save_transcript(transcripts_dir: Path, filename: str, buffer) -> None:
    """Write a BytesIO transcript buffer to disk.

    Args:
        transcripts_dir: Directory to save into.
        filename: File name (e.g. ``ticket-general-42.html``).
        buffer: BytesIO buffer with HTML content.
    """
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    filepath = transcripts_dir / filename
    buffer.seek(0)
    filepath.write_bytes(buffer.read())
    logger.info(f"Saved transcript to {filepath}")
