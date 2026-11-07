"""Sends Web Push notifications to a user's subscribed browsers.

A no-op wherever VAPID keys aren't configured (see SELF_HOSTING.md) -- lets
the rest of the app call this unconditionally without checking config first.
"""

import json
import logging

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.push_subscription import PushSubscription

logger = logging.getLogger("push")


def send_push_to_user(db: Session, user_id, title: str, body: str, settings: Settings) -> None:
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return

    subscriptions = list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id)))
    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                },
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
            )
        except WebPushException as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code in (404, 410):
                # Browser unsubscribed or the endpoint expired; stop trying it.
                db.delete(subscription)
                db.commit()
            else:
                logger.error("Push an Nutzer %s fehlgeschlagen: %s", user_id, exc)
