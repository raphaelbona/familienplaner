"""Testcontainers-gestützte Fixtures: startet eine echte Postgres-Instanz über
lokales Docker Desktop je Testsession, statt gegen ein abweichendes
Test-Backend (z.B. SQLite) zu testen. Braucht einen laufenden Docker-Daemon.
"""

import re
import secrets
import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.postgres import PostgresContainer

from app import models  # noqa: F401  -- registers all tables on Base.metadata
from app.api.deps import hash_device_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.family import Family
from app.models.family_device import FamilyDevice


@pytest.fixture(scope="session")
def _postgres_url() -> Generator[str, None, None]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        yield postgres.get_connection_url()


@pytest.fixture(scope="session")
def _engine(_postgres_url: str):
    engine = create_engine(_postgres_url)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(_engine) -> Generator[Session, None, None]:
    """Jeder Test läuft in einer eigenen Transaktion, die am Ende zurückgerollt wird."""
    connection = _engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(bind=connection)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


class _AutoPairingClient(TestClient):
    """Test client that proves device pairing for the endpoints that need it (PIN login, PIN account lists).

    Most feature tests only need a child/dashboard login and are not about pairing: for those calls this
    client pairs a device for the request's family (path or JSON body) and sends X-Device-Token, unless the
    test passes its own header. Pairing behaviour itself is tested with the plain ``unpaired_client``.
    """

    db: Session
    _tokens: dict

    def request(self, method, url, **kwargs):  # noqa: ANN001
        headers = dict(kwargs.get("headers") or {})
        if not any(k.lower() == "x-device-token" for k in headers):
            family_id = self._device_family_id(str(url), kwargs.get("json"))
            if family_id is not None:
                token = self._token_for(family_id)
                if token:
                    headers["X-Device-Token"] = token
                    kwargs["headers"] = headers
        return super().request(method, url, **kwargs)

    @staticmethod
    def _device_family_id(url: str, body):
        path = url.split("?")[0]
        match = re.fullmatch(r"(?:https?://[^/]+)?/families/([0-9a-f-]{36})/(?:pin|child)-accounts", path)
        if match:
            return match.group(1)
        if path in ("/auth/pin", "/auth/child-pin") and isinstance(body, dict):
            return body.get("family_id")
        return None

    def _token_for(self, family_id: str):
        if family_id in self._tokens:
            return self._tokens[family_id]
        try:
            family_uuid = uuid.UUID(str(family_id))
        except ValueError:
            return None
        if self.db.get(Family, family_uuid) is None:
            return None
        token = secrets.token_urlsafe(32)
        self.db.add(FamilyDevice(family_id=family_uuid, label="Test-Tablet", token_hash=hash_device_token(token)))
        self.db.commit()
        self._tokens[family_id] = token
        return token


@pytest.fixture()
def unpaired_client(db_session: Session) -> Generator[TestClient, None, None]:
    """Plain client: never sends a device token on its own."""

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with _AutoPairingClient(app) as test_client:
        test_client.db = db_session
        test_client._tokens = {}
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _no_discover_rate_limit(monkeypatch):
    """The production rate limit (10 s between calls) would make repeated /recipes/discover calls in
    a single test fail; tests of the limiter itself install their own limiter with a fake clock."""
    from app.api import recipes
    from app.core.discover_limits import DiscoverRateLimiter

    monkeypatch.setattr(recipes, "discover_limiter", DiscoverRateLimiter(min_interval=0, daily_cap=10**9))


@pytest.fixture(autouse=True)
def _fresh_site_search_state(monkeypatch):
    """Site-search pacing, circuit breaker and result cache are process-wide: isolate every test and keep the
    default 1 s politeness interval from slowing them down (tests of the pacing build their own gate)."""
    from app.core.recipe_sources import site_search

    monkeypatch.setattr(site_search, "MIN_REQUEST_INTERVAL", 0.0)
    site_search.reset_shared_state()
    yield
    site_search.reset_shared_state()


@pytest.fixture(autouse=True)
def _no_real_google_network(monkeypatch):
    """Tests must never reach Google: building the API client, refreshing a token and revoking a token fail
    loudly unless a test installs its own mock (patching ``discovery.build`` etc. inside the test wins)."""

    def refuse(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("Tests duerfen Google nicht wirklich aufrufen")

    monkeypatch.setattr("app.core.google_sync.discovery.build", refuse)
    monkeypatch.setattr("app.core.google_sync.httpx.post", refuse)
    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh", refuse)
