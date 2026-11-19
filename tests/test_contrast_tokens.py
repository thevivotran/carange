"""WCAG AA contrast guard for the colour tokens.

Colours are defined twice — the Tailwind CDN config inline in base.html and the CSS custom
properties in carange-tokens.css — so this test reads both and asserts every text token
clears 4.5:1 against the surfaces it sits on.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BASE_HTML = (ROOT / "app/templates/base.html").read_text()
TOKENS_CSS = (ROOT / "app/static/css/carange-tokens.css").read_text()

AA = 4.5
WHITE = "#FFFFFF"
SLATE_50 = "#F8FAFC"
SLATE_800 = "#1E293B"  # dark-mode card surface
BRAND_SUBTLE = "#FFF5ED"  # active nav background


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def _tailwind_colours() -> dict[str, str]:
    """Flatten the `colors: {...}` block of the inline Tailwind config."""
    block = BASE_HTML[BASE_HTML.index("tailwind.config") :]
    block = block[block.index("colors:") : block.index("fontFamily")]
    colours: dict[str, str] = {}
    group = None
    for line in block.splitlines():
        line = re.sub(r"/\*.*?\*/", "", line).strip()
        m = re.match(r"([\w'-]+):\s*\{", line)
        if m:
            group = m.group(1).strip("'")
            continue
        if line.startswith("}"):
            group = None
            continue
        m = re.match(r"([\w'-]+):\s*'(#[0-9A-Fa-f]{6})'", line)
        if m:
            key = m.group(1).strip("'")
            name = group if key == "DEFAULT" else (f"{group}-{key}" if group else key)
            colours[name] = m.group(2).upper()
    return colours


def _dark_override(cls: str) -> str:
    m = re.search(r"\.dark \.%s\s*\{\s*color:\s*(#[0-9A-Fa-f]{6})" % re.escape(cls), BASE_HTML)
    assert m, f"missing .dark .{cls} override in base.html"
    return m.group(1).upper()


def _css_vars() -> dict[str, str]:
    root = TOKENS_CSS[: TOKENS_CSS.index('[data-theme="dark"]')]
    raw = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", root))

    def resolve(value: str) -> str:
        value = value.strip()
        m = re.fullmatch(r"var\((--[\w-]+)\)", value)
        return resolve(raw[m.group(1)]) if m else value.upper()

    return {k: resolve(v) for k, v in raw.items() if k.startswith("--")}


TW = _tailwind_colours()
CSS = _css_vars()

LIGHT_PAIRS = [
    ("text-muted", WHITE),
    ("text-muted", SLATE_50),
    ("text-secondary", WHITE),
    ("income", WHITE),
    ("income", SLATE_50),
    ("expense", WHITE),
    ("success", WHITE),
    ("danger", WHITE),
    ("danger", "#FEF2F2"),
    ("warning", WHITE),
    ("warning", "#FFFBEB"),
    ("brand-text", WHITE),
    ("brand-text", BRAND_SUBTLE),
    ("accent-text", WHITE),
]


@pytest.mark.parametrize("token,surface", LIGHT_PAIRS)
def test_light_text_tokens_meet_aa(token, surface):
    ratio = contrast(TW[token], surface)
    assert ratio >= AA, f"{token} {TW[token]} on {surface} is {ratio:.2f}:1"


def test_white_on_solid_brand_button_meets_aa():
    assert contrast(WHITE, TW["brand-solid"]) >= AA
    assert contrast(WHITE, TW["brand-solid-hover"]) >= AA


@pytest.mark.parametrize(
    "cls",
    [
        "text-income",
        "text-expense",
        "text-success",
        "text-danger",
        "text-warning",
        "text-text-muted",
        "text-brand-text",
    ],
)
def test_dark_text_overrides_meet_aa(cls):
    colour = _dark_override(cls)
    ratio = contrast(colour, SLATE_800)
    assert ratio >= AA, f".dark .{cls} {colour} on {SLATE_800} is {ratio:.2f}:1"


@pytest.mark.parametrize(
    "tw_name,css_var",
    [
        ("text-muted", "--text-muted"),
        ("income", "--income"),
        ("success", "--success"),
        ("warning", "--warning"),
        ("danger", "--danger"),
        ("brand-text", "--brand-text"),
        ("brand-solid", "--brand-solid"),
    ],
)
def test_tailwind_and_css_tokens_agree(tw_name, css_var):
    assert TW[tw_name] == CSS[css_var], f"{tw_name}={TW[tw_name]} but {css_var}={CSS[css_var]}"


def test_contrast_helper():
    assert round(contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert contrast("#FFFFFF", "#FFFFFF") == 1.0
