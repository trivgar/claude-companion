import asyncio
import gzip

import pytest

from claude_companion.server.gzip import GzipTextMiddleware


async def _receive():
    return {"type": "http.disconnect"}


def _response(content_type, body, *, more_body=False, headers=()):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
            *headers,
        ]})
        await send({"type": "http.response.body", "body": body, "more_body": more_body})
    return app


def _call(app, headers=((b"accept-encoding", b"gzip"),)):
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "headers": list(headers)}
    asyncio.run(GzipTextMiddleware(app)(scope, _receive, send))
    return sent


def _headers(message):
    return {key.decode().lower(): value.decode() for key, value in message["headers"]}


@pytest.mark.parametrize("content_type", ["text/html; charset=utf-8", "application/json"])
def test_html_and_json_bodies_are_gzipped(content_type):
    body = b"x" * 2048
    start, response = _call(_response(content_type, body))
    headers = _headers(start)
    assert headers["content-encoding"] == "gzip"
    assert "accept-encoding" in headers["vary"].lower()
    assert gzip.decompress(response["body"]) == body


def test_small_body_is_not_gzipped():
    body = b"x" * 100
    start, response = _call(_response("text/plain", body))
    assert "content-encoding" not in _headers(start)
    assert response["body"] == body


def test_audio_is_not_gzipped():
    body = b"x" * 2048
    start, response = _call(_response("audio/mpeg", body))
    assert "content-encoding" not in _headers(start)
    assert response["body"] == body


@pytest.mark.parametrize(("content_type", "headers"), [
    ("image/png", ()),
    ("text/plain", ((b"content-disposition", b"attachment; filename=data.txt"),)),
    ("text/plain", ((b"content-encoding", b"br"),)),
])
def test_images_downloads_and_encoded_responses_are_not_gzipped(content_type, headers):
    body = b"x" * 2048
    start, response = _call(_response(content_type, body, headers=headers))
    assert _headers(start).get("content-encoding") != "gzip"
    assert response["body"] == body


def test_an_event_stream_is_not_gzipped():
    body = b"data: update\n\n" * 200
    start, response = _call(_response("text/event-stream", body, more_body=True))
    assert "content-encoding" not in _headers(start)
    assert response["body"] == body
    assert response["more_body"] is True


def test_client_without_gzip_receives_plain_bytes():
    body = b"x" * 2048
    start, response = _call(_response("text/plain", body), headers=())
    assert "content-encoding" not in _headers(start)
    assert response["body"] == body


def test_compressed_content_length_matches_body():
    body = b"x" * 2048
    start, response = _call(_response("text/css", body))
    assert int(_headers(start)["content-length"]) == len(response["body"])
