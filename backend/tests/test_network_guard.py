"""Tests for the CalDAV SSRF guard."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from app.core.network_guard import UnsafeUrlError, validate_external_server_url


def _settings(hosting_mode: str) -> MagicMock:
    settings = MagicMock()
    settings.hosting_mode = hosting_mode
    return settings


def _fake_addrinfo(ip: str) -> list[tuple]:
    return [(None, None, None, None, (ip, 0))]


def test_rejects_non_http_scheme():
    with pytest.raises(UnsafeUrlError, match="http/https"):
        validate_external_server_url("ftp://example.com", _settings("self_hosted"))


def test_rejects_loopback_in_any_hosting_mode():
    with patch("app.core.network_guard.socket.getaddrinfo", return_value=_fake_addrinfo("127.0.0.1")):
        with pytest.raises(UnsafeUrlError):
            validate_external_server_url("http://localhost/caldav", _settings("self_hosted"))
        with pytest.raises(UnsafeUrlError):
            validate_external_server_url("http://localhost/caldav", _settings("ch_cloud"))


def test_rejects_cloud_metadata_address_in_any_hosting_mode():
    with patch("app.core.network_guard.socket.getaddrinfo", return_value=_fake_addrinfo("169.254.169.254")):
        with pytest.raises(UnsafeUrlError):
            validate_external_server_url("http://metadata.internal/caldav", _settings("self_hosted"))
        with pytest.raises(UnsafeUrlError):
            validate_external_server_url("http://metadata.internal/caldav", _settings("ch_cloud"))


def test_allows_private_lan_address_when_self_hosted():
    """A self-hosted family's own Nextcloud on their LAN must remain reachable."""
    with patch("app.core.network_guard.socket.getaddrinfo", return_value=_fake_addrinfo("192.168.1.50")):
        validate_external_server_url("http://nas.local/caldav", _settings("self_hosted"))


def test_rejects_private_lan_address_when_ch_cloud():
    """In shared multi-tenant hosting, a family must not be able to pivot to internal infra."""
    with patch("app.core.network_guard.socket.getaddrinfo", return_value=_fake_addrinfo("192.168.1.50")):
        with pytest.raises(UnsafeUrlError, match="Private Netzwerkadressen"):
            validate_external_server_url("http://nas.local/caldav", _settings("ch_cloud"))


def test_unresolvable_hostname_does_not_raise():
    """DNS resolution failure isn't a security-relevant case; let the real connection fail instead."""
    with patch("app.core.network_guard.socket.getaddrinfo", side_effect=socket.gaierror("name resolution failed")):
        validate_external_server_url("http://does-not-exist.example.invalid/caldav", _settings("self_hosted"))


def test_allows_public_address():
    with patch("app.core.network_guard.socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
        validate_external_server_url("https://example.com/caldav", _settings("ch_cloud"))
