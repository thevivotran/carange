"""Undo-delete can't lose work: soft-deletable records delete immediately and Undo restores
them; hard deletes are flushed with keepalive if the page is left inside the undo window."""


def test_undo_helper_flushes_pending_deletes_on_page_hide(client):
    html = client.get("/").text
    assert "function deleteWithUndo(element, deleteUrl, reloadFn, itemName, delay, restoreUrl)" in html
    assert "window.addEventListener('pagehide', _flushPendingDeletes)" in html
    assert "keepalive: true" in html
    assert "var _UNDO_MS = 10000;" in html
    # Once flushed, Undo can't cancel the delete, so its toast must go too.
    assert "Alpine.store('toasts').remove(p.timer.toastId);" in html
    assert "timer.toastId = toastId;" in html


def test_undo_toast_pauses_on_hover_and_focus(client):
    html = client.get("/").text
    assert '@mouseenter="toast._pause && toast._pause()"' in html
    assert '@focusin="toast._pause && toast._pause()"' in html


def test_soft_deletable_pages_pass_restore_urls(client):
    assert "'/api/transactions/' + t.id + '/restore'" in client.get("/transactions").text
    assert "'/api/savings/' + id + '/restore'" in client.get("/savings").text
