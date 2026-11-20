"""next_due_payment: the payment a project card shows as "next"."""

from datetime import date
from types import SimpleNamespace

from app.models.database import PaymentStatus
from app.services.project_service import next_due_payment


def _pay(id_, due=None, status=PaymentStatus.PENDING, order=0):
    return SimpleNamespace(id=id_, due_date=due, status=status, sort_order=order, amount=1)


def _project(*payments):
    return SimpleNamespace(payments=list(payments))


def test_no_payments_or_all_paid():
    assert next_due_payment(_project()) is None
    assert next_due_payment(_project(_pay(1, date(2026, 1, 1), PaymentStatus.PAID))) is None


def test_earliest_dated_pending_wins_over_undated_and_paid():
    early_paid = _pay(1, date(2026, 1, 1), PaymentStatus.PAID)
    later = _pay(2, date(2026, 6, 1))
    sooner = _pay(3, date(2026, 3, 1))
    undated = _pay(4, None, order=0)
    assert next_due_payment(_project(early_paid, later, sooner, undated)) is sooner


def test_undated_pending_falls_back_to_schedule_order():
    second = _pay(5, None, order=2)
    first = _pay(6, None, order=1)
    assert next_due_payment(_project(second, first)) is first


def test_project_card_shows_next_payment(client, db_session):
    from app.models.database import FinancialProject, ProjectPayment, ProjectType

    proj = FinancialProject(name="Flat", type=ProjectType.REAL_ESTATE, target_amount=500_000_000, current_amount=0)
    db_session.add(proj)
    db_session.commit()
    db_session.add(ProjectPayment(project_id=proj.id, amount=200_000_000, due_date=date(2027, 2, 3)))
    db_session.commit()
    html = client.get("/fragments/projects/grid").text
    assert "Next payment" in html
    assert "3 Feb 2027" in html


# ── Across projects (Dashboard / Pulse) ──────────────────────────────────────


def _mk_project(db, name, deleted=False):
    from datetime import datetime, timezone

    from app.models.database import FinancialProject, ProjectType

    p = FinancialProject(
        name=name,
        type=ProjectType.REAL_ESTATE,
        target_amount=1,
        current_amount=0,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
    )
    db.add(p)
    db.commit()
    return p


def test_next_due_payment_overall(db_session):
    from app.models.database import ProjectPayment
    from app.services.project_service import next_due_payment_overall

    today = date(2026, 11, 20)
    assert next_due_payment_overall(db_session, today=today) is None

    flat, car, gone = (
        _mk_project(db_session, "Flat"),
        _mk_project(db_session, "Car"),
        _mk_project(db_session, "Gone", True),
    )
    db_session.add_all(
        [
            ProjectPayment(project_id=flat.id, amount=1, due_date=date(2026, 11, 1)),  # past
            ProjectPayment(project_id=flat.id, amount=2, due_date=date(2026, 11, 22), status=PaymentStatus.PAID),
            ProjectPayment(project_id=gone.id, amount=3, due_date=date(2026, 11, 21)),  # deleted project
            ProjectPayment(project_id=car.id, amount=4, due_date=date(2026, 11, 25)),
            ProjectPayment(project_id=flat.id, amount=5, due_date=date(2026, 12, 5)),
            ProjectPayment(project_id=flat.id, amount=6, due_date=None),
        ]
    )
    db_session.commit()
    nxt = next_due_payment_overall(db_session, today=today)
    assert nxt["project_name"] == "Car" and float(nxt["amount"]) == 4 and nxt["days"] == 5


def test_dashboard_and_pulse_show_next_payment(client, db_session):
    from app.models.database import ProjectPayment

    p = _mk_project(db_session, "Flat")
    db_session.add(ProjectPayment(project_id=p.id, amount=7_000_000, due_date=date.today()))
    db_session.commit()
    for path in ("/", "/pulse"):
        html = client.get(path).text
        assert "Next project payment" in html and "today" in html
