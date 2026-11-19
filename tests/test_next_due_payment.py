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
