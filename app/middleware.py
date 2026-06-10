"""ProfileMiddleware — resolves the household profile once per request.

Fail-closed by default: any route not on the public allowlist requires a
resolved profile, so future routers are covered automatically. The resolved
context is placed on request.state where both handlers and the Jinja context
processors (inject_nav_items) read it.
"""

from urllib.parse import quote

from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.services import profiles as profiles_service

PUBLIC_EXACT = {"/health", "/sw.js", "/favicon.ico"}
PUBLIC_PREFIXES = ("/static/", "/profiles")


def _is_public(path: str) -> bool:
    return path in PUBLIC_EXACT or any(path.startswith(p) for p in PUBLIC_PREFIXES)


class ProfileMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        ctx = profiles_service.resolve_request_context(request)
        if ctx is not None:
            request.state.user = ctx.user
            request.state.visible_nav_items = ctx.visible_nav_items
            request.state.visible_sections = ctx.visible_sections
        elif not _is_public(request.url.path):
            # HTMX swap must never inject the picker page into the DOM —
            # HX-Redirect makes htmx do a full-page navigation instead.
            if request.headers.get("HX-Request") == "true":
                return Response(status_code=401, headers={"HX-Redirect": "/profiles"})
            if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
                return RedirectResponse(f"/profiles?next={quote(request.url.path)}", status_code=302)
            return JSONResponse({"detail": "No profile selected"}, status_code=401)
        return await call_next(request)
