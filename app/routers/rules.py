"""CRUD for transaction rules."""

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.database import TransactionRule, get_db

router = APIRouter()

VALID_FIELDS = {"description", "amount", "payment_method", "source", "payee_id", "type"}
VALID_OPS = {"equals", "contains", "regex", "range", "in", "gt", "lt"}


class RuleCreate(BaseModel):
    name: str
    is_active: bool = True
    priority: int = 0
    match_field: str
    match_op: str
    match_value: str
    action_json: dict


class RuleUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None
    match_field: Optional[str] = None
    match_op: Optional[str] = None
    match_value: Optional[str] = None
    action_json: Optional[dict] = None


def _validate(field: str, op: str):
    if field not in VALID_FIELDS:
        raise HTTPException(422, f"match_field must be one of: {', '.join(sorted(VALID_FIELDS))}")
    if op not in VALID_OPS:
        raise HTTPException(422, f"match_op must be one of: {', '.join(sorted(VALID_OPS))}")


@router.get("/")
def list_rules(db: Session = Depends(get_db)):
    rules = db.query(TransactionRule).order_by(TransactionRule.priority.asc(), TransactionRule.id.asc()).all()
    return [_to_dict(r) for r in rules]


@router.post("/", status_code=201)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)):
    _validate(payload.match_field, payload.match_op)
    rule = TransactionRule(
        name=payload.name,
        is_active=payload.is_active,
        priority=payload.priority,
        match_field=payload.match_field,
        match_op=payload.match_op,
        match_value=payload.match_value,
        action_json=json.dumps(payload.action_json),
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _to_dict(rule)


@router.put("/{rule_id}")
def update_rule(rule_id: int, payload: RuleUpdate, db: Session = Depends(get_db)):
    rule = _get(rule_id, db)
    if payload.name is not None:
        rule.name = payload.name
    if payload.is_active is not None:
        rule.is_active = payload.is_active
    if payload.priority is not None:
        rule.priority = payload.priority
    if payload.match_field is not None or payload.match_op is not None:
        field = payload.match_field or rule.match_field
        op = payload.match_op or rule.match_op
        _validate(field, op)
        rule.match_field = field
        rule.match_op = op
    if payload.match_value is not None:
        rule.match_value = payload.match_value
    if payload.action_json is not None:
        rule.action_json = json.dumps(payload.action_json)
    db.commit()
    db.refresh(rule)
    return _to_dict(rule)


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = _get(rule_id, db)
    db.delete(rule)
    db.commit()


def _get(rule_id: int, db: Session) -> TransactionRule:
    rule = db.query(TransactionRule).filter(TransactionRule.id == rule_id).first()
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule


def _to_dict(r: TransactionRule) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "is_active": r.is_active,
        "priority": r.priority,
        "match_field": r.match_field,
        "match_op": r.match_op,
        "match_value": r.match_value,
        "action_json": json.loads(r.action_json or "{}"),
        "match_count": r.match_count,
        "last_matched_at": r.last_matched_at.isoformat() if r.last_matched_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
