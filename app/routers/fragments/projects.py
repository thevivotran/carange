from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.models.database import FinancialProject, ProjectStatus, get_db
from app.routers.fragments._helpers import render_fragment

router = APIRouter()


def _calc_progress(project: FinancialProject) -> int:
    if project.target_amount and project.target_amount > 0:
        return min(100, round(project.current_amount / project.target_amount * 100))
    return 0


@router.get("/grid")
def fragment_projects_grid(
    request: Request,
    status: str = "",
    project_type: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(FinancialProject).filter(FinancialProject.deleted_at.is_(None))
    if status:
        query = query.filter(FinancialProject.status == status)
    if project_type:
        query = query.filter(FinancialProject.type == project_type)

    projects = query.order_by(FinancialProject.created_at.desc()).all()
    for p in projects:
        p.progress_pct = _calc_progress(p)

    active_projects = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status.in_([ProjectStatus.PLANNING, ProjectStatus.IN_PROGRESS]),
            FinancialProject.deleted_at.is_(None),
        )
        .all()
    )
    total_target = sum(p.target_amount for p in active_projects)
    total_current = sum(p.current_amount for p in active_projects)
    completed_count = (
        db.query(FinancialProject)
        .filter(
            FinancialProject.status == ProjectStatus.COMPLETED,
            FinancialProject.deleted_at.is_(None),
        )
        .count()
    )

    return render_fragment(
        request,
        "partials/projects/_project_grid.html",
        {
            "projects": projects,
            "active_count": len(active_projects),
            "completed_count": completed_count,
            "total_current": total_current,
            "progress_pct": round(total_current / total_target * 100, 2) if total_target > 0 else 0,
        },
    )
