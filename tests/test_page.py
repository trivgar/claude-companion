import json
import re

import pytest

pytest.importorskip("fastapi")
sync_api = pytest.importorskip("playwright.sync_api")
from fastapi.testclient import TestClient  # noqa: E402,F401

from claude_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
ROWS = [
    {"pid": 1, "name": "alpha", "project": "api", "title": "Rate limiter", "status": "busy", "waiting": False,
     "model": "claude-opus-5-5", "cost_usd": 3.21, "context_pct": 42.5, "last_reply": "Token bucket in place.",
     "last_activity": 4, "cwd": "/w/api", "branch": "main", "child": False, "session_id": "s1"},
    {"pid": 2, "name": "beta <b>x</b>", "project": "web", "title": "", "status": "idle", "waiting": True,
     "model": "claude-sonnet-5-5", "cost_usd": 0.5, "context_pct": 10, "last_reply": "x" * 400,
     "last_activity": 90, "cwd": "/w/web", "child": False, "session_id": "s2"},
    {"pid": 3, "name": "gamma", "project": "worker", "title": "", "status": "idle", "waiting": False,
     "model": "claude-haiku-5-5", "cost_usd": 0.1, "context_pct": 5, "last_reply": "Ready.",
     "last_activity": 10, "cwd": "/w/worker", "child": False, "session_id": "s3"},
]
USAGE = {"five_hour": {"cost_usd": 1.5, "total_tokens": 1000}, "seven_day": {"cost_usd": 20, "total_tokens": 9000},
         "limits": {"available": False, "reason": "no credentials"}}


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    import os
    d = tmp_path_factory.mktemp("home")
    os.environ["CLAUDE_COMPANION_CLAUDE_HOME"] = str(d / ".claude")
    os.environ["CLAUDE_COMPANION_DATA_DIR"] = str(d / "data")
    orig = server.FEATURES
    import threading
    import uvicorn
    server.FEATURES = ["sessions", "push"]
    app = server.create_app(token=TOKEN, owner_key=OWNER)
    server.FEATURES = orig
    cfg = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    import time
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    os.environ.pop("CLAUDE_COMPANION_CLAUDE_HOME", None)
    os.environ.pop("CLAUDE_COMPANION_DATA_DIR", None)


@pytest.fixture
def page(base_url):
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # browser not installed
            pytest.skip(f"chromium unavailable: {exc}")
        ctx = b.new_context(viewport={"width": 390, "height": 844})
        pg = ctx.new_page()
        pg.route("**/api/sessions*", lambda r: r.fulfill(json=ROWS))
        pg.route("**/api/usage*", lambda r: r.fulfill(json=USAGE))
        pg.add_init_script(f"localStorage.setItem('cc-owner-key', '{OWNER}')")
        pg.goto(f"{base_url}/")
        yield pg
        b.close()


def test_cards_render_without_horizontal_scroll(page):
    page.wait_for_selector(".scard")
    assert page.locator(".scard").count() == 3
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Strings are escaped, never parsed as markup.
    assert page.locator(".scard b").count() == 0
    assert "beta <b>x</b>" in page.locator(".scard").nth(1).inner_text()


def test_waiting_session_shows_alert_badge_and_nav_count(page):
    page.wait_for_selector(".scard")
    waiting = page.locator(".scard.waiting")
    assert waiting.count() == 1
    badge = waiting.locator(".wbadge")
    assert badge.inner_text().strip().lower() == "waiting"
    color = badge.evaluate("e => getComputedStyle(e).color")
    root = page.evaluate("(() => { const d = document.createElement('i'); d.style.color='var(--alert)'; "
                         "document.body.appendChild(d); return getComputedStyle(d).color })()")
    assert color == root
    assert page.locator('#nav button[data-id="sessions"] .n').inner_text().strip() == "1"


def test_the_strip_shows_spend(page):
    page.wait_for_selector(".scard")
    page.wait_for_function("document.getElementById('strip').textContent.includes('$')")
    t = page.locator("#strip").inner_text()
    assert "$1.50" in t and "$20.00" in t


def test_a_failed_read_says_the_servers_reason(page):
    # A feature that is not set up answers 503 with a detail saying what to set;
    # the page shows that, not "/api/...: 503".
    page.wait_for_selector(".scard")
    msg = page.evaluate("CC.get('/api/nowhere-here').then(() => 'ok', e => e.message)")
    assert msg == "Not Found"
    page.route("**/api/broken*", lambda r: r.fulfill(status=503, json={"detail": "comms is not set up: set tts_dir"}))
    msg = page.evaluate("CC.get('/api/broken').then(() => 'ok', e => e.message)")
    assert msg == "comms is not set up: set tts_dir"
    page.route("**/api/bare*", lambda r: r.fulfill(status=502, body="bad gateway"))
    msg = page.evaluate("CC.get('/api/bare').then(() => 'ok', e => e.message)")
    assert msg == "/api/bare: 502"


@pytest.mark.parametrize(("action", "name", "path", "body"), [
    ("stop", "alpha", "/api/cave/stop/alpha", {}),
    ("resume", "gamma", "/api/cave/resume/gamma", {"pid": 3, "session_id": "s3"}),
    ("compact", "gamma", "/api/cave/send", {"to": "gamma", "text": "/compact"}),
    ("clear", "gamma", "/api/cave/send", {"to": "gamma", "text": "/clear"}),
    ("end", "gamma", "/api/cave/end/gamma", {"pid": 3, "session_id": "s3"}),
])
def test_card_action_arms_then_posts(page, action, name, path, body):
    calls = []

    def cave(route):
        calls.append((route.request.url, route.request.post_data_json))
        route.fulfill(json={"ok": True, "detail": ""})

    page.route("**/api/cave/**", cave)
    button = page.locator(f'.scard:has(h3:text-is("{name}")) [data-card-action="{action}"]')
    button.click()
    assert button.inner_text() == f"Tap again to {action}"
    assert calls == []

    button.click()
    page.locator(".action-result", has_text=action.capitalize() + ("ped." if action == "stop" else "")).wait_for(timeout=1000)
    assert len(calls) == 1
    assert calls[0][0].endswith(path)
    assert calls[0][1] == body


def test_a_single_card_action_tap_does_not_post(page):
    calls = []
    page.route("**/api/cave/**", lambda route: calls.append(route) or route.fulfill(json={"ok": True}))
    button = page.locator('.scard:has(h3:text-is("gamma")) [data-card-action="clear"]')
    button.click()
    assert button.inner_text() == "Tap again to clear"
    assert calls == []
