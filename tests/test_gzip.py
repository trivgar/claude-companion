import asyncio
import gzip

import pytest

from agent_companion.server.gzip import GzipTextMiddleware


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


def _chunked(content_type, chunks, *, length=True):
    total = sum(len(c) for c in chunks)
    async def app(scope, receive, send):
        headers = [(b"content-type", content_type.encode())]
        if length:
            headers.append((b"content-length", str(total).encode()))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for i, chunk in enumerate(chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": i < len(chunks) - 1})
    return app


@pytest.mark.parametrize("content_type", ["application/javascript", "text/javascript; charset=utf-8"])
def test_a_static_file_sent_in_chunks_is_gathered_and_gzipped(content_type):
    chunks = [b"console.log(1);\n" * 300, b"console.log(2);\n" * 300, b""]
    sent = _call(_chunked(content_type, chunks))
    assert len(sent) == 2
    assert _headers(sent[0])["content-encoding"] == "gzip"
    assert gzip.decompress(sent[1]["body"]) == b"".join(chunks)
    assert _headers(sent[0])["content-length"] == str(len(sent[1]["body"]))


def test_chunks_without_a_length_pass_through_as_a_stream():
    chunks = [b"a" * 2000, b"b" * 2000, b""]
    sent = _call(_chunked("text/plain", chunks, length=False))
    assert "content-encoding" not in _headers(sent[0])
    assert [m["body"] for m in sent[1:]] == chunks
