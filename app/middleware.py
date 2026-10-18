"""ProfileMiddleware — resolves the household profile once per request.

Fail-closed by default: any route not on the public allowlist requires a
resolved profile, so future routers are covered automatically. The resolved
context is placed on request.state where both handlers and the Jinja context
processors (inject_nav_items) read it.

SameOriginMiddleware is the CSRF guard. The app has no login, so a cross-site
form POST (e.g. to the public /profiles/{id}/delete) would otherwise succeed
from any page a household member visits. Unsafe methods whose Origin/Referer
names a different host are rejected; requests carrying neither header
(curl, workers, TestClient) are allowed through.
"""

from urllib.parse import quote, urlsplit

from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.services import profiles as profiles_service

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}

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


def _request_hosts(request) -> set[str]:
    hosts = {request.headers.get("host", "")}
    forwarded = request.headers.get("x-forwarded-host")
    if forwarded:
        hosts.add(forwarded.split(",")[0].strip())
    return {h.lower() for h in hosts if h}


def _is_cross_origin(request) -> bool:
    source = request.headers.get("origin") or request.headers.get("referer")
    if source is None:
        return False
    if source == "null":  # sandboxed iframe / privacy-redirected form
        return True
    return urlsplit(source).netloc.lower() not in _request_hosts(request)


class SameOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method not in SAFE_METHODS and _is_cross_origin(request):
            return JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
