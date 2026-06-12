from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.services.forecast_service import build_forecast

router = APIRouter()


@router.get("/data")
def forecast_data(horizon: int = 90, db: Session = Depends(get_db)):
    horizon = max(7, min(365, horizon))  # clamp
    f = build_forecast(db, horizon_days=horizon)
    return {
        **f,
        "start_date": f["start_date"].isoformat(),
        "end_date": f["end_date"].isoformat(),
        "events": [{**e, "date": e["date"].isoformat()} for e in f["events"]],
        "series": [{"date": p["date"].isoformat(), "balance": p["balance"]} for p in f["series"]],
        "low_point": {**f["low_point"], "date": f["low_point"]["date"].isoformat()} if f["low_point"] else None,
        "shortfall": {
            **f["shortfall"],
            "date": f["shortfall"]["date"].isoformat() if f["shortfall"]["date"] else None,
        },
    }
