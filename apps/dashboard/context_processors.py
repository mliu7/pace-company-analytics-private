import os

from django.conf import settings

_CSS = os.path.join(os.path.dirname(__file__), "static", "dashboard", "app.css")


def _asset_version():
    """Latest shared/map asset mtime → ?v= cache-buster, so stylesheet and map-script edits are never served stale
    (the dev server sends no cache headers and browsers heuristically cache static files)."""
    try:
        assets = [_CSS] + [os.path.join(os.path.dirname(_CSS), name) for name in
                           ("project_map.css", "project_map.js", "project_map_geometry.js", "project_map_towers.js", "search.js")]
        return int(max(os.stat(path).st_mtime for path in assets if os.path.exists(path)))
    except OSError:
        return 0


def app_context(request):
    return {"APP_NAME": "Pace Company Analytics", "MODELLED_DIVISION": settings.MODELLED_DIVISION_CODE,
            "ASSET_VERSION": _asset_version(), "PRIVATE_MODE": settings.PRIVATE_MODE}
