from sqlalchemy import select

from app.core.security import decode_access_token, hash_secret
from app.models.user import User, UserRole


def register_family(client, email="eltern@example.ch", password="s3hr-sicher"):
    return client.post(
        "/auth/register-family",
        json={
            "family_name": "Muster",
            "admin_display_name": "Mama",
            "admin_email": email,
            "admin_password": password,
        },
    )


def test_register_family_returns_token(client):
    response = register_family(client)
    assert response.status_code == 201
    assert "access_token" in response.json()


def test_register_family_rejects_duplicate_email(client):
    register_family(client, email="doppelt@example.ch")
    response = register_family(client, email="doppelt@example.ch")
    assert response.status_code == 409


def test_login_with_correct_credentials(client):
    register_family(client, email="login@example.ch", password="korrekt-123")
    response = client.post("/auth/login", json={"email": "login@example.ch", "password": "korrekt-123"})
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_with_wrong_password_fails(client):
    register_family(client, email="falsch@example.ch", password="korrekt-123")
    response = client.post("/auth/login", json={"email": "falsch@example.ch", "password": "falsch"})
    assert response.status_code == 401


def test_pin_login_accepts_dashboard_user(client, db_session):
    register_family(client, email="dashboard@example.ch")
    admin = db_session.scalar(select(User).where(User.email == "dashboard@example.ch"))

    dashboard_user = User(
        family_id=admin.family_id,
        role=UserRole.DASHBOARD,
        display_name="Wand-Dashboard",
        pin_hash=hash_secret("2468"),
    )
    db_session.add(dashboard_user)
    db_session.commit()
    db_session.refresh(dashboard_user)

    response = client.post(
        "/auth/pin",
        json={"family_id": str(admin.family_id), "user_id": str(dashboard_user.id), "pin": "2468"},
    )
    assert response.status_code == 200

    payload = decode_access_token(response.json()["access_token"])
    assert payload["role"] == "dashboard"
