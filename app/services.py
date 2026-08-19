import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Comment, DMJob, DuplicateBlock, Rule, WebhookEvent
from app.schemas import WebhookPayload


def create_rule(session: Session, *, keyword: str, dm_message: str) -> Rule:
    rule = Rule(keyword=keyword, keyword_folded=keyword.casefold(), dm_message=dm_message)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def record_webhook(session: Session, payload: WebhookPayload, raw_payload: dict) -> bool:
    """Persist one webhook and create/cancel jobs. Returns False for an event redelivery."""
    if session.get(WebhookEvent, payload.event_id) is not None:
        return False

    try:
        session.add(
            WebhookEvent(
                event_id=payload.event_id,
                event_type=payload.event_type,
                comment_id=payload.data.comment_id,
                raw_payload=raw_payload,
            )
        )
        session.flush()

        if payload.event_type == "comment.created":
            _record_creation(session, payload)

        session.commit()
        return True
    except IntegrityError:
        # A concurrent delivery of the same event won the primary-key race.
        session.rollback()
        return False
    except Exception:
        session.rollback()
        raise


def _record_creation(session: Session, payload: WebhookPayload) -> None:
    data = payload.data
    if not data.text or not data.from_:
        raise ValueError("comment.created requires data.text and data.from.user_id")

    comment = session.get(Comment, data.comment_id)
    if comment is None:
        comment = Comment(id=data.comment_id, state="active", created_at_source=data.created_at)
        session.add(comment)
    else:
        # Part A does not act on comment.deleted events; an old tombstone must
        # not prevent a valid new comment.created event from being processed.
        comment.state = "active"

    text_folded = data.text.casefold()
    rules = session.scalars(select(Rule)).all()
    for rule in rules:
        if rule.keyword_folded not in text_folded:
            continue
        job_id = str(uuid.uuid4())
        job = DMJob(
            id=job_id,
            rule_id=rule.id,
            recipient_user_id=data.from_.user_id,
            comment_id=data.comment_id,
            message=rule.dm_message,
            status="queued",
        )
        try:
            with session.begin_nested():
                session.add(job)
                session.flush()
        except IntegrityError:
            session.add(
                DuplicateBlock(
                    event_id=payload.event_id,
                    rule_id=rule.id,
                    recipient_user_id=data.from_.user_id,
                    reason="rule_recipient_already_claimed",
                )
            )


class PseudoGramClient:
    def __init__(self, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def send_dm(self, *, recipient_user_id: str, message: str, comment_id: str, idempotency_key: str) -> httpx.Response:
        return self._client.post(
            "/v1/dm/send",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "recipient_user_id": recipient_user_id,
                "message": message,
                "comment_id": comment_id,
            },
        )
