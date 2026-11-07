"""Tests for the family location endpoint and the weather widget's forecast proxy."""

from unittest.mock import MagicMock, patch

import httpx


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


def _child_headers(client, db_session, family_id, pin="1234"):
    from app.core.security import hash_secret
    from app.models.user import User, UserRole

    child = User(family_id=family_id, role=UserRole.CHILD, display_name="Kind", pin_hash=hash_secret(pin))
    db_session.add(child)
    db_session.commit()
    db_session.refresh(child)

    login = client.post(
        "/auth/child-pin", json={"family_id": str(family_id), "user_id": str(child.id), "pin": pin}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_forecast_without_location_returns_404(client, db_session):
    parent_headers = _register_parent(client)
    response = client.get("/weather/forecast", headers=parent_headers)
    assert response.status_code == 404
    assert "Standort nicht konfiguriert" in response.json()["detail"]


def test_update_family_location_requires_parent(client, db_session):
    parent_headers = _register_parent(client, email="loc1@example.ch")
    response = client.get("/families/me", headers=parent_headers)
    family_id = response.json()["id"]
    child_headers = _child_headers(client, db_session, family_id)

    response = client.patch(
        "/families/me/location",
        headers=child_headers,
        json={"latitude": 47.37, "longitude": 8.54, "location_name": "Zürich"},
    )
    assert response.status_code == 403


def test_update_family_location_persists_and_enables_forecast(client, db_session):
    parent_headers = _register_parent(client, email="loc2@example.ch")

    update = client.patch(
        "/families/me/location",
        headers=parent_headers,
        json={"latitude": 47.37, "longitude": 8.54, "location_name": "Zürich"},
    )
    assert update.status_code == 200
    assert update.json()["location_name"] == "Zürich"

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "current_weather": {"time": "2026-09-12T12:00"},
        "hourly": {
            "time": [f"2026-09-12T{h:02d}:00" for h in range(24)] + [f"2026-09-13T{h:02d}:00" for h in range(24)],
            "temperature_2m": [15.0] * 48,
            "precipitation_probability": [10] * 48,
            "weathercode": [1] * 48,
        },
    }
    fake_response.raise_for_status = MagicMock()

    with patch("app.core.weather.httpx.get", return_value=fake_response):
        forecast = client.get("/weather/forecast", headers=parent_headers)

    assert forecast.status_code == 200
    entries = forecast.json()
    assert len(entries) == 48
    assert entries[0]["time"] == "2026-09-12T00:00"
    assert entries[-1]["time"] == "2026-09-13T23:00"
    assert entries[0]["temperature"] == 15.0


def test_forecast_upstream_error_returns_503(client, db_session):
    parent_headers = _register_parent(client, email="loc3@example.ch")
    client.patch(
        "/families/me/location",
        headers=parent_headers,
        json={"latitude": 47.37, "longitude": 8.54, "location_name": "Zürich"},
    )

    with patch("app.core.weather.httpx.get", side_effect=httpx.ConnectError("down")):
        response = client.get("/weather/forecast", headers=parent_headers)

    assert response.status_code == 503


def test_search_location_requires_parent(client, db_session):
    parent_headers = _register_parent(client, email="loc4@example.ch")
    response = client.get("/families/me", headers=parent_headers)
    family_id = response.json()["id"]
    child_headers = _child_headers(client, db_session, family_id)

    response = client.get("/weather/search-location", headers=child_headers, params={"query": "Zuerich"})
    assert response.status_code == 403


def test_search_location_returns_results(client, db_session):
    parent_headers = _register_parent(client, email="loc5@example.ch")

    fake_response = MagicMock()
    fake_response.json.return_value = {
        "results": [
            {"name": "Zürich", "admin1": "Zürich", "country": "Schweiz", "latitude": 47.37, "longitude": 8.54}
        ]
    }
    fake_response.raise_for_status = MagicMock()

    with patch("app.core.weather.httpx.get", return_value=fake_response):
        response = client.get("/weather/search-location", headers=parent_headers, params={"query": "Zuerich"})

    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["name"] == "Zürich"
