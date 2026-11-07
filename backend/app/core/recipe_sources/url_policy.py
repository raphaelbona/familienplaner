"""URL policy shared by the recipe sources and the adopt endpoint."""

from urllib.parse import urlparse

THEMEALDB_HOSTS = ("themealdb.com",)


def parse_domains(raw: str | None) -> list[str]:
    return [d.strip().lower() for d in (raw or "").split(",") if d.strip()]


def host_matches(hostname: str | None, domain: str) -> bool:
    """True if hostname is ``domain`` or a subdomain of it (never a look-alike suffix)."""
    if not hostname:
        return False
    hostname = hostname.lower().rstrip(".")
    domain = domain.lower().strip().lstrip(".")
    return bool(domain) and (hostname == domain or hostname.endswith("." + domain))


def is_trusted_https_url(url: str | None, allowed_domains: list[str], max_len: int = 500) -> bool:
    """https only, default port, no credentials, host on the allowlist."""
    if not url or len(url) > max_len:
        return False
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if port not in (None, 443):
        return False
    return any(host_matches(parsed.hostname, d) for d in allowed_domains)


def is_https_url(url: str | None, max_len: int = 500) -> bool:
    if not url or len(url) > max_len:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
