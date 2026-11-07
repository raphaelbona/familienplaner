"""Tests for Web Push subscription endpoints and the task-assignment trigger."""

from datetime import date
from unittest.mock import patch

from sqlalchemy import select

from app.models.push_subscription import PushSubscription
from app.models.user import User


def _register_parent(client, email="eltern@example.ch"):
    response = client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Papa",
            "admin_email": email,
            "admin_password": "s3hr-sicher",
        },
    )
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_vapid_public_key_null_when_not_configured(client, db_session):
    with patch("app.api.push.get_settings") as mock_settings:
        mock_settings.return_value.vapid_public_key = None
        response = client.get("/notifications/vapid-public-key")
    assert response.status_code == 200
    assert response.json()["public_key"] is None


def test_subscribe_and_unsubscribe(client, db_session):
    parent_headers = _register_parent(client)

    subscribe = client.post(
        "/notifications/subscribe",
        headers=parent_headers,
        json={"endpoint": "https://push.example.com/abc", "p256dh": "key123", "auth": "auth123"},
    )
    assert subscribe.status_code == 201

    subscriptions = db_session.scalars(select(PushSubscription)).all()
    assert len(subscriptions) == 1
    assert subscriptions[0].endpoint == "https://push.example.com/abc"

    unsubscribe = client.request(
        "DELETE",
        "/notifications/subscribe",
        headers=parent_headers,
        params={"endpoint": "https://push.example.com/abc"},
    )
    assert unsubscribe.status_code == 204
    assert db_session.scalars(select(PushSubscription)).all() == []


def test_resubscribing_same_endpoint_updates_instead_of_duplicating(client, db_session):
    parent_headers = _register_parent(client, email="resub@example.ch")

    client.post(
        "/notifications/subscribe",
        headers=parent_headers,
        json={"endpoint": "https://push.example.com/abc", "p256dh": "old-key", "auth": "old-auth"},
    )
    client.post(
        "/notifications/subscribe",
        headers=parent_headers,
        json={"endpoint": "https://push.example.com/abc", "p256dh": "new-key", "auth": "new-auth"},
    )

    subscriptions = db_session.scalars(select(PushSubscription)).all()
    assert len(subscriptions) == 1
    assert subscriptions[0].p256dh == "new-key"


def test_creating_task_instance_sends_push_to_assignee(client, db_session):
    from app.core.security import hash_secret
    from app.models.user import UserRole

    parent_headers = _register_parent(client, email="pushtask@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "pushtask@example.ch"))

    child = User(family_id=admin.family_id, role=UserRole.CHILD, display_name="Kind", pin_hash=hash_secret("1234"))
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    subscription = PushSubscription(
        user_id=child.id, endpoint="https://push.example.com/child", p256dh="key", auth="auth"
    )
    db_session.add(subscription)
    db_session.commit()

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zimmer aufräumen", "kind": "basis", "points_value": 0},
    ).json()

    with patch("app.api.tasks.get_settings") as mock_settings, patch("app.core.push.webpush") as mock_webpush:
        mock_settings.return_value.vapid_public_key = "pub"
        mock_settings.return_value.vapid_private_key = "priv"
        mock_settings.return_value.vapid_subject = "mailto:test@example.ch"

        response = client.post(
            "/tasks/instances",
            headers=parent_headers,
            json={
                "template_id": template["id"],
                "assignee_user_id": str(child.id),
                "due_date": date.today().isoformat(),
            },
        )

    assert response.status_code == 201
    mock_webpush.assert_called_once()
    call_kwargs = mock_webpush.call_args.kwargs
    assert call_kwargs["subscription_info"]["endpoint"] == "https://push.example.com/child"


def test_creating_task_instance_without_push_configured_does_not_call_webpush(client, db_session):
    from app.core.security import hash_secret
    from app.models.user import UserRole

    parent_headers = _register_parent(client, email="pushtask2@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "pushtask2@example.ch"))

    child = User(family_id=admin.family_id, role=UserRole.CHILD, display_name="Kind", pin_hash=hash_secret("1234"))
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    template = client.post(
        "/tasks/templates",
        headers=parent_headers,
        json={"title": "Zimmer aufräumen", "kind": "basis", "points_value": 0},
    ).json()

    with patch("app.core.push.webpush") as mock_webpush:
        response = client.post(
            "/tasks/instances",
            headers=parent_headers,
            json={
                "template_id": template["id"],
                "assignee_user_id": str(child.id),
                "due_date": date.today().isoformat(),
            },
        )

    assert response.status_code == 201
    mock_webpush.assert_not_called()
