from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from crawler.api.dependencies import ReviewQueueDep
from crawler.hardening import CreateReviewQueueItem, ReviewQueueItem, ReviewStatus

router = APIRouter(prefix="/review/queue", tags=["review"])


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: tuple[ReviewQueueItem, ...] = ()


class ReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: Literal["approved", "rejected", "skipped"]
    reason: str | None = Field(default=None, max_length=500)
    reviewer: str | None = Field(default=None, max_length=100)


@router.get("", response_model=ReviewQueueResponse)
async def list_review_queue(
    store: ReviewQueueDep,
    status: ReviewStatus | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> ReviewQueueResponse:
    return ReviewQueueResponse(items=store.list(status=status, limit=limit))


@router.post("", response_model=ReviewQueueItem)
async def create_review_item(store: ReviewQueueDep, request: CreateReviewQueueItem) -> ReviewQueueItem:
    return store.create(request)


@router.post("/{item_id}/decision", response_model=ReviewQueueItem)
async def decide_review_item(
    item_id: str,
    request: ReviewDecisionRequest,
    store: ReviewQueueDep,
) -> ReviewQueueItem:
    item = store.decide(
        item_id,
        decision=request.decision,
        reason=request.reason,
        reviewer=request.reviewer,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"Review item not found: {item_id}")
    return item
