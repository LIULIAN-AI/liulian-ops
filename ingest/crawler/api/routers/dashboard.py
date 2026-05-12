from __future__ import annotations

from fastapi import APIRouter, Query

from crawler.api.dependencies import ObservabilityDep
from crawler.hardening import DashboardSummary, summarize_dashboard

router = APIRouter(prefix="/admin/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardSummary)
async def dashboard_summary(
    observability_sink: ObservabilityDep,
    stale_after_seconds: int = Query(default=86_400, ge=1),
) -> DashboardSummary:
    return summarize_dashboard(
        observability_sink,
        stale_after_seconds=stale_after_seconds,
    )
