from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import OtherAsset, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


@router.get("/grid")
def fragment_assets_grid(
    request: Request,
    asset_type: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(OtherAsset)
    if asset_type:
        query = query.filter(OtherAsset.asset_type == asset_type)
    assets = query.order_by(OtherAsset.created_at.desc()).all()

    all_assets = db.query(OtherAsset).all()
    total_invested = sum(a.purchase_price_vnd for a in all_assets)
    total_current = sum(a.current_value_vnd for a in all_assets)
    gain_loss = total_current - total_invested
    gain_loss_pct = round(gain_loss / total_invested * 100, 2) if total_invested > 0 else 0

    return render_fragment(
        request,
        "partials/assets/_grid.html",
        {
            "assets": assets,
            "total_count": len(all_assets),
            "total_invested": total_invested,
            "total_current": total_current,
            "gain_loss": gain_loss,
            "gain_loss_pct": gain_loss_pct,
        },
    )
