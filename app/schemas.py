from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RuleCreate(BaseModel):
    keyword: str = Field(min_length=1, max_length=500)
    dm_message: str = Field(min_length=1, max_length=5000)

    @field_validator("keyword", "dm_message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class RuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    keyword: str
    dm_message: str


class EventAuthor(BaseModel):
    user_id: str
    username: str | None = None


class EventData(BaseModel):
    comment_id: str
    post_id: str | None = None
    text: str | None = None
    created_at: datetime | None = None
    from_: EventAuthor | None = Field(default=None, alias="from")

    model_config = ConfigDict(populate_by_name=True)


class WebhookPayload(BaseModel):
    event_id: str
    event_type: Literal["comment.created", "comment.deleted"]
    sent_at: datetime
    data: EventData


class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int
