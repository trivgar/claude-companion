"""A first start reads all of Claude Code's history; the page says so."""
import pytest

pytest.importorskip("fastapi")
sync_api = pytest.importorskip("playwright.sync_api")

from agent_companion import server  # noqa: E402

TOKEN = "t" * 32
OWNER = "o" * 64


@pytest.fixture(scope="module")
def base_url():
    import socket
    import threading
    import time
    import uvicorn

    original = server.FEATURES
    server.FEATURES = ["sessions"]
    app = server.create_app(token=TOKEN, owner_key=OWNER)
    server.FEATURES = original
    try:
        sock = socket.socket()
    except PermissionError as exc:
        pytest.skip(f"sandbox cannot open the browser-test socket: {exc}")
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    threading.Thread(target=lambda: service.run(sockets=[sock]), daemon=True).start()
    for _ in range(100):
        if service.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    service.should_exit = True


def test_a_slow_first_load_says_why_instead_of_spinning(base_url):
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
        page.route("**/api/sessions", lambda route: None)   # never answers, like a first scan
        page.add_init_script(f"localStorage.setItem('cc-owner-key', '{OWNER}')")
        page.goto(f"{base_url}/?view=sessions")
        page.wait_for_selector("text=Loading sessions...")
        page.wait_for_selector("text=Reading your Claude Code history", timeout=8000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        browser.close()
