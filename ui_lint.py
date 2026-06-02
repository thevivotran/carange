"""ui_lint.py — HTML template design-token linter.

Enforces three rules derived from the Carange design system:

  badge-font-medium   rounded-full + text-xs + tint bg → must have font-medium/semibold
  icon-button-size    standalone icon buttons must use w-10 h-10, not w-9 or w-11
  modal-button-font   flex-1 modal footer buttons must have font-medium/semibold

Run:  python ui_lint.py
Exit: 0 = clean, 1 = violations found
"""

import re
import sys
from pathlib import Path

TEMPLATES_DIR = Path(__file__).parent / "app" / "templates"

# Tint classes used as status-badge backgrounds (light shades only)
_TINT_BG = re.compile(
    r"^bg-(?:green|red|blue|amber|orange|purple|indigo|emerald|"
    r"yellow|pink|rose|violet|cyan|teal|gray)-(?:50|100|200)$"
)


def _class_sets(line: str) -> list[set[str]]:
    """Return one frozenset of class tokens per class="…" occurrence in line."""
    return [set(m.group(1).split()) for m in re.finditer(r'class=["\']([^"\']+)["\']', line)]


def lint_line(line: str, path: Path, lineno: int) -> list[str]:
    violations: list[str] = []
    loc = f"{path}:{lineno}"

    for cls in _class_sets(line):
        # ── badge-font-medium ─────────────────────────────────────────────
        # text-xs rounded-full + any light-tint bg → must declare font weight.
        # Exemption: skip if a dynamic `style=` sets background (category color dots).
        if (
            "rounded-full" in cls
            and "text-xs" in cls
            and any(_TINT_BG.match(c) for c in cls)
            and "style=" not in line
            and "font-medium" not in cls
            and "font-semibold" not in cls
        ):
            violations.append(
                f"{loc}: [badge-font-medium] text-xs rounded-full badge is missing font-medium — add font-medium"
            )

        # ── icon-button-size ──────────────────────────────────────────────
        # Standalone icon buttons must use w-10 h-10.
        # w-7 (pill-internal × close) is fine; w-9 and w-11 are not.
        if "<button" in line and "rounded-lg" in cls:
            if ("w-9" in cls and "h-9" in cls) or ("w-11" in cls and "h-11" in cls):
                bad = "w-9 h-9" if "w-9" in cls else "w-11 h-11"
                violations.append(f"{loc}: [icon-button-size] icon button uses {bad} — standardize to w-10 h-10")

        # ── modal-button-font ─────────────────────────────────────────────
        # flex-1 + px-4 + rounded-lg → modal footer button → needs font weight.
        if (
            "<button" in line
            and {"flex-1", "px-4", "rounded-lg"} <= cls
            and "font-medium" not in cls
            and "font-semibold" not in cls
        ):
            violations.append(f"{loc}: [modal-button-font] modal footer button is missing font-medium/semibold")

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
    banner = "─" * 50
    if violations:
        print(f"\n── ui-lint: {len(violations)} violation(s) in {n_files} templates {banner[:20]}")
        for v in violations:
            print(f"  ✗ {v}")
        return 1
    print(f"── ui-lint: {n_files} templates OK {banner[:28]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
