import json
from fastapi import Request
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")


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
