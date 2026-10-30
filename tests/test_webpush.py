import json

import pytest

from claude_companion.webpush import PushStore


@pytest.fixture
def store(tmp_path):
    return PushStore(tmp_path)


def _sub(endpoint="https://fcm.example/abc"):
    return {"endpoint": endpoint,
            "keys": {"p256dh": "BPn...", "auth": "xyz"}}


def test_keys_are_generated_once_and_reused(store, tmp_path):
    a = store.public_key()
    b = PushStore(tmp_path).public_key()
    assert a and a == b, "a new key each restart would silently orphan every subscription"


def test_the_private_key_is_not_world_readable(store, tmp_path):
    store.public_key()
    mode = (tmp_path / "session-companion-vapid.json").stat().st_mode & 0o077
    assert mode == 0, "the VAPID private key must not be readable by others"


def test_subscribing_is_idempotent(store):
    store.subscribe(_sub())
    store.subscribe(_sub())
    assert len(store.subscriptions()) == 1, "re-subscribing must not duplicate a device"


def test_several_devices_are_kept(store):
    store.subscribe(_sub("https://fcm.example/phone"))
    store.subscribe(_sub("https://fcm.example/laptop"))
    assert len(store.subscriptions()) == 2


def test_a_subscription_without_an_endpoint_is_refused(store):
    assert store.subscribe({"keys": {}}) is False
    assert store.subscribe("not a dict") is False
    assert store.subscriptions() == []


def test_subscriptions_survive_a_restart(store, tmp_path):
    store.subscribe(_sub())
    assert len(PushStore(tmp_path).subscriptions()) == 1


def test_a_gone_subscription_is_dropped(store):
    """A browser that has been uninstalled or revoked answers 410. Keeping it
    means every later send pays for a device that will never receive again."""
    store.subscribe(_sub("https://fcm.example/dead"))
    store.subscribe(_sub("https://fcm.example/live"))

    def fake_send(sub, payload, **kw):
        if "dead" in sub["endpoint"]:
            raise _Gone(410)
        return True

    sent = store.send("t", "b", sender=fake_send)
    assert sent == 1
    assert [s["endpoint"] for s in store.subscriptions()] == ["https://fcm.example/live"]


def test_a_transient_failure_keeps_the_subscription(store):
    """A 500 from the push service is theirs, not ours -- dropping the device
    would turn a blip into a permanent silence."""
    store.subscribe(_sub())

    def fake_send(sub, payload, **kw):
        raise _Gone(503)

    assert store.send("t", "b", sender=fake_send) == 0
    assert len(store.subscriptions()) == 1


def test_sending_with_no_subscriptions_is_not_an_error(store):
    assert store.send("t", "b", sender=lambda *a, **k: True) == 0


def test_the_payload_carries_what_the_worker_needs(store):
    store.subscribe(_sub())
    seen = {}

    def fake_send(sub, payload, **kw):
        seen.update(json.loads(payload))
        return True

    store.send("Cassius", "needs you", url="https://claude.ai/x", tag="needs-Cassius",
               sender=fake_send)
    assert seen["title"] == "Cassius"
    assert seen["body"] == "needs you"
    assert seen["url"] == "https://claude.ai/x"
    assert seen["tag"] == "needs-Cassius"


class _Gone(Exception):
    def __init__(self, status):
        super().__init__(str(status))
        self.response = type("R", (), {"status_code": status})()


def test_resubscribing_the_same_device_repairs_a_lost_registration(store):
    """The browser's subscribe() succeeds even when the POST to us fails, so a
    phone can end up holding a subscription the server never recorded. The page
    re-posts on every load; that must repair rather than duplicate."""
    store.subscribe(_sub("https://fcm.example/phone"))
    store.subscribe(_sub("https://fcm.example/phone"))
    assert len(store.subscriptions()) == 1


def test_the_real_sender_can_load_the_key_it_generated(tmp_path):
    """The key was stored as a PKCS8 PEM and pywebpush wants the raw 32-byte
    scalar, so every push failed with "Could not deserialize key data" -- and
    send() swallowed it, returning 0, which reads exactly like "nobody is
    subscribed". Nothing looked wrong for an hour."""
    pytest.importorskip("pywebpush")
    store = PushStore(tmp_path)
    store.public_key()                      # generate
    keys = store._keys()
    assert keys.get("private_raw"), "the raw scalar is what the sender needs"

    from py_vapid import Vapid01
    v = Vapid01.from_raw(keys["private_raw"].encode())
    assert v is not None


def test_a_key_file_without_the_raw_scalar_is_upgraded_in_place(tmp_path):
    """Files written before private_raw existed must keep the SAME key --
    regenerating would orphan every subscription already registered."""
    pytest.importorskip("pywebpush")
    store = PushStore(tmp_path)
    pub = store.public_key()
    keys = store._keys()
    del keys["private_raw"]
    store._save_keys(keys)

    assert PushStore(tmp_path)._default_sender() is not None
    assert PushStore(tmp_path).public_key() == pub, "the key must not change"
    assert PushStore(tmp_path)._keys().get("private_raw")


def test_a_failed_send_is_recorded_not_swallowed(tmp_path):
    (tmp_path / "tts").mkdir()
    store = PushStore(tmp_path)
    store.subscribe(_sub())

    def boom(sub, payload):
        raise RuntimeError("push service said no")

    assert store.send("t", "b", sender=boom) == 0
    assert "PUSH-FAIL" in (tmp_path / "tts" / "speak.log").read_text()


class _Timeout(Exception):
    """Shaped like requests' timeouts, which is what the classifier reads."""


def test_a_read_timeout_is_not_treated_as_a_failure(store):
    """The request left the machine and only the answer was lost, so the
    notification very probably arrived. Treating it as failed made speak.sh fall
    back to Home Assistant, and the same clip arrived twice under two app
    names, with the FCM ReadTimeout in the log
    at the same second."""
    store.subscribe(_sub())

    class ReadTimeout(Exception):
        pass

    def slow(sub, payload):
        raise ReadTimeout("HTTPSConnectionPool(host='fcm.googleapis.com', "
                          "port=443): Read timed out. (read timeout=8)")

    assert store.send("t", "b", sender=slow) == 1, \
        "a read timeout must count as delivered, or the fallback duplicates it"
    assert len(store.subscriptions()) == 1, "and the device must be kept"


def test_a_connect_failure_is_still_a_failure(store):
    """Nothing left the machine, so the fallback is exactly right for it. The
    distinction is the whole point: one is a lost answer, the other is a lost
    message."""
    store.subscribe(_sub())

    def dead(sub, payload):
        raise OSError("Failed to establish a new connection: [Errno 113] "
                      "No route to host")

    assert store.send("t", "b", sender=dead) == 0
    assert len(store.subscriptions()) == 1, "a transient failure keeps the device"


def test_the_classifier_does_not_match_on_the_word_timeout_alone():
    """A ConnectTimeout also says "timeout". Matching that would turn every
    unreachable-network failure into a silent success and lose the notification
    entirely -- the opposite mistake, and a worse one."""
    from claude_companion.webpush import _is_read_timeout

    class ConnectTimeout(Exception):
        pass

    assert not _is_read_timeout(ConnectTimeout("connection timeout"))
    assert _is_read_timeout(Exception("Read timed out. (read timeout=8)"))


# --- a send to nobody is a failure --------------------------------------------
# On 2026-09-16 the last push subscription was pruned at 16:19. From then on
# /api/push/send answered {"ok": true} with zero devices, speak.sh's sc_notify
# checked only the HTTP code, and every notification "succeeded" while reaching
# nobody -- so nothing ever fell back to Home Assistant. Clip subtitles
# stopped and nothing said so. The comms-hold notification, whose whole
# job is to be the only way back from an indefinite hold, would have gone the
# same way.

@pytest.mark.skip(reason="needs the server and page: step 2 of the split")
def test_sending_with_no_subscribers_is_not_ok(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SESSION_COMPANION_TOKEN", "t")
    import claude_companion.server as srv
    monkeypatch.setattr(srv._push, "_path", tmp_path / "push.json", raising=False)
    monkeypatch.setattr(srv._push, "subscriptions", lambda: [])
    monkeypatch.setattr(srv._push, "send", lambda *a, **k: 0)
    c = TestClient(srv.app)
    r = c.post("/api/push/send?k=t", json={"title": "x", "body": "y"})
    assert r.status_code == 200, "the request itself was fine"
    d = r.json()
    assert d["ok"] is False, "reported success while reaching nobody"
    assert d["sent"] == 0
    assert d["devices"] == 0
    assert "no device is subscribed" in d["detail"]


@pytest.mark.skip(reason="needs the server and page: step 2 of the split")
def test_sending_to_a_real_subscriber_is_ok(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SESSION_COMPANION_TOKEN", "t")
    import claude_companion.server as srv
    monkeypatch.setattr(srv._push, "subscriptions", lambda: [{"endpoint": "e"}])
    monkeypatch.setattr(srv._push, "send", lambda *a, **k: 1)
    c = TestClient(srv.app)
    d = c.post("/api/push/send?k=t", json={"title": "x", "body": "y"}).json()
    assert d["ok"] is True and d["sent"] == 1 and d["detail"] == ""


@pytest.mark.skip(reason="needs the server and page: step 2 of the split")
def test_subscribers_that_all_reject_are_also_not_ok(tmp_path, monkeypatch):
    """Registered but nobody accepted -- a different cause, same consequence,
    and the caller needs to fall back either way."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SESSION_COMPANION_TOKEN", "t")
    import claude_companion.server as srv
    monkeypatch.setattr(srv._push, "subscriptions", lambda: [{"endpoint": "e"}, {"endpoint": "f"}])
    monkeypatch.setattr(srv._push, "send", lambda *a, **k: 0)
    c = TestClient(srv.app)
    d = c.post("/api/push/send?k=t", json={"title": "x", "body": "y"}).json()
    assert d["ok"] is False
    assert "none of the 2 subscribed devices" in d["detail"]


# --- whose device: each person's own notifications, and none of the owner's to them --

def _sent(store, to=None, record=True):
    got = []
    store.send("t", "b", "/", "x", sender=lambda sub, payload: got.append(sub["endpoint"]), record=record, to=to)
    return got


def test_the_owners_pushes_reach_their_devices_and_others_theirs(store):
    store.subscribe({"endpoint": "https://push/own-old", "keys": {}})            # before people existed: the owner's
    store.subscribe({"endpoint": "https://push/own-new", "keys": {}}, person=None)
    store.subscribe({"endpoint": "https://push/theirs", "keys": {}}, person="alex")
    assert _sent(store) == ["https://push/own-old", "https://push/own-new"]
    assert _sent(store, to="alex") == ["https://push/theirs"]
    assert store.devices(None) == 2 and store.devices("alex") == 1


def test_anothers_pushes_never_land_in_the_owners_feed(store):
    fed = []
    store.on_send = lambda *a: fed.append(a)
    store.subscribe({"endpoint": "https://push/theirs", "keys": {}}, person="alex")
    _sent(store, to="alex")
    assert fed == [], "the Push feed is the owner's"
    _sent(store)
    assert len(fed) == 1


def test_pruning_a_dead_device_keeps_everyone_elses(store):
    class Gone(Exception):
        response = type("R", (), {"status_code": 410})()
    store.subscribe({"endpoint": "https://push/own", "keys": {}})
    store.subscribe({"endpoint": "https://push/theirs", "keys": {}}, person="alex")

    def send(sub, payload):
        raise Gone("gone")
    store.send("t", "b", sender=send, to="alex")
    assert [s["endpoint"] for s in store.subscriptions()] == ["https://push/own"]


def test_a_push_waits_for_a_sleeping_phone_and_wakes_it():
    """Pushes seemed to stop until the dashboard was reopened. pywebpush's TTL default of 0 dropped every push the
    dozing phone could not take at that instant."""
    import json
    from claude_companion.webpush import pick_delivery
    assert pick_delivery(json.dumps({"tag": "ask-1a2b"})) == (86400, "high")
    assert pick_delivery(json.dumps({"tag": "tts-autohold-1"})) == (86400, "high")
    assert pick_delivery(json.dumps({"tag": "sub-Cave"})) == (300, "normal")
    assert pick_delivery("not json") == (86400, "high")


def test_a_newer_ask_buzzes_even_though_it_replaces_the_older(tmp_path):
    """needs-<Name> replaces in place (one per agent) but must not be silent."""
    import json
    from claude_companion.webpush import PushStore
    ps = PushStore(tmp_path)
    ps.subscriptions = lambda: [{"endpoint": "https://x/1", "keys": {}}]
    got = []
    ps.send("Cave asks you", "q", tag="needs-Cave", sender=lambda sub, payload: got.append(json.loads(payload)) or True,
            record=False)
    ps.send("clip", "c", tag="sub-Cave", sender=lambda sub, payload: got.append(json.loads(payload)) or True,
            record=False)
    assert got[0].get("renotify") is True and "renotify" not in got[1], got


def test_no_icons_module_means_no_picture():
    from claude_companion.webpush import _icons
    data, url = _icons()
    assert data("x", "y") == "" and url("x", "y") == ""
