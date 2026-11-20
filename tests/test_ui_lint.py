"""ui_lint — HTML template design-token linter, plus its tests.

Rules enforced across all templates:

Visual consistency (prevent drift from the design system):
  badge-font-medium    rounded-full + text-xs + tint bg → must have font-medium/semibold
  icon-button-size     standalone icon buttons must use w-10 h-10, not w-9 or w-11
  modal-button-font    flex-1 modal footer buttons must have font-medium/semibold
  page-heading-color   h2 with text-3xl font-bold must include text-gray-800

Accessibility (prevent silent a11y regressions):
  icon-btn-label       every <button>/<a> (multi-line too) needs text, aria-label or title (file-level)
  input-focus-ring     inputs/selects with border class must have focus:ring-2
  img-alt              every <img> must carry an alt= attribute
  dialog-role          fixed inset-0 overlays with a modal/drawer id need role="dialog"
  label-for            every <label> has for= or wraps its control (file-level)
  clickable-div        div/li/tr/span/td with onclick/@click needs role= and tabindex= so it
                       is keyboard-reachable; overlay backdrops (inset-0) are exempt (file-level)

Colour tokens:
  semantic-color-token raw text-(green|red|emerald|rose)-NNN → use text-income / text-expense /
                       text-success / text-danger (tokens are contrast-checked in both themes)

Brand:
  primary-action-brand Add/Save/Create/New/Edit Budgets buttons use bg-brand, not blue/indigo (file-level)
  primary-button-contrast filled buttons with visible text use bg-brand-solid (white on
                       bg-brand is 2.8:1); bg-brand is for icon-only FABs (file-level)

Two test layers below:
  1. Unit tests — verify each rule fires/passes on synthetic HTML snippets.
  2. Integration test — run the linter against the real template directory
     and assert zero violations (this is the guard that blocks new regressions).
"""

import re
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "app" / "templates"

# Light-shade tint classes used as status-badge backgrounds
_TINT_BG = re.compile(
    r"^bg-(?:green|red|blue|amber|orange|purple|indigo|emerald|"
    r"yellow|pink|rose|violet|cyan|teal|gray)-(?:50|100|200)$"
)

# Input types that never need a visible focus ring
_SKIP_INPUT_TYPES = {"radio", "checkbox", "hidden", "range"}


def _class_sets(line: str) -> list[set[str]]:
    """One set of class tokens per class='…' occurrence on the line."""
    return [set(m.group(1).split()) for m in re.finditer(r'class=["\']([^"\']+)["\']', line)]


def _attr(line: str, name: str) -> str | None:
    """Return the value of a named attribute on the line, or None if absent."""
    m = re.search(rf'{name}=["\']([^"\']*)["\']', line)
    return m.group(1) if m else None


def _button_text(line: str) -> str:
    """Extract text content of a single-line <button>…</button>, tags removed."""
    m = re.search(r"<button[^>]*>(.*?)</button>", line, re.DOTALL)
    if not m:
        return ""
    return re.sub(r"<[^>]+>", "", m.group(1)).strip()


# ── Rules ─────────────────────────────────────────────────────────────────────


def _check_badge_font_medium(line: str, loc: str) -> list[str]:
    """rounded-full + text-xs + tint bg → must declare font weight."""
    violations = []
    for cls in _class_sets(line):
        if (
            "rounded-full" in cls
            and "text-xs" in cls
            and any(_TINT_BG.match(c) for c in cls)
            and "style=" not in line  # dynamic bg set via style= — skip
            and "font-medium" not in cls
            and "font-semibold" not in cls
        ):
            violations.append(
                f"{loc}: [badge-font-medium] text-xs rounded-full badge missing font-medium — add font-medium"
            )
    return violations


def _check_icon_button_size(line: str, loc: str) -> list[str]:
    """Standalone icon buttons must use w-10 h-10 (not w-9 or w-11)."""
    violations = []
    if "<button" not in line:
        return violations
    for cls in _class_sets(line):
        if "rounded-lg" not in cls:
            continue
        if ("w-9" in cls and "h-9" in cls) or ("w-11" in cls and "h-11" in cls):
            bad = "w-9 h-9" if "w-9" in cls else "w-11 h-11"
            violations.append(f"{loc}: [icon-button-size] icon button uses {bad} — standardize to w-10 h-10")
    return violations


def _check_modal_button_font(line: str, loc: str) -> list[str]:
    """flex-1 + px-4 + rounded-lg modal footer buttons need font weight."""
    violations = []
    if "<button" not in line:
        return violations
    for cls in _class_sets(line):
        if {"flex-1", "px-4", "rounded-lg"} <= cls and "font-medium" not in cls and "font-semibold" not in cls:
            violations.append(f"{loc}: [modal-button-font] modal footer button missing font-medium/semibold")
    return violations


def _check_page_heading_color(line: str, loc: str) -> list[str]:
    """Page titles (h1/h2 with text-3xl font-bold) must carry text-gray-800."""
    violations = []
    if "<h2" not in line and "<h1" not in line:
        return violations
    for cls in _class_sets(line):
        if "text-3xl" in cls and "font-bold" in cls and "text-gray-800" not in cls:
            violations.append(f"{loc}: [page-heading-color] heading with text-3xl font-bold is missing text-gray-800")
    return violations


def _check_icon_btn_label(text: str, path: Path) -> list[str]:
    """Every <button>/<a> needs an accessible name: visible text, aria-label,
    aria-labelledby or title. Matches multi-line elements (file-level)."""
    violations = []
    for m in _ELEMENT.finditer(text):
        attrs, inner = m.group(2), m.group(3)
        if re.search(r"\b(aria-label|aria-labelledby|title|:aria-label|x-bind:aria-label)=", attrs):
            continue
        if "x-text" in inner or "x-text" in attrs or "x-html" in attrs:
            continue
        visible = re.sub(r"<[^>]*\bsr-only\b[^>]*>(.*?)</[a-z]+>", r"\1", inner, flags=re.DOTALL)
        visible = re.sub(r"<[^>]+>", "", visible)
        visible = re.sub(r"\{[{%#].*?[%}#]\}", "X", visible).strip()
        if visible:
            continue
        if re.search(r"<img\b[^>]*\balt=\"[^\"]+\"", inner):
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        violations.append(f"{path}:{lineno}: [icon-btn-label] <{m.group(1)}> has no text, aria-label or title")
    return violations


def _check_input_focus_ring(line: str, loc: str) -> list[str]:
    """Bordered inputs/selects/textareas must have focus:ring-2."""
    violations = []
    is_input = any(tag in line for tag in ("<input", "<select", "<textarea"))
    if not is_input:
        return violations
    # Skip input types that never need a focus ring
    t = _attr(line, "type")
    if t in _SKIP_INPUT_TYPES:
        return violations
    for cls in _class_sets(line):
        # Only check elements that have a visible border
        if "border" not in cls:
            continue
        # border-0 / border-none / border-transparent → no visible border
        if cls & {"border-0", "border-none", "border-transparent"}:
            continue
        if "focus:ring-2" not in cls:
            violations.append(f"{loc}: [input-focus-ring] input/select with border class is missing focus:ring-2")
    return violations


def _check_img_alt(line: str, loc: str) -> list[str]:
    """Every <img> must carry an alt= attribute (empty string is valid)."""
    violations = []
    for m in re.finditer(r"<img\b[^>]*>", line):
        tag = m.group(0)
        if "alt=" not in tag:
            violations.append(f'{loc}: [img-alt] <img> is missing alt= attribute — use alt="" for decorative images')
    return violations


def _check_dialog_role(line: str, loc: str) -> list[str]:
    """Overlay modals/drawers (fixed inset-0 with a *modal*/*drawer* id) must declare
    role="dialog" so the shared dialog controller (base.html) manages focus and Esc."""
    if "<div" not in line or "fixed inset-0" not in line:
        return []
    el_id = _attr(line, "id") or ""
    if not re.search(r"modal|drawer", el_id):
        return []
    if 'role="dialog"' in line or 'role="alertdialog"' in line:
        return []
    return [f'{loc}: [dialog-role] overlay #{el_id} is missing role="dialog" aria-modal="true"']


# ── File-level rules (elements spanning several lines) ───────────────────────

_PRIMARY_ACTION_TEXT = re.compile(r"^(Add|Save|Create|New|Edit Budgets)\b")
_BLUE_PRIMARY_BG = {"bg-blue-500", "bg-blue-600", "bg-primary", "bg-accent", "bg-indigo-500", "bg-indigo-600"}
_ELEMENT = re.compile(r"<(button|a)\b([^>]*)>(.*?)</\1>", re.DOTALL)


def _check_primary_action_brand(text: str, path: Path) -> list[str]:
    """Primary actions (Add/Save/Create/New/Edit Budgets) use brand orange, not blue —
    orange is the action colour, blue is reserved for links and data."""
    violations = []
    for m in _ELEMENT.finditer(text):
        attrs, inner = m.group(2), m.group(3)
        label = re.sub(r"\{[{%#].*?[%}#]\}", "", re.sub(r"<[^>]+>", "", inner)).strip()
        if not _PRIMARY_ACTION_TEXT.match(label):
            continue
        classes = set(_attr(attrs, "class").split()) if _attr(attrs, "class") else set()
        if classes & _BLUE_PRIMARY_BG:
            lineno = text.count("\n", 0, m.start()) + 1
            violations.append(
                f"{path}:{lineno}: [primary-action-brand] '{label[:30]}' uses a blue/indigo background"
                " — use bg-brand-solid hover:bg-brand-solid-hover"
            )
    return violations


_LABEL = re.compile(r"<label\b([^>]*)>(.*?)</label>", re.DOTALL)


def _check_label_association(text: str, path: Path) -> list[str]:
    """Every <label> names a control: either for="id" or by wrapping the input/select/textarea.
    (Group captions for radio sets use <p id> + role="radiogroup" aria-labelledby instead.)"""
    violations = []
    for m in _LABEL.finditer(text):
        if "for=" in m.group(1) or re.search(r"<(input|select|textarea)\b", m.group(2)):
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        violations.append(f'{path}:{lineno}: [label-for] <label> has no for="…" and wraps no control')
    return violations


def _visible_text(inner: str) -> str:
    """Text a sighted user sees inside an element: tags stripped, Jinja expressions kept as 'X'."""
    text = re.sub(r"<[^>]+>", "", inner)
    return re.sub(r"\{[{%#].*?[%}#]\}", "X", text).strip()


def _check_primary_button_contrast(text: str, path: Path) -> list[str]:
    """White text on bg-brand (#F97316) is 2.8:1 — buttons/links with visible text must use
    bg-brand-solid. Icon-only round FABs may keep bg-brand."""
    violations = []
    for m in _ELEMENT.finditer(text):
        attrs, inner = m.group(2), m.group(3)
        classes = set((_attr(attrs, "class") or "").split())
        if "bg-brand" not in classes or "text-white" not in classes:
            continue
        if not _visible_text(inner) and "x-text" not in inner:
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        violations.append(f"{path}:{lineno}: [primary-button-contrast] text on bg-brand fails AA — use bg-brand-solid")
    return violations


_CLICKABLE = re.compile(r"<(div|li|tr|span|td)\b([^>]*?(?:\sonclick=|\s@click=)[^>]*)>", re.DOTALL)


def _check_clickable_div(text: str, path: Path) -> list[str]:
    """Non-interactive elements with click handlers must be focusable and announced."""
    violations = []
    for m in _CLICKABLE.finditer(text):
        attrs = m.group(2)
        if "role=" in attrs and "tabindex=" in attrs:
            continue
        if re.search(r"\binset-0\b|@click\.(?:stop|self|outside|away)", attrs):
            continue
        lineno = text.count("\n", 0, m.start()) + 1
        violations.append(
            f"{path}:{lineno}: [clickable-div] <{m.group(1)}> has a click handler but no role= + tabindex="
        )
    return violations


_FILE_RULES = [
    _check_clickable_div,
    _check_primary_action_brand,
    _check_label_association,
    _check_primary_button_contrast,
    _check_icon_btn_label,
]


_RAW_SEMANTIC_TEXT = re.compile(r"(?<![\w.-])(?:[a-z-]+:)*text-(?:green|red|emerald|rose)-\d{3}\b")


def _check_semantic_color_token(line: str, loc: str) -> list[str]:
    """Income/expense/status text goes through tokens. CSS selectors (`.dark .text-red-600`)
    are dark-mode overrides, not usages, and are skipped."""
    if re.search(r"\.(?:dark|peer)\b.*\{", line):
        return []
    m = _RAW_SEMANTIC_TEXT.search(line)
    if not m:
        return []
    return [f"{loc}: [semantic-color-token] '{m.group(0)}' — use text-income/text-expense/text-success/text-danger"]


# ── Dispatcher ────────────────────────────────────────────────────────────────

_RULES = [
    _check_badge_font_medium,
    _check_icon_button_size,
    _check_modal_button_font,
    _check_page_heading_color,
    _check_input_focus_ring,
    _check_img_alt,
    _check_dialog_role,
    _check_semantic_color_token,
]


def lint_line(line: str, path: Path, lineno: int) -> list[str]:
    loc = f"{path}:{lineno}"
    violations: list[str] = []
    for rule in _RULES:
        violations.extend(rule(line, loc))
    return violations


def lint_file(path: Path) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return [f"{path}: cannot read — {exc}"]
    violations: list[str] = []
    for lineno, line in enumerate(lines, 1):
        violations.extend(lint_line(line, path, lineno))
    text = "\n".join(lines)
    for rule in _FILE_RULES:
        violations.extend(rule(text, path))
    return violations


def lint_dir(templates_dir: Path = TEMPLATES_DIR) -> list[str]:
    violations: list[str] = []
    for path in sorted(templates_dir.rglob("*.html")):
        violations.extend(lint_file(path))
    return violations


# ── badge-font-medium ─────────────────────────────────────────────────────────


def test_badge_missing_font_medium_is_flagged():
    line = '<span class="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700">On track</span>'
    assert any("badge-font-medium" in v for v in lint_line(line, Path("t.html"), 1))


def test_badge_with_font_medium_passes():
    line = '<span class="text-xs font-medium px-2 py-0.5 rounded-full bg-green-100 text-success-text">On track</span>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_badge_with_font_semibold_passes():
    line = '<span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">3 pending</span>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_badge_no_tint_bg_not_flagged():
    """bg-white, bg-primary, no bg at all → not a status badge."""
    line = '<span class="text-xs px-2 py-0.5 rounded-full bg-white text-gray-700">Label</span>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_badge_text_sm_not_flagged():
    """Pending-pill uses text-sm (not text-xs) — must not be flagged."""
    line = (
        '<span class="text-sm font-semibold px-3.5 py-1.5 rounded-full '
        'bg-orange-50 border text-orange-700">3 pending</span>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_badge_shade_500_opacity_not_flagged():
    """bg-green-500/20 is an opacity tint, not a standard light-shade badge bg."""
    line = '<span class="text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-white">↑ flat</span>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_badge_dynamic_style_skipped():
    """Inline style= means background is set dynamically — skip the check."""
    line = (
        '<span class="text-xs px-2 py-0.5 rounded-full bg-green-100" style="background-color: {{ color }}">label</span>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


# ── icon-button-size ──────────────────────────────────────────────────────────


def test_icon_button_w9_flagged():
    line = (
        '<button class="w-9 h-9 rounded-lg flex items-center'
        ' justify-center bg-green-100"><i class="fas fa-download"></i></button>'
    )
    assert any("icon-button-size" in v for v in lint_line(line, Path("t.html"), 1))


def test_icon_button_w11_flagged():
    line = (
        '<button class="w-11 h-11 rounded-lg flex items-center'
        ' justify-center bg-white border"><i class="fas fa-history"></i></button>'
    )
    assert any("icon-button-size" in v for v in lint_line(line, Path("t.html"), 1))


def test_icon_button_w10_passes():
    line = (
        '<button title="Refresh" class="w-10 h-10 rounded-lg flex items-center'
        ' justify-center bg-white border"><i class="fas fa-sync-alt text-sm"></i></button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_small_close_button_w7_not_flagged():
    """The pill-internal × close button uses w-7 h-7 — intentionally smaller."""
    line = '<button class="hidden w-7 h-7 flex items-center justify-center rounded-lg text-gray-400">&#215;</button>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_month_nav_w10_passes():
    line = (
        '<button onclick="prevMonth()" class="w-10 h-10 flex items-center'
        ' justify-center rounded-lg text-xl text-gray-400">&#8249;</button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_table_row_icon_no_width_not_flagged():
    """Table row action buttons use p-2, not explicit width — not checked by icon-button-size."""
    line = (
        '<button title="Edit" class="p-2 text-gray-400 hover:text-primary'
        ' rounded-lg hover:bg-gray-100"><i class="fas fa-edit"></i></button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


# ── modal-button-font ─────────────────────────────────────────────────────────


def test_modal_button_missing_font_flagged():
    line = '<button onclick="closeModal()" class="flex-1 px-4 py-2.5 border rounded-lg text-sm">Cancel</button>'
    assert any("modal-button-font" in v for v in lint_line(line, Path("t.html"), 1))


def test_modal_button_font_medium_passes():
    line = (
        '<button onclick="closeModal()" class="flex-1 px-4 py-2.5'
        ' border rounded-lg text-sm font-medium">Cancel</button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_modal_button_font_semibold_passes():
    line = (
        '<button onclick="save()" class="flex-1 px-4 py-2.5'
        ' bg-primary text-white rounded-lg text-sm font-semibold">Save</button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_non_modal_button_not_flagged():
    """No flex-1 → not a modal footer button."""
    line = '<button class="px-4 py-2 bg-primary text-white rounded-lg text-sm">Open</button>'
    assert lint_line(line, Path("t.html"), 1) == []


# ── page-heading-color ────────────────────────────────────────────────────────


def test_page_heading_missing_gray800_flagged():
    line = '<h2 class="text-3xl font-bold">Transactions</h2>'
    assert any("page-heading-color" in v for v in lint_line(line, Path("t.html"), 1))


def test_page_heading_wrong_color_flagged():
    line = '<h2 class="text-3xl font-bold text-gray-700">Transactions</h2>'
    assert any("page-heading-color" in v for v in lint_line(line, Path("t.html"), 1))


def test_page_heading_with_gray800_passes():
    line = '<h2 class="text-3xl font-bold text-gray-800">Transactions</h2>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_page_heading_not_3xl_not_checked():
    """Smaller headings (card titles, sidebar) are not page-level headings."""
    line = '<h2 class="text-xl font-bold">Card Title</h2>'
    assert lint_line(line, Path("t.html"), 1) == []


# ── icon-btn-label ────────────────────────────────────────────────────────────


def test_icon_btn_no_label_flagged():
    html = '<button onclick="doThing()" class="text-gray-400"><i class="fas fa-times"></i></button>'
    assert _check_icon_btn_label(html, Path("t.html"))


def test_icon_btn_multiline_no_label_flagged():
    html = '<button onclick="doThing()"\n        class="w-12 h-12">\n    <i class="fas fa-plus"></i>\n</button>'
    assert _check_icon_btn_label(html, Path("t.html"))


def test_icon_link_no_label_flagged():
    html = '<a href="/x"><i class="fas fa-arrow-left"></i></a>'
    assert _check_icon_btn_label(html, Path("t.html"))


def test_icon_btn_with_title_passes():
    html = '<button title="Close" class="text-gray-400"><i class="fas fa-times"></i></button>'
    assert not _check_icon_btn_label(html, Path("t.html"))


def test_icon_btn_with_aria_label_passes():
    html = '<button aria-label="Close"><i class="fas fa-times"></i></button>'
    assert not _check_icon_btn_label(html, Path("t.html"))


def test_icon_btn_with_text_not_flagged():
    html = '<button class="px-4">\n  <i class="fas fa-plus mr-2"></i>Add Transaction\n</button>'
    assert not _check_icon_btn_label(html, Path("t.html"))


def test_icon_btn_with_jinja_or_x_text_not_flagged():
    assert not _check_icon_btn_label('<a href="/p">{{ name }}</a>', Path("t.html"))
    assert not _check_icon_btn_label('<button><span x-text="label"></span></button>', Path("t.html"))


# ── input-focus-ring ──────────────────────────────────────────────────────────


def test_input_missing_focus_ring_flagged():
    line = '<input type="text" class="border rounded-lg px-3 py-2 text-sm">'
    assert any("input-focus-ring" in v for v in lint_line(line, Path("t.html"), 1))


def test_select_missing_focus_ring_flagged():
    line = '<select class="border rounded-lg px-3 py-2 text-sm">'
    assert any("input-focus-ring" in v for v in lint_line(line, Path("t.html"), 1))


def test_input_with_focus_ring_passes():
    line = '<input type="text" class="border rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-primary">'
    assert lint_line(line, Path("t.html"), 1) == []


def test_input_checkbox_not_checked():
    """Checkboxes and radios don't need focus:ring-2 via this rule."""
    line = '<input type="checkbox" class="border w-5 h-5 rounded">'
    assert lint_line(line, Path("t.html"), 1) == []


def test_input_hidden_not_checked():
    line = '<input type="hidden" class="border">'
    assert lint_line(line, Path("t.html"), 1) == []


def test_input_border_none_not_checked():
    """Explicitly borderless inputs don't need a focus ring."""
    line = '<input type="text" class="border-none bg-transparent outline-none">'
    assert lint_line(line, Path("t.html"), 1) == []


def test_input_no_border_class_not_checked():
    """Inputs without the border class token are not checked."""
    line = '<input type="text" class="border-gray-300 rounded-lg px-3 py-2">'
    assert lint_line(line, Path("t.html"), 1) == []


# ── img-alt ───────────────────────────────────────────────────────────────────


def test_img_without_alt_flagged():
    line = '<img src="/static/logo.png" class="w-32">'
    assert any("img-alt" in v for v in lint_line(line, Path("t.html"), 1))


def test_img_with_alt_passes():
    line = '<img src="/static/logo.png" alt="Company logo" class="w-32">'
    assert lint_line(line, Path("t.html"), 1) == []


def test_img_with_empty_alt_passes():
    """alt="" is valid for decorative images."""
    line = '<img src="/static/decoration.png" alt="" class="w-full">'
    assert lint_line(line, Path("t.html"), 1) == []


# ── integration: actual template directory must be clean ──────────────────────


def test_templates_are_clean():
    """Zero ui-lint violations in the live template directory.

    This test will fail if a developer adds a new template that violates
    the design tokens. Fix the template (not the linter) to make it pass.
    """
    violations = lint_dir(TEMPLATES_DIR)
    if violations:
        formatted = "\n".join(f"  ✗ {v}" for v in violations)
        pytest.fail(f"ui-lint found {len(violations)} violation(s) — fix the templates:\n{formatted}")


# ── primary-action-brand ──────────────────────────────────────────────────────


def test_primary_action_blue_flagged():
    html = '<button class="bg-blue-600 text-white px-4">\n  <i class="fas fa-plus"></i> Add Asset\n</button>'
    assert _check_primary_action_brand(html, Path("x.html"))


def test_primary_action_brand_passes():
    html = '<button class="bg-brand hover:bg-brand-hover text-white">Save Transaction</button>'
    assert not _check_primary_action_brand(html, Path("x.html"))


def test_blue_non_action_button_not_flagged():
    html = '<a href="/x" class="bg-blue-600 text-white">View all</a>'
    assert not _check_primary_action_brand(html, Path("x.html"))


# ── dialog-role ───────────────────────────────────────────────────────────────


def test_dialog_without_role_flagged():
    line = '<div id="asset-modal" class="hidden fixed inset-0 z-[100] flex">'
    assert _check_dialog_role(line, "x:1")


def test_dialog_with_role_passes():
    line = '<div id="asset-modal" role="dialog" aria-modal="true" class="hidden fixed inset-0 z-[100] flex">'
    assert not _check_dialog_role(line, "x:1")


def test_non_dialog_overlay_not_flagged():
    line = '<div id="toast-layer" class="fixed inset-0 pointer-events-none">'
    assert not _check_dialog_role(line, "x:1")


# ── label-for ─────────────────────────────────────────────────────────────────


def test_label_without_for_flagged():
    html = '<label class="block">Amount</label>\n<input id="amt" class="border focus:ring-2">'
    assert _check_label_association(html, Path("x.html"))


def test_label_with_for_passes():
    assert not _check_label_association('<label for="amt">Amount</label>', Path("x.html"))


def test_wrapping_label_passes():
    assert not _check_label_association('<label><input type="checkbox"> Remember</label>', Path("x.html"))


# ── primary-button-contrast ───────────────────────────────────────────────────


def test_primary_button_contrast_flags_text_on_bg_brand():
    html = '<button class="bg-brand text-white px-4">\n  <i class="fas fa-plus"></i> Add\n</button>'
    assert _check_primary_button_contrast(html, Path("x.html"))


def test_primary_button_contrast_allows_solid():
    html = '<button class="bg-brand-solid text-white px-4">Add</button>'
    assert not _check_primary_button_contrast(html, Path("x.html"))


def test_primary_button_contrast_allows_icon_only_fab():
    html = '<button aria-label="Add" class="bg-brand text-white rounded-full"><i class="fas fa-plus"></i></button>'
    assert not _check_primary_button_contrast(html, Path("x.html"))


# ── semantic-color-token ──────────────────────────────────────────────────────


def test_semantic_color_token_flags_raw_green():
    line = '<span class="text-sm text-green-600">+1,000</span>'
    assert any("semantic-color-token" in v for v in lint_line(line, Path("t.html"), 1))


def test_semantic_color_token_flags_variant_prefix():
    line = '<a class="hover:text-red-700">Delete</a>'
    assert any("semantic-color-token" in v for v in lint_line(line, Path("t.html"), 1))


def test_semantic_color_token_allows_tokens():
    line = '<span class="text-income">+1,000</span><span class="text-danger">x</span>'
    assert not any("semantic-color-token" in v for v in lint_line(line, Path("t.html"), 1))


def test_semantic_color_token_skips_dark_css_overrides():
    line = "        .dark .text-red-600   { color: #f87171 !important; }"
    assert not any("semantic-color-token" in v for v in lint_line(line, Path("t.html"), 1))


# ── clickable-div ─────────────────────────────────────────────────────────────


def test_clickable_div_flagged():
    html = '<div class="row cursor-pointer"\n     onclick="openTx(1)">Coffee</div>'
    assert _check_clickable_div(html, Path("x.html"))


def test_clickable_div_with_role_and_tabindex_passes():
    html = '<div role="button" tabindex="0" onclick="openTx(1)">Coffee</div>'
    assert not _check_clickable_div(html, Path("x.html"))


def test_clickable_backdrop_exempt():
    html = '<div class="absolute inset-0" onclick="closeDrawer()"></div>'
    assert not _check_clickable_div(html, Path("x.html"))
