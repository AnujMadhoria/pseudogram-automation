from datetime import timedelta

import httpx
from fastapi.testclient import TestClient

from app.main import app, get_stats, post_rule
from app.models import DMJob, DuplicateBlock, utcnow
from app.schemas import EventAuthor, EventData, RuleCreate, WebhookPayload
from app.services import create_rule, record_webhook
from app.worker import process_one


def created_event(event_id: str, comment_id: str, user_id: str, text: str) -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id,
        event_type="comment.created",
        sent_at=utcnow(),
        data=EventData(comment_id=comment_id, text=text, from_=EventAuthor(user_id=user_id)),
    )


class FakeDMApi:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.send_calls: list[dict] = []

    def send_dm(self, **kwargs):
        self.send_calls.append(kwargs)
        return httpx.Response(self.status_code, json={"dm_id": "dm-test"})


class RateLimitedDMApi(FakeDMApi):
    def send_dm(self, **kwargs):
        self.send_calls.append(kwargs)
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate_limited"})


def test_rule_route_returns_required_contract(session):
    response = post_rule(RuleCreate(keyword="PRICE", dm_message="Price list"), session)

    assert response.rule_id
    assert response.keyword == "PRICE"
    assert response.dm_message == "Price list"


def test_http_routes_accept_rule_webhook_and_stats():
    with TestClient(app) as client:
        assert client.post("/rules", json={"keyword": "PRICE", "dm_message": "List"}).status_code == 201
        webhook = {
            "event_id": "http-event",
            "event_type": "comment.created",
            "sent_at": "2026-08-10T09:14:22.481Z",
            "data": {
                "comment_id": "http-comment",
                "text": "price please",
                "from": {"user_id": "http-user"},
            },
        }
        assert client.post("/webhook", json=webhook).status_code == 200
        assert client.get("/stats").json() == {
            "sent": 0,
            "failed": 0,
            "queued": 1,
            "duplicates_blocked": 0,
        }


def test_case_insensitive_matching_and_rule_user_deduplication(session):
    rule = create_rule(session, keyword="PRICE", dm_message="List")
    first = created_event("evt-1", "cmt-1", "usr-1", "price please")
    second = created_event("evt-2", "cmt-2", "usr-1", "PRICE again")

    assert record_webhook(session, first, first.model_dump(by_alias=True, mode="json"))
    assert record_webhook(session, second, second.model_dump(by_alias=True, mode="json"))

    job = session.query(DMJob).one()
    assert job.rule_id == rule.id
    assert job.comment_id == "cmt-1"
    assert session.query(DuplicateBlock).count() == 1


def test_event_redelivery_creates_no_second_job(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-repeat", "cmt-1", "usr-1", "PRICE")

    assert record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    assert not record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    assert session.query(DMJob).count() == 1


def test_worker_marks_successful_mock_response_as_sent(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-send", "cmt-send", "usr-send", "PRICE")
    record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))
    client = FakeDMApi(status_code=200)

    assert process_one(client)
    job = session.query(DMJob).one()
    session.refresh(job)
    assert job.status == "sent"
    assert client.send_calls[0]["idempotency_key"].endswith("attempt:1")
    assert get_stats(session).model_dump() == {
        "sent": 1,
        "failed": 0,
        "queued": 0,
        "duplicates_blocked": 0,
    }


def test_transient_failure_retries_with_same_idempotency_key(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-retry", "cmt-retry", "usr-retry", "PRICE")
    record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))

    assert process_one(FakeDMApi(status_code=500))
    job = session.query(DMJob).one()
    session.refresh(job)
    original_key = job.idempotency_key
    assert job.status == "queued"
    assert original_key is not None

    job.next_attempt_at = utcnow() - timedelta(seconds=1)
    session.commit()
    recovered = FakeDMApi(status_code=202)
    assert process_one(recovered)
    session.refresh(job)
    assert job.status == "sent"
    assert recovered.send_calls[0]["idempotency_key"] == original_key


def test_rate_limit_reschedules_without_losing_the_job(session):
    create_rule(session, keyword="PRICE", dm_message="List")
    event = created_event("evt-rate", "cmt-rate", "usr-rate", "PRICE")
    record_webhook(session, event, event.model_dump(by_alias=True, mode="json"))

    assert process_one(RateLimitedDMApi())
    job = session.query(DMJob).one()
    session.refresh(job)
    assert job.status == "queued"
    assert job.delivery_attempt == 0
    assert job.idempotency_key is None
    assert job.last_error == "remote_rate_limited"
