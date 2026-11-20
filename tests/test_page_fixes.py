"""Notes, Import, Settings and sidebar fixes from the UX review."""


def test_notes_type_select_has_one_general_option(client):
    html = client.get("/notes").text
    select = html[html.index('id="note-type"') : html.index("</select>", html.index('id="note-type"'))]
    assert select.count(">General<") == 1
    assert 'value=""' not in select


def test_notes_filter_pill_uses_brand_and_untyped_notes_count_as_general(client):
    html = client.get("/notes").text
    assert ".filter-btn.active { background: var(--brand-solid)" in html
    assert "(n.type || 'general') === activeFilter" in html


def test_import_csv_section_is_on_brand(client):
    html = client.get("/import").text
    page = html[html.index("<main") :]  # base.html's dark-mode CSS still lists purple overrides
    assert "bg-purple-600" not in page and 'class="bg-purple-50' not in page
    assert 'aria-controls="csv-panel"' in html


def test_settings_tabs_follow_the_url_hash_and_guard_unsaved_changes(client):
    html = client.get("/settings").text
    assert "@hashchange.window=" in html
    assert "You have unsaved changes on this tab" in html
    assert "beforeunload" in html


def test_theme_control_is_a_labelled_switch(client):
    html = client.get("/").text
    assert 'role="switch"' in html
    assert "Dark mode" in html
    assert "Night Mode" not in html


def test_paste_to_import_is_wired_on_import_and_review(client):
    assert "window.imagesFromPaste = function(e)" in client.get("/").text
    imp = client.get("/import").text
    assert "window.addEventListener('paste'" in imp and "stageFiles(files)" in imp
    rev = client.get("/review").text
    assert "window.addEventListener('paste'" in rev and "/api/import/jobs" in rev


def test_keyboard_shortcuts_sheet_and_reduced_motion(client):
    html = client.get("/").text
    assert 'id="shortcuts-dialog" role="dialog"' in html
    assert "if (e.key === '?')" in html and "window.openAddModal()" in html
    assert "@media (prefers-reduced-motion: reduce)" in html
    assert "prefers-reduced-motion: reduce)').matches ? false" in html  # Chart.js animation off
