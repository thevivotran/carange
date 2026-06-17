"""
Tests for ocr_worker.learned_parser_store — CRUD, sandbox safety, script validation,
and the human approval gate (Option A security model).
"""

from ocr_worker.learned_parser_store import save, lookup, run_parser
from ocr_worker.types import TextBlock


def test_save_and_lookup_requires_approval(db_session):
    save(db_session, "test_bank", ["TestBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    # Not approved yet — lookup must return None
    result = lookup(db_session, "testbank hello world")
    assert result is None


def test_lookup_returns_result_when_approved(db_session):
    from app.models.database import LearnedParser

    save(db_session, "approved_bank", ["ApprovedBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    row = db_session.query(LearnedParser).filter(LearnedParser.source_name == "approved_bank").first()
    row.is_approved = True
    db_session.commit()

    result = lookup(db_session, "approvedbank transaction here")
    assert result is not None
    assert result.source_name == "approved_bank"

    no_result = lookup(db_session, "unrelated text")
    assert no_result is None


def test_hit_count_increments_on_lookup(db_session):
    from app.models.database import LearnedParser

    save(db_session, "hit_bank", ["HitBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    row = db_session.query(LearnedParser).filter(LearnedParser.source_name == "hit_bank").first()
    row.is_approved = True
    db_session.commit()

    result = lookup(db_session, "hitbank transaction here")
    assert result is not None
    db_session.commit()

    row = db_session.query(LearnedParser).filter(LearnedParser.source_name == "hit_bank").first()
    assert row.hit_count == 1


def test_run_parser_valid_script():
    script = (
        "def parse(blocks):\n"
        "    return [ParsedTransaction(\n"
        "        date=date(2026, 5, 15),\n"
        "        amount=50000,\n"
        "        tx_type='expense',\n"
        "        description='test',\n"
        "        confidence=0.9,\n"
        "    )]\n"
    )
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is not None
    assert len(result) == 1
    assert result[0].amount == 50000
    assert result[0].tx_type == "expense"


def test_run_parser_empty_list_returns_none():
    script = "def parse(blocks):\n    return []"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_run_parser_sandbox_rejects_open():
    # AST check rejects call to `open`
    script = "def parse(blocks):\n    f = open('/etc/passwd')\n    return []"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_run_parser_sandbox_rejects_import():
    # AST check rejects `__import__` call
    script = "def parse(blocks):\n    os = __import__('os')\n    return []"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_run_parser_sandbox_rejects_import_statement():
    # AST check rejects `import` statement
    script = "import os\ndef parse(blocks):\n    return []"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_run_parser_sandbox_rejects_dunder_class_escape():
    # AST check rejects __class__ attribute access (sandbox escape vector)
    script = "def parse(blocks):\n    x = ''.__class__.__mro__[1].__subclasses__()\n    return []"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_run_parser_syntax_error_returns_none():
    script = "def parse(blocks):\n    return !!!"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None


def test_save_duplicate_source_name_upserts(db_session):
    save(db_session, "upsert_bank", ["UpsertBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    save(db_session, "upsert_bank", ["UpsertBank", "NewKeyword"], "def parse(blocks):\n    return [1]")
    db_session.commit()

    from app.models.database import LearnedParser

    rows = db_session.query(LearnedParser).filter(LearnedParser.source_name == "upsert_bank").all()
    assert len(rows) == 1
    assert "NewKeyword" in rows[0].detection_keywords


def test_new_parser_defaults_to_unapproved(db_session):
    from app.models.database import LearnedParser

    save(db_session, "pending_bank", ["PendingBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    row = db_session.query(LearnedParser).filter(LearnedParser.source_name == "pending_bank").first()
    assert row.is_approved is False


def test_unapproved_parser_not_returned_by_lookup(db_session):
    save(db_session, "invisible_bank", ["InvisibleBank"], "def parse(blocks):\n    return []")
    db_session.commit()

    result = lookup(db_session, "invisiblebank statement")
    assert result is None


def test_run_parser_timeout_returns_none():
    # Script that runs forever — should be killed by signal.alarm
    script = "def parse(blocks):\n    while True: pass"
    blocks = [TextBlock(text="test", confidence=0.9, x=0, y=0, w=10, h=10)]
    result = run_parser(script, blocks)
    assert result is None
