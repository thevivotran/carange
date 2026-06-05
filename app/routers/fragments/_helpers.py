import decimal
import json
import markupsafe
from datetime import timedelta, timezone
from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


def _decimal_safe_tojson(value, **kwargs):
    class _Enc(json.JSONEncoder):
        def default(self, o):
            if isinstance(o, decimal.Decimal):
                return float(o)
            return super().default(o)

    return markupsafe.Markup(
        json.dumps(value, cls=_Enc)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("'", "\\u0027")
    )


templates.env.filters["tojson"] = _decimal_safe_tojson

_VN_TZ = timezone(timedelta(hours=7))


def _format_vn_dt(dt) -> str:
    """Format a UTC datetime as HH:MM DD/MM in Vietnam time (UTC+7)."""
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_VN_TZ).strftime("%H:%M %d/%m")


templates.env.filters["format_vn_dt"] = _format_vn_dt


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render_fragment(
    request: Request,
    template_name: str,
    context: dict,
    *,
    toast: str = None,
    toast_type: str = "success",
    push_url: str = None,
    trigger_events: dict = None,
):
    response = templates.TemplateResponse(request, template_name, context)
    events = {}
    if toast:
        events["showToast"] = {"message": toast, "type": toast_type}
    if trigger_events:
        events.update(trigger_events)
    if events:
        response.headers["HX-Trigger"] = json.dumps(events)
    if push_url:
        response.headers["HX-Push-Url"] = push_url
    return response
