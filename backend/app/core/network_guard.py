"""Guards against SSRF via user-supplied server URLs (e.g. CalDAV server URLs)."""

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import Settings

_ALWAYS_BLOCKED_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. the 169.254.169.254 cloud-metadata address
    ipaddress.ip_network("fe80::/10"),
]


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied URL resolves to a disallowed network target."""


def validate_external_server_url(url: str, settings: Settings) -> None:
    """Reject URLs that could be used for SSRF against internal/metadata services.

    In "ch_cloud" (shared multi-tenant) hosting, private/loopback ranges are blocked
    outright, since one family's CalDAV URL could otherwise be pointed at another
    container or internal service reachable from the backend. In "self_hosted" mode
    a family may legitimately run CalDAV on their own LAN, so only loopback and the
    link-local/cloud-metadata range are blocked there.

    Note: this validates the hostname's DNS resolution at connect time; it does not
    protect against DNS rebinding between this check and the later HTTP request.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Nur http/https-URLs sind erlaubt.")
    if not parsed.hostname:
        raise UnsafeUrlError("Ungültige Server-URL.")

    try:
        addr_infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        # Can't resolve it, so there's no internal target to reach; the connection
        # attempt itself will fail naturally afterwards. Don't fail closed here, since
        # that would also reject legitimate hosts during transient DNS hiccups.
        return

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise UnsafeUrlError("Diese Server-Adresse ist nicht erlaubt.")
        if any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
            raise UnsafeUrlError("Diese Server-Adresse ist nicht erlaubt.")
        if settings.hosting_mode == "ch_cloud" and ip.is_private:
            raise UnsafeUrlError("Private Netzwerkadressen sind in dieser Hosting-Umgebung nicht erlaubt.")
