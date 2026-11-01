"""Gzip the dashboard's sizeable text responses without touching streams."""
from __future__ import annotations

import re
import zlib
from collections.abc import Awaitable, Callable

ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]
Send = Callable[[dict], Awaitable[None]]

MINIMUM_SIZE = 1024
BUFFER_LIMIT = 2 * 1024 * 1024     # a chunked text file up to this is gathered whole
TEXT_TYPES = frozenset({
    "text/html",
    "text/css",
    "text/plain",
    "application/javascript",
    "text/javascript",
    "image/svg+xml",
    "application/manifest+json",
    "application/json",
})
NO_BODY_STATUSES = frozenset({204, 205, 206, 304})
_QUALITY = re.compile(r"(?:^|;)\s*q\s*=\s*([0-9.]+)\s*(?:;|$)", re.I)


def _header_values(headers: list[tuple[bytes, bytes]], name: str) -> list[str]:
    wanted = name.encode("latin-1")
    return [value.decode("latin-1") for key, value in headers if key.lower() == wanted]


def accepts_gzip(headers: list[tuple[bytes, bytes]]) -> bool:
    """Whether an ASGI request explicitly permits gzip."""
    for value in _header_values(headers, "accept-encoding"):
        for part in value.split(","):
            coding, _, parameters = part.strip().partition(";")
            if coding.lower() != "gzip":
                continue
            quality = _QUALITY.search(";" + parameters)
            if quality is None:
                return True
            try:
                return float(quality.group(1)) > 0
            except ValueError:
                return False
    return False


def _content_type(headers: list[tuple[bytes, bytes]]) -> str:
    values = _header_values(headers, "content-type")
    return values[-1].split(";", 1)[0].strip().lower() if values else ""


def _has_attachment(headers: list[tuple[bytes, bytes]]) -> bool:
    return any(value.lower().lstrip().startswith("attachment")
               for value in _header_values(headers, "content-disposition"))


def _can_compress(start: dict, body: bytes, minimum_size: int) -> bool:
    headers = start.get("headers", [])
    status = start.get("status", 200)
    return (
        status >= 200
        and status not in NO_BODY_STATUSES
        and len(body) >= minimum_size
        and _content_type(headers) in TEXT_TYPES
        and not _header_values(headers, "content-encoding")
        and not _header_values(headers, "content-range")
        and not _has_attachment(headers)
        and not any("no-transform" in value.lower()
                    for value in _header_values(headers, "cache-control"))
    )


def _declared_length(headers: list[tuple[bytes, bytes]]) -> int | None:
    values = _header_values(headers, "content-length")
    try:
        return int(values[-1]) if values else None
    except ValueError:
        return None


def _gatherable(start: dict, minimum_size: int) -> bool:
    """A file sent in chunks (static JS, CSS) with a known, bounded length.
    A stream has no Content-Length and is never gathered."""
    length = _declared_length(start.get("headers", []))
    return (length is not None and minimum_size <= length <= BUFFER_LIMIT
            and _can_compress(start, b"x" * minimum_size, minimum_size))


def _compressed_start(start: dict, length: int) -> dict:
    headers = [(key, value) for key, value in start.get("headers", [])
               if key.lower() not in {b"content-length", b"content-encoding"}]
    if not any("accept-encoding" in {item.strip().lower() for item in value.decode("latin-1").split(",")}
               for key, value in headers if key.lower() == b"vary"):
        headers.append((b"vary", b"Accept-Encoding"))
    headers.extend(((b"content-encoding", b"gzip"),
                    (b"content-length", str(length).encode("ascii"))))
    result = dict(start)
    result["headers"] = headers
    return result


class GzipTextMiddleware:
    """A small ASGI middleware for finite, whitelisted text responses.

    It decides on the whole body. A file sent in chunks with a declared length
    of at most BUFFER_LIMIT (static JS and CSS) is gathered first; chunks with
    no length are a stream and pass through unchanged.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = MINIMUM_SIZE, level: int = 5):
        self.app = app
        self.minimum_size = minimum_size
        self.level = level

    async def __call__(self, scope: dict, receive: Callable, send: Send) -> None:
        if scope["type"] != "http" or not accepts_gzip(scope.get("headers", [])):
            await self.app(scope, receive, send)
            return

        start: dict | None = None
        gathered: list[bytes] | None = None

        async def wrapped(message: dict) -> None:
            nonlocal start, gathered
            if message["type"] == "http.response.start":
                start = message
                return
            if start is None:
                await send(message)
                return
            if message["type"] != "http.response.body":
                await send(start)
                start = None
                await send(message)
                return

            body = message.get("body", b"")
            if gathered is None and message.get("more_body") and _gatherable(start, self.minimum_size):
                gathered = []
            if gathered is not None:
                gathered.append(body)
                if message.get("more_body"):
                    return
                body, gathered = b"".join(gathered), None
                message = {**message, "body": body}
            if message.get("more_body") or not _can_compress(start, body, self.minimum_size):
                await send(start)
                start = None
                await send(message)
                return

            compressor = zlib.compressobj(self.level, zlib.DEFLATED, zlib.MAX_WBITS | 16)
            compressed = compressor.compress(body) + compressor.flush()
            await send(_compressed_start(start, len(compressed)))
            start = None
            result = dict(message)
            result["body"] = compressed
            await send(result)

        await self.app(scope, receive, wrapped)
        if start is not None:
            await send(start)
