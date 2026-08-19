import os

os.environ.setdefault("DATABASE_URL", "sqlite:///./test.sqlite3")
os.environ.setdefault("WEBHOOK_SIGNATURE_REQUIRED", "false")
os.environ.setdefault("PSEUDOGRAM_API_KEY", "test-key")

import pytest
from sqlalchemy import delete

from app.database import SessionLocal
from app.migrate import run_migrations
from app.models import ApiRequestLog, Comment, DMJob, DuplicateBlock, Rule, WebhookEvent


run_migrations()


@pytest.fixture(autouse=True)
def clean_database():
    session = SessionLocal()
    try:
        for model in (DuplicateBlock, ApiRequestLog, DMJob, WebhookEvent, Comment, Rule):
            session.execute(delete(model))
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

