"""Brute-force protection for the 4-digit PIN login (children and wall dashboard).

State lives in the database (user and family_device rows) so it holds across replicas and restarts. Every
counter change happens under ``SELECT ... FOR UPDATE`` so parallel guesses cannot exceed the limits.
"""

import math
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_secret
from app.models.family_device import FamilyDevice
from app.models.user import User, UserRole

# --- Policy (one place) ---
MAX_FAILED_PINS = 5  # consecutive wrong PINs per user before a lock
LOCK_DURATIONS = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))  # by level, last is the cap
DEVICE_MAX_FAILURES = 20  # wrong PINs per device within DEVICE_WINDOW (across all users)
DEVICE_WINDOW = timedelta(hours=1)

PIN_WRONG_MESSAGE = "PIN falsch"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Injectable clock: tests replace this with a fake.
clock: Callable[[], datetime] = _utcnow


def too_many_attempts(remaining: timedelta) -> HTTPException:
    seconds = max(1, math.ceil(remaining.total_seconds()))
    minutes = max(1, math.ceil(seconds / 60))
    unit = "Minute" if minutes == 1 else "Minuten"
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Zu viele Versuche. Bitte in {minutes} {unit} erneut versuchen.",
        headers={"Retry-After": str(seconds)},
    )


def lock_duration(level: int) -> timedelta:
    return LOCK_DURATIONS[min(max(level, 1), len(LOCK_DURATIONS)) - 1]


def reset_user_lock(user: User) -> None:
    user.failed_pin_attempts = 0
    user.pin_locked_until = None
    user.pin_lock_level = 0


def _register_device_failure(device: FamilyDevice, now: datetime) -> None:
    started = device.pin_window_started_at
    if started is None or now >= started + DEVICE_WINDOW:
        device.pin_window_started_at = now
        device.failed_pin_attempts = 0
    device.failed_pin_attempts += 1


def pin_login_user(db: Session, device: FamilyDevice, family_id: uuid.UUID, user_id: uuid.UUID, pin: str) -> User:
    """The user for a correct PIN; raises 401 (indistinguishable failures) or 429 (throttled / locked).

    The device must already be verified as valid and to belong to ``family_id``. The device row is locked
    first, then the user row (fixed order, no deadlocks).
    """
    now = clock()
    locked_device = db.get(FamilyDevice, device.id, with_for_update=True, populate_existing=True)
    if locked_device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, PIN_WRONG_MESSAGE)
    started = locked_device.pin_window_started_at
    if (
        started is not None
        and now < started + DEVICE_WINDOW
        and locked_device.failed_pin_attempts >= DEVICE_MAX_FAILURES
    ):
        db.commit()  # release the row lock (nothing changed)
        raise too_many_attempts(started + DEVICE_WINDOW - now)

    user = db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    if user is None or user.family_id != family_id or user.role not in (UserRole.CHILD, UserRole.DASHBOARD):
        _register_device_failure(locked_device, now)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, PIN_WRONG_MESSAGE)

    if user.pin_locked_until is not None and user.pin_locked_until > now:
        remaining = user.pin_locked_until - now
        db.commit()  # release the row locks (nothing changed)
        raise too_many_attempts(remaining)  # the PIN is not verified while locked

    if user.pin_hash and verify_secret(pin, user.pin_hash):
        reset_user_lock(user)  # the device counter is deliberately not reset
        db.commit()
        return user

    _register_device_failure(locked_device, now)
    user.failed_pin_attempts += 1
    lock_error: HTTPException | None = None
    if user.failed_pin_attempts >= MAX_FAILED_PINS:
        user.pin_lock_level += 1
        duration = lock_duration(user.pin_lock_level)
        user.pin_locked_until = now + duration
        user.failed_pin_attempts = 0
        lock_error = too_many_attempts(duration)
    db.commit()
    raise lock_error or HTTPException(status.HTTP_401_UNAUTHORIZED, PIN_WRONG_MESSAGE)
