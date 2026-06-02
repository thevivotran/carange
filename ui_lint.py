"""ui_lint.py — HTML template design-token linter.

Seven rules enforced across all templates:

Visual consistency (prevent drift from the design system):
  badge-font-medium    rounded-full + text-xs + tint bg → must have font-medium/semibold
  icon-button-size     standalone icon buttons must use w-10 h-10, not w-9 or w-11
  modal-button-font    flex-1 modal footer buttons must have font-medium/semibold
  page-heading-color   h2 with text-3xl font-bold must include text-gray-800

Accessibility (prevent silent a11y regressions):
  icon-btn-label       single-line icon-only buttons need title= or aria-label=
  input-focus-ring     inputs/selects with border class must have focus:ring-2
  img-alt              every <img> must carry an alt= attribute

Run:  python ui_lint.py
Exit: 0 = clean, 1 = violations found
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "app" / "templates"

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
    """h2 page titles (text-3xl font-bold) must carry text-gray-800."""
    violations = []
    if "<h2" not in line:
        return violations
    for cls in _class_sets(line):
        if "text-3xl" in cls and "font-bold" in cls and "text-gray-800" not in cls:
            violations.append(f"{loc}: [page-heading-color] h2 with text-3xl font-bold is missing text-gray-800")
    return violations


def _check_icon_btn_label(line: str, loc: str) -> list[str]:
    """Single-line icon-only <button> must have title= or aria-label=."""
    violations = []
    if "<button" not in line or "</button>" not in line:
        return violations
    if '<i class="fa' not in line:
        return violations
    # Only flag when the button has no readable text (icon-only)
    if _button_text(line):
        return violations
    if "title=" not in line and "aria-label=" not in line:
        violations.append(
            f"{loc}: [icon-btn-label] icon-only button has no title= or aria-label= — add one for accessibility"
        )
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


# ── Dispatcher ────────────────────────────────────────────────────────────────

_RULES = [
    _check_badge_font_medium,
    _check_icon_button_size,
    _check_modal_button_font,
    _check_page_heading_color,
    _check_icon_btn_label,
    _check_input_focus_ring,
    _check_img_alt,
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
    return violations


def lint_dir(templates_dir: Path = TEMPLATES_DIR) -> list[str]:
    violations: list[str] = []
    for path in sorted(templates_dir.rglob("*.html")):
        violations.extend(lint_file(path))
    return violations


def main() -> int:
    violations = lint_dir()
    n_files = sum(1 for _ in TEMPLATES_DIR.rglob("*.html"))
    if violations:
        print(f"\n── ui-lint: {len(violations)} violation(s) in {n_files} templates " + "─" * 20)
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print(f"── ui-lint: {n_files} templates OK " + "─" * 30)
    return 0


if __name__ == "__main__":
    sys.exit(main())
