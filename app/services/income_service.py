"""Income source and compensation event domain business logic."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, joinedload

from app.models.database import CompensationEvent, CompensationEventType, IncomeSource

REVIEW_OVERDUE_DAYS = 365


def is_review_overdue(income_source: IncomeSource) -> bool:
    """True when there's no `review`-type event in the last 365 days."""
    if not income_source.is_active:
        return False
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=REVIEW_OVERDUE_DAYS)
    return not any(
        e.event_type == CompensationEventType.REVIEW and e.event_date >= cutoff for e in income_source.events
    )


def attach_review_overdue(income_source: IncomeSource) -> IncomeSource:
    income_source.review_overdue = is_review_overdue(income_source)
    return income_source


def get_income_sources(db: Session, user_id: int | None = None) -> list[IncomeSource]:
    query = db.query(IncomeSource).options(joinedload(IncomeSource.events))
    if user_id is not None:
        query = query.filter(IncomeSource.user_id == user_id)
    sources = query.order_by(IncomeSource.created_at.desc()).all()
    for s in sources:
        attach_review_overdue(s)
    return sources


def get_income_source(db: Session, income_source_id: int) -> IncomeSource | None:
    source = (
        db.query(IncomeSource)
        .options(joinedload(IncomeSource.events))
        .filter(IncomeSource.id == income_source_id)
        .first()
    )
    if source is not None:
        attach_review_overdue(source)
    return source


def create_income_source(db: Session, data) -> IncomeSource:
    db_source = IncomeSource(**data.model_dump())
    db.add(db_source)
    db.commit()
    db.refresh(db_source)
    return attach_review_overdue(db_source)


def update_income_source(db: Session, source: IncomeSource, data) -> IncomeSource:
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(source, key, value)
    db.commit()
    db.refresh(source)
    return attach_review_overdue(source)


def delete_income_source(db: Session, source: IncomeSource) -> None:
    db.delete(source)
    db.commit()


def get_events(db: Session, income_source_id: int) -> list[CompensationEvent]:
    return (
        db.query(CompensationEvent)
        .filter(CompensationEvent.income_source_id == income_source_id)
        .order_by(CompensationEvent.event_date.desc(), CompensationEvent.id.desc())
        .all()
    )


def create_event(db: Session, income_source: IncomeSource, data) -> CompensationEvent:
    event = CompensationEvent(income_source_id=income_source.id, **data.model_dump())
    db.add(event)
    if data.new_base_amount is not None:
        income_source.base_amount_monthly = data.new_base_amount
    db.commit()
    db.refresh(event)
    return event


def update_event(db: Session, event: CompensationEvent, data) -> CompensationEvent:
    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    return event


def delete_event(db: Session, event: CompensationEvent) -> None:
    db.delete(event)
    db.commit()
