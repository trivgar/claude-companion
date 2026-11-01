"""Android GET exceptions remain owner reads."""
from agent_companion.server import auth, comms


def test_android_gets_have_the_owner_read_guard():
    routes = {route.path: route for route in comms.router.routes}
    for path in ("/api/tts/outbox", "/api/tts/clip/{seq}"):
        guards = {dependency.call for dependency in routes[path].dependant.dependencies}
        assert guards == {auth.require_token}
