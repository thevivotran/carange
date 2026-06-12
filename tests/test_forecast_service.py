from datetime import date, timedelta

from app.models.database import (
    BudgetAllocation,
    FinancialProject,
    PaymentStatus,
    ProjectPayment,
    ProjectStatus,
    ProjectType,
    SavingsBundle,
    SavingsStatus,
    SavingsType,
    TransactionTemplate,
    TransactionType,
)
from app.services.fiscal_period import current_period_label, get_month_start_day
from app.services.forecast_service import build_forecast
from app.services.settings_service import set_setting


def test_empty_db_no_events_single_series_point(db_session):
    result = build_forecast(db_session, horizon_days=90)

    assert result["events"] == []
    assert len(result["series"]) == 1
    assert result["series"][0]["date"] == date.today()
    assert result["series"][0]["balance"] == result["starting_balance"]
    assert result["horizon_net"] == 0
    assert result["shortfall"]["breached"] is False


def test_template_projects_n_occurrences(db_session, expense_cat):
    today = date.today()
    tmpl = TransactionTemplate(
        name="Rent",
        amount=1_000_000,
        type=TransactionType.EXPENSE,
        category_id=expense_cat.id,
        is_active=True,
        cadence="monthly",
        next_run_at=today,
    )
    db_session.add(tmpl)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    tmpl_events = [e for e in result["events"] if e["source"] == "template"]
    # monthly cadence starting today over a 90-day horizon -> 4 occurrences
    # (day 0, +1mo, +2mo, +3mo all <= 90 days roughly)
    assert len(tmpl_events) >= 3
    for e in tmpl_events:
        assert e["signed"] == -1_000_000.0
        assert e["amount"] == 1_000_000.0
        assert e["entity_id"] == tmpl.id
        assert e["estimated"] is False


def test_income_template_is_positive(db_session, income_cat):
    today = date.today()
    tmpl = TransactionTemplate(
        name="Salary",
        amount=20_000_000,
        type=TransactionType.INCOME,
        category_id=income_cat.id,
        is_active=True,
        cadence="monthly",
        next_run_at=today,
    )
    db_session.add(tmpl)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=10)

    tmpl_events = [e for e in result["events"] if e["source"] == "template"]
    assert len(tmpl_events) == 1
    assert tmpl_events[0]["signed"] == 20_000_000.0


def test_pending_payment_in_window_is_negative_event(db_session):
    today = date.today()
    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    payment = ProjectPayment(
        project_id=project.id,
        due_date=today + timedelta(days=10),
        amount=50_000_000,
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    pay_events = [e for e in result["events"] if e["source"] == "project_payment"]
    assert len(pay_events) == 1
    assert pay_events[0]["signed"] == -50_000_000.0
    assert pay_events[0]["label"] == "Apartment: payment"
    assert pay_events[0]["entity_id"] == payment.id
    assert pay_events[0]["date"] == today + timedelta(days=10)


def test_paid_payment_not_included(db_session):
    today = date.today()
    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    payment = ProjectPayment(
        project_id=project.id,
        due_date=today + timedelta(days=10),
        amount=50_000_000,
        status=PaymentStatus.PAID,
    )
    db_session.add(payment)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    pay_events = [e for e in result["events"] if e["source"] == "project_payment"]
    assert pay_events == []


def test_savings_maturity_is_positive_event(db_session):
    today = date.today()
    bundle = SavingsBundle(
        name="Term Deposit",
        bank_name="VCB",
        type=SavingsType.FIXED_DEPOSIT,
        initial_deposit=10_000_000,
        current_amount=10_000_000,
        future_amount=10_500_000,
        interest_rate=5.0,
        start_date=today - timedelta(days=30),
        maturity_date=today + timedelta(days=20),
        status=SavingsStatus.ACTIVE,
    )
    db_session.add(bundle)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    mat_events = [e for e in result["events"] if e["source"] == "savings_maturity"]
    assert len(mat_events) == 1
    assert mat_events[0]["signed"] == 10_500_000.0
    assert mat_events[0]["label"] == "Term Deposit matures"
    assert mat_events[0]["entity_id"] == bundle.id


def test_low_point_detection(db_session):
    today = date.today()
    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    payment = ProjectPayment(
        project_id=project.id,
        due_date=today + timedelta(days=5),
        amount=99_999_999_999,
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    low_point = result["low_point"]
    assert low_point["date"] == today + timedelta(days=5)
    assert low_point["balance"] == result["starting_balance"] - 99_999_999_999.0


def test_shortfall_true_when_balance_dips_below_buffer(db_session):
    today = date.today()
    set_setting(db_session, "forecast_buffer", "0")

    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    payment = ProjectPayment(
        project_id=project.id,
        due_date=today + timedelta(days=5),
        amount=99_999_999_999,
        status=PaymentStatus.PENDING,
    )
    db_session.add(payment)
    db_session.commit()

    result = build_forecast(db_session, horizon_days=90)

    assert result["buffer"] == 0.0
    assert result["shortfall"]["breached"] is True
    assert result["shortfall"]["date"] == today + timedelta(days=5)


def test_boundary_event_on_end_included_one_day_past_excluded(db_session):
    today = date.today()
    horizon_days = 30
    end = today + timedelta(days=horizon_days)

    project = FinancialProject(
        name="Apartment",
        type=ProjectType.REAL_ESTATE,
        status=ProjectStatus.IN_PROGRESS,
        target_amount=1_000_000_000,
    )
    db_session.add(project)
    db_session.commit()

    on_end = ProjectPayment(
        project_id=project.id,
        due_date=end,
        amount=1_000_000,
        status=PaymentStatus.PENDING,
    )
    past_end = ProjectPayment(
        project_id=project.id,
        due_date=end + timedelta(days=1),
        amount=2_000_000,
        status=PaymentStatus.PENDING,
    )
    db_session.add_all([on_end, past_end])
    db_session.commit()

    result = build_forecast(db_session, horizon_days=horizon_days)

    pay_dates = {e["date"] for e in result["events"] if e["source"] == "project_payment"}
    assert end in pay_dates
    assert (end + timedelta(days=1)) not in pay_dates


def test_budget_headroom_estimate_events(db_session, expense_cat):
    day = get_month_start_day(db_session)
    label = current_period_label(date.today(), day)

    allocation = BudgetAllocation(
        category_id=expense_cat.id,
        year_month=label,
        amount=1_000_000,
    )
    db_session.add(allocation)
    db_session.commit()

    with_estimate = build_forecast(db_session, horizon_days=90, include_budget_estimate=True)
    without_estimate = build_forecast(db_session, horizon_days=90, include_budget_estimate=False)

    estimate_events = [e for e in with_estimate["events"] if e["source"] == "budget_estimate"]
    assert len(estimate_events) >= 1
    for e in estimate_events:
        assert e["estimated"] is True
        assert e["entity_id"] == expense_cat.id

    assert without_estimate["events"] == []
    assert with_estimate["low_point"]["balance"] <= without_estimate["low_point"]["balance"]
