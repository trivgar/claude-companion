import pytest

pytest.importorskip("fastapi")
sync_api = pytest.importorskip("playwright.sync_api")

from claude_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64
LONG = "Friday at 2pm works for me. I'll move the invite and send the agenda the day before.\n\nThanks,\nAlex"
DRAFTS = {"counts": {"pending": 1}, "drafts": [
    {"id": "d1", "subject": "Moving the sync", "from": "Sam <sam@example.com>", "why": "asked", "reply": LONG,
     "state": "pending"}]}


@pytest.fixture(scope="module")
def base_url(tmp_path_factory):
    import threading
    import time
    import uvicorn
    orig = server.FEATURES
    server.FEATURES = ["mail"]
    app = server.create_app(token=TOKEN, owner_key=OWNER)
    server.FEATURES = orig
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{srv.servers[0].sockets[0].getsockname()[1]}"
    srv.should_exit = True


def test_a_draft_shows_in_full_not_cut_at_five_lines(base_url):
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
        pg.route("**/api/mail", lambda r: r.fulfill(json=DRAFTS))
        pg.add_init_script(f"localStorage.setItem('cc-owner-key', '{OWNER}')")
        pg.goto(f"{base_url}/")
        pg.wait_for_selector("textarea")
        assert pg.evaluate("(() => { const t = document.querySelector('textarea'); return t.scrollHeight <= t.clientHeight + 1 })()")
        assert [x.inner_text() for x in pg.locator(".acts button").all()] == ["Approve", "Save edit", "Skip"]
        b.close()
