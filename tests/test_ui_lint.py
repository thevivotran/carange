"""Tests for the ui_lint design-token linter.

Two layers:
  1. Unit tests — verify each rule fires/passes on synthetic HTML snippets.
  2. Integration test — run the linter against the real template directory
     and assert zero violations (this is the guard that blocks new regressions).
"""

from pathlib import Path

import pytest

from ui_lint import TEMPLATES_DIR, lint_dir, lint_line

# ── badge-font-medium ─────────────────────────────────────────────────────────


def test_badge_missing_font_medium_is_flagged():
    line = '<span class="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700">On track</span>'
    assert any("badge-font-medium" in v for v in lint_line(line, Path("t.html"), 1))


def test_badge_with_font_medium_passes():
    line = '<span class="text-xs font-medium px-2 py-0.5 rounded-full bg-green-100 text-green-700">On track</span>'
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
    line = '<span class="text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-green-200">↑ flat</span>'
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
    line = '<button onclick="doThing()" class="text-gray-400 hover:text-gray-600"><i class="fas fa-times"></i></button>'
    assert any("icon-btn-label" in v for v in lint_line(line, Path("t.html"), 1))


def test_icon_btn_with_title_passes():
    line = '<button onclick="doThing()" title="Close" class="text-gray-400"><i class="fas fa-times"></i></button>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_icon_btn_with_aria_label_passes():
    line = '<button onclick="doThing()" aria-label="Close" class="text-gray-400"><i class="fas fa-times"></i></button>'
    assert lint_line(line, Path("t.html"), 1) == []


def test_icon_btn_with_text_not_flagged():
    """Button with visible text does not need aria-label."""
    line = (
        '<button class="bg-primary text-white px-4 py-2 rounded-lg">'
        '<i class="fas fa-plus mr-2"></i>Add Transaction</button>'
    )
    assert lint_line(line, Path("t.html"), 1) == []


def test_icon_btn_multiline_not_checked():
    """Multi-line buttons (no </button> on same line) are skipped."""
    line = '<button class="w-10 h-10 rounded-lg">'
    assert lint_line(line, Path("t.html"), 1) == []


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
