"""Every route an app serves, on any FastAPI version.

FastAPI up to about 0.135 copies an included router's routes into
app.routes. Newer versions keep one _IncludedRouter entry per include, with
the routes under original_router, so a plain walk of app.routes sees only the
app's own few. A guard test written that way passes while checking nothing.
"""


def all_routes(app_or_router):
    out = []
    for route in getattr(app_or_router, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            out.extend(all_routes(inner))
        else:
            out.append(route)
    return out
