from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from app.main import app, get_stats, post_rule
from app.models import Comment, DMJob, DuplicateBlock, utcnow
from app.schemas import EventAuthor, EventData, RuleCreate, WebhookPayload
from app.security import signature_is_valid
from app.services import create_rule, record_webhook
from app.worker import process_one


def created_event(event_id: str, comment_id: str, user_id: str, text: str) -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id,
        event_type="comment.created",
        sent_at=utcnow(),
        data=EventData(comment_id=comment_id, text=text, from_=EventAuthor(user_id=user_id)),
    )


def deleted_event(event_id: str, comment_id: str) -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id,
        event_type="comment.deleted",
        sent_at=utcnow(),
        data=EventData(comment_id=comment_id),
    )


class FakeDMApi:
    def __init__(self) -> None:
        self.send_calls: list[dict] = []
        self.status_calls: list[str] = []

    def send_dm(self, **kwargs):
        self.send_calls.append(kwargs)
        return httpx.Response(202, json={"dm_id": "dm-test"})

    def dm_status(self, dm_id: str):
        self.status_calls.append(dm_id)
        return httpx.Response(200, json={"dm_id": dm_id, "status": "delivered"})


class RateLimitedDMApi(FakeDMApi):
    def send_dm(self, **kwargs):
        self.send_calls.append(kwargs)
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate_limited"})


class FailingDMApi(FakeDMApi):
    def send_dm(self, **kwargs):
        self.send_calls.append(kwargs)
        return httpx.Response(500, json={"error": "internal_error"})


def test_rule_creation_returns_required_contract(session):
    response = post_rule(RuleCreate(keyword="PRICE", dm_message="Price list"), session)

    assert response.keyword == "PRICE"
    assert response.dm_message == "Price list"
    assert response.rule_id


def test_http_routes_create_rule_accept_webhook_and_return_stats():
    with TestClient(app) as client:
        rule_response = client.post("/rules", json={"keyword": "PRICE", "dm_message": "List"})
        assert rule_response.status_code == 201
        assert set(rule_response.json()) == {"rule_id", "keyword", "dm_message"}

        webhook_response = client.post(
            "/webhook",
            json={
                "event_id": "http-event",
                "event_type": "comment.created",
                "sent_at": "2026-08-10T09:14:22.481Z",
                "data": {
                    "comment_id": "http-comment",
                    "text": "price please",
                    "from": {"user_id": "http-user", "username": "changed-name"},
                },
            },
        )
        assert webhook_response.status_code == 200
        assert client.get("/stats").json() == {
            "sent": 0,
            "failed": 0,
            "queued": 1,
            "duplicates_blocked": 0,
        }


def test_casefold_matching_and_rule_user_deduplication(session):
    rule = create_rule(session, keyword="PRICE", dm_message="List")
    first = created_event("evt-1", "cmt-1", "usr-1", "price please")
    second = created_event("evt-2", "cmt-2", "usr-1", "PRICE again")

    assert record_webhook(session, first, first.model_dump(by_alias=True, mode="json"))
    assert record_webhook(session, second, second.model_dump(by_alias=True, mode="json"))

    jobs = session.query(DMJob).all()
    blocks = session.query(DuplicateBlock).all()
    assert len(jobs) == 1
    assert jobs[0].rule_id == rule.id
    assert jobs[0].comment_id == "cmt-1"
    assert len(blocks) == 1
    assert blocks[0].reason == "rule_recipient_already_claimed"


def test_event_redelivery_is_ignored_without_creating_another_job(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-repeat", "cmt-1", "usr-1", "PRICE")

    assert record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    assert not record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    assert session.query(DMJob).count() == 1
    assert session.query(DuplicateBlock).count() == 0


def test_deleted_tombstone_prevents_out_of_order_creation(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    deleted = deleted_event("evt-delete", "cmt-1")
    created = created_event("evt-create", "cmt-1", "usr-1", "PRICE")

    assert record_webhook(session, deleted, deleted.model_dump(by_alias=True, mode="json"))
    assert record_webhook(session, created, created.model_dump(by_alias=True, mode="json"))
    assert session.get(Comment, "cmt-1").state == "deleted"
    assert session.query(DMJob).count() == 0


def test_worker_sends_then_reconciles_delivery_and_stats(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-1", "cmt-1", "usr-1", "PRICE")
    record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    client = FakeDMApi()

    assert process_one(client)
    job = session.query(DMJob).one()
    session.refresh(job)
    assert job.status == "awaiting_delivery"
    assert client.send_calls[0]["idempotency_key"].endswith("attempt:1")

    job.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    assert process_one(client)
    session.refresh(job)
    assert job.status == "delivered"
    assert get_stats(session).model_dump() == {
        "sent": 1,
        "failed": 0,
        "queued": 0,
        "duplicates_blocked": 0,
    }


def test_rate_limit_and_server_error_leave_durable_retry_jobs(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    first = created_event("evt-rate", "cmt-rate", "usr-rate", "PRICE")
    record_webhook(session, first, first.model_dump(by_alias=True, mode="json"))

    assert process_one(RateLimitedDMApi())
    rate_job = session.query(DMJob).one()
    session.refresh(rate_job)
    assert rate_job.status == "queued"
    assert rate_job.delivery_attempt == 0
    assert rate_job.idempotency_key is None
    assert rate_job.last_error == "remote_rate_limited"

    rate_job.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    # Make a distinct rule/user pair so the unique constraint intentionally does not suppress it.
    second_rule = create_rule(session, keyword="DISCOUNT", dm_message="Discount list")
    second = created_event("evt-500", "cmt-500", "usr-500", "DISCOUNT")
    record_webhook(session, second, second.model_dump(by_alias=True, mode="json"))
    # Cancel the rate-limited job so the 500 test claims the newly created job.
    rate_job.status = "cancelled"
    session.commit()

    assert process_one(FailingDMApi())
    failed_job = session.query(DMJob).filter(DMJob.rule_id == second_rule.id).one()
    session.refresh(failed_job)
    assert failed_job.status == "queued"
    assert failed_job.delivery_attempt == 1
    assert failed_job.idempotency_key is not None
    assert failed_job.last_error == "remote_http_500"

    original_key = failed_job.idempotency_key
    failed_job.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    recovery_client = FakeDMApi()
    assert process_one(recovery_client)
    assert recovery_client.send_calls[0]["idempotency_key"] == original_key


def test_signature_verification_uses_raw_body_and_constant_time_comparison(monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(
        "app.security.get_settings",
        lambda: Settings(database_url="sqlite:///./unused.sqlite3", pseudogram_api_key="secret", webhook_signature_required=True),
    )
    raw = b'{"event_id":"evt"}'
    import hashlib
    import hmac

    valid = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    assert signature_is_valid(raw, valid)
    assert not signature_is_valid(raw, "sha256=forged")
