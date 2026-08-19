import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.migrate import run_migrations
from app.models import DMJob, DuplicateBlock, Rule
from app.schemas import RuleCreate, RuleResponse, StatsResponse, WebhookPayload
from app.security import signature_is_valid
from app.services import create_rule, record_webhook


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_migrations()
    yield


app = FastAPI(title="PseudoGram Automation", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def post_rule(payload: RuleCreate, session: Session = Depends(get_db)) -> RuleResponse:
    rule = create_rule(session, keyword=payload.keyword, dm_message=payload.dm_message)
    return RuleResponse(rule_id=rule.id, keyword=rule.keyword, dm_message=rule.dm_message)


@app.post("/webhook")
async def post_webhook(
    request: Request,
    x_pseudogram_signature: str | None = Header(default=None),
    session: Session = Depends(get_db),
) -> Response:
    raw_body = await request.body()
    if not signature_is_valid(raw_body, x_pseudogram_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook signature")
    try:
        raw_payload = json.loads(raw_body)
        payload = WebhookPayload.model_validate(raw_payload)
        record_webhook(session, payload, raw_payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_200_OK)


@app.get("/stats", response_model=StatsResponse)
def get_stats(session: Session = Depends(get_db)) -> StatsResponse:
    def count_jobs(*states: str) -> int:
        return int(session.scalar(select(func.count()).select_from(DMJob).where(DMJob.status.in_(states))) or 0)

    return StatsResponse(
        sent=count_jobs("delivered"),
        failed=count_jobs("failed"),
        queued=count_jobs("queued", "sending", "awaiting_delivery"),
        duplicates_blocked=int(session.scalar(select(func.count()).select_from(DuplicateBlock)) or 0),
    )

