from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.models.database import get_db, OtherAsset, AssetType
from app.models.schemas import OtherAsset as OtherAssetSchema, OtherAssetCreate, OtherAssetUpdate

router = APIRouter()


@router.get("/", response_model=List[OtherAssetSchema])
def get_assets(
    asset_type: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    query = db.query(OtherAsset)
    if asset_type:
        query = query.filter(OtherAsset.asset_type == asset_type)
    return query.order_by(OtherAsset.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/stats/summary")
def get_assets_summary(db: Session = Depends(get_db)):
    assets = db.query(OtherAsset).all()
    total_invested = sum(a.purchase_price_vnd for a in assets)
    total_current = sum(a.current_value_vnd for a in assets)
    gain_loss = total_current - total_invested
    gain_loss_pct = round((gain_loss / total_invested * 100), 2) if total_invested > 0 else 0
    return {
        "total_assets_count": len(assets),
        "total_invested_vnd": total_invested,
        "total_current_value_vnd": total_current,
        "total_gain_loss_vnd": gain_loss,
        "total_gain_loss_pct": gain_loss_pct,
    }


@router.get("/{asset_id}", response_model=OtherAssetSchema)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.query(OtherAsset).filter(OtherAsset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.post("/", response_model=OtherAssetSchema)
def create_asset(asset: OtherAssetCreate, db: Session = Depends(get_db)):
    db_asset = OtherAsset(**asset.model_dump())
    db.add(db_asset)
    db.commit()
    db.refresh(db_asset)
    return db_asset


@router.put("/{asset_id}", response_model=OtherAssetSchema)
def update_asset(asset_id: int, asset_update: OtherAssetUpdate, db: Session = Depends(get_db)):
    db_asset = db.query(OtherAsset).filter(OtherAsset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    for key, value in asset_update.model_dump(exclude_unset=True).items():
        setattr(db_asset, key, value)
    db.commit()
    db.refresh(db_asset)
    return db_asset


@router.delete("/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    db_asset = db.query(OtherAsset).filter(OtherAsset.id == asset_id).first()
    if not db_asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    db.delete(db_asset)
    db.commit()
    return {"message": "Asset deleted successfully"}
