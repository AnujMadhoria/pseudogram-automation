import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.migrate import run_migrations
from app.models import ApiRequestLog, DMJob, utcnow
from app.services import PseudoGramClient

logger = logging.getLogger(__name__)
RATE_LIMIT_WINDOW = timedelta(seconds=60)
RATE_LIMIT_MAX_REQUESTS = 10


class DMApi(Protocol):
    def send_dm(self, *, recipient_user_id: str, message: str, comment_id: str, idempotency_key: str) -> httpx.Response: ...


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _retry_delay(attempt: int) -> timedelta:
    return timedelta(seconds=min(60, 2 ** min(max(attempt, 1), 5)))


def _claim_next(session: Session, status_name: str) -> DMJob | None:
    now = utcnow()
    statement = (
        select(DMJob)
        .where(DMJob.status == status_name, DMJob.next_attempt_at <= now)
        .order_by(DMJob.next_attempt_at, DMJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=session.bind is not None and session.bind.dialect.name == "postgresql")
    )
    return session.scalar(statement)


def _take_send_lock(session: Session) -> bool:
    """Serialize sends across accidental extra worker replicas on PostgreSQL."""
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return True
    return bool(session.scalar(text("SELECT pg_try_advisory_xact_lock(918273646)")))


def process_one(client: DMApi) -> bool:
    """Process one durable Part A send job. Returns whether work was claimed."""
    return _send_one(client)


def _send_one(client: DMApi) -> bool:
    session = SessionLocal()
    try:
        if not _take_send_lock(session):
            session.rollback()
            return False
        job = _claim_next(session, "queued")
        if job is None:
            session.rollback()
            return False

        now = utcnow()
        cutoff = now - RATE_LIMIT_WINDOW
        session.execute(delete(ApiRequestLog).where(ApiRequestLog.requested_at < cutoff - RATE_LIMIT_WINDOW))
        recent = session.scalars(
            select(ApiRequestLog)
            .where(ApiRequestLog.requested_at >= cutoff)
            .order_by(ApiRequestLog.requested_at)
        ).all()
        if len(recent) >= RATE_LIMIT_MAX_REQUESTS:
            job.next_attempt_at = _as_utc(recent[0].requested_at) + RATE_LIMIT_WINDOW + timedelta(milliseconds=100)
            job.last_error = "local_rate_limit_wait"
            session.commit()
            return True

        # The database state and idempotency key are held in the same transaction as the call.
        # If the process crashes before commit, the exact same key will be retried.
        job.status = "sending"
        # Transient failures retain this key. Retrying them with a new key could
        # duplicate a DM when the remote API accepted a request but its response
        # was lost. A delivery that later reaches terminal `failed` clears it.
        if job.idempotency_key is None:
            job.delivery_attempt += 1
            job.idempotency_key = f"dm:{job.id}:attempt:{job.delivery_attempt}"
        job.lease_expires_at = now + timedelta(seconds=get_settings().http_timeout_seconds + 5)
        session.add(ApiRequestLog(job_id=job.id, requested_at=now))
        session.flush()

        try:
            response = client.send_dm(
                recipient_user_id=job.recipient_user_id,
                message=job.message,
                comment_id=job.comment_id,
                idempotency_key=job.idempotency_key,
            )
            _handle_send_response(job, response, now)
        except httpx.RequestError as exc:
            _handle_transient_failure(job, f"network_error: {exc}", now)

        job.lease_expires_at = None
        session.commit()
        return True
    except Exception:
        session.rollback()
        logger.exception("DM send attempt failed")
        raise
    finally:
        session.close()


def _handle_send_response(job: DMJob, response: httpx.Response, now: datetime) -> None:
    # Part A treats a successful mock API response as the completed send.
    # The live mock returns 200 while the brief documents 202, so accept all 2xx.
    if 200 <= response.status_code < 300:
        job.status = "sent"
        job.last_error = None
        job.next_attempt_at = now
        return

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "60")
        try:
            wait_seconds = max(1, float(retry_after))
        except ValueError:
            wait_seconds = 60
        # The API rejected this call, so it did not consume a delivery attempt.
        job.delivery_attempt = max(0, job.delivery_attempt - 1)
        job.status = "queued"
        job.idempotency_key = None
        job.last_error = "remote_rate_limited"
        job.next_attempt_at = now + timedelta(seconds=wait_seconds)
        return

    if response.status_code >= 500:
        _handle_transient_failure(job, f"remote_http_{response.status_code}", now)
        return

    job.status = "failed"
    job.last_error = f"non_retryable_http_{response.status_code}"
    job.next_attempt_at = now


def _handle_transient_failure(job: DMJob, detail: str, now: datetime) -> None:
    if job.delivery_attempt >= get_settings().dm_max_attempts:
        job.status = "failed"
        job.last_error = f"{detail}; retry_budget_exhausted"
        job.next_attempt_at = now
        return
    job.status = "queued"
    job.last_error = detail
    job.next_attempt_at = now + _retry_delay(job.delivery_attempt)


def run_forever() -> None:
    settings = get_settings()
    run_migrations()
    client = PseudoGramClient(
        api_key=settings.pseudogram_api_key,
        base_url=settings.pseudogram_base_url,
        timeout_seconds=settings.http_timeout_seconds,
    )
    logger.info("worker started")
    try:
        while True:
            try:
                processed = process_one(client)
            except Exception:
                logger.exception("worker loop recovered from an unexpected error")
                time.sleep(1)
                continue
            if not processed:
                time.sleep(settings.worker_poll_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
