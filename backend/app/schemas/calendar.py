import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CalendarEventCreate(BaseModel):
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    color: str | None = None
    owner_user_id: uuid.UUID | None = None
    recurrence_rule: Literal["daily", "weekly"] | None = None
    # When set, one event is created per listed person instead of a single
    # family-wide (or single-owner) event.
    owner_user_ids: list[uuid.UUID] | None = None


class CalendarEventRead(BaseModel):
    id: uuid.UUID
    title: str
    start: datetime
    end: datetime
    all_day: bool
    color: str | None
    owner_user_id: uuid.UUID | None
    recurrence_rule: str | None
    series_id: uuid.UUID | None
    source: str

    model_config = {"from_attributes": True}


class GoogleConnectResponse(BaseModel):
    authorization_url: str


class GoogleCalendarInfo(BaseModel):
    id: str
    name: str
    selected: bool
    owner_user_ids: list[uuid.UUID]


class GoogleStatusRead(BaseModel):
    configured: bool
    connected: bool
    email: str | None = None
    # "ok" | "needs_reauth" | "error"; None while not connected
    status: str | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None
    calendars: list[GoogleCalendarInfo] = []


class GoogleCalendarSelectionItem(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    owner_user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class GoogleCalendarsUpdate(BaseModel):
    """The complete selection: calendars not listed are no longer imported."""

    calendars: list[GoogleCalendarSelectionItem] = Field(max_length=100)


class CalDAVConnectRequest(BaseModel):
    server_url: str
    username: str
    password: str
