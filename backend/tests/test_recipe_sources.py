"""JSON-LD parser and ChefkochSearchSource (security properties). No live network."""

import json
import socket

import httpx
import pytest

from app.core.config import Settings
from app.core.recipe_sources import json_ld_parser as p

RECIPE_LD = {
    "@type": "Recipe",
    "name": "Pasta Pomodoro",
    "image": ["https://img.example/a.jpg", "https://img.example/b.jpg"],
    "recipeIngredient": ["200 g Pasta", "4 Eggs", "2 EL Olivenöl", "1,5 kg Tomaten (reif)"],
    "prepTime": "PT15M",
    "cookTime": "PT1H15M",
    "recipeYield": "4 Portionen",
}


def _page(*ld_objs) -> str:
    blocks = "".join(f'<script type="application/ld+json">{json.dumps(o)}</script>' for o in ld_objs)
    return f"<html><head>{blocks}</head><body>x</body></html>"


# ---------------------------------------------------------------- json_ld_parser


def test_parser_handles_graph_wrapper():
    html = _page({"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, RECIPE_LD]})
    found = p.find_recipes_in_json_ld(p.extract_json_ld_from_html(html))
    assert [r.title for r in found] == ["Pasta Pomodoro"]


def test_parser_handles_type_arrays_and_top_level_lists():
    typed = {**RECIPE_LD, "@type": ["Recipe", "Thing"], "name": "Array Typ"}
    found = p.find_recipes_in_json_ld(p.extract_json_ld_from_html(_page([typed, {"@type": "Person"}])))
    assert [r.title for r in found] == ["Array Typ"]


def test_parser_ignores_non_recipes_and_broken_json():
    html = '<script type="application/ld+json">{not json</script>' + _page({"@type": "Article", "name": "x"})
    assert p.find_recipes_in_json_ld(p.extract_json_ld_from_html(html)) == []


def test_duration_parsing():
    assert p._parse_iso8601_duration("PT30M") == 30
    assert p._parse_iso8601_duration("PT1H15M") == 75
    assert p._parse_iso8601_duration("PT2H") == 120
    assert p._parse_iso8601_duration("P0DT45M") == 45
    assert p._parse_iso8601_duration("PT") is None
    assert p._parse_iso8601_duration("soon") is None
    assert p._parse_iso8601_duration(None) is None


def test_recipe_fields_are_parsed():
    rec = p.parse_json_ld_recipe(RECIPE_LD)
    assert rec.prep_minutes == 90  # 15 + 75
    assert rec.servings == 4
    assert rec.image_url == "https://img.example/a.jpg"
    assert rec.ingredients == ["Pasta", "Eggs", "Olivenöl", "Tomaten"]


def test_image_as_object_and_list_of_objects():
    assert p.parse_json_ld_recipe({**RECIPE_LD, "image": {"url": "https://i.example/o.jpg"}}).image_url == (
        "https://i.example/o.jpg"
    )
    assert p.parse_json_ld_recipe({**RECIPE_LD, "image": [{"url": "https://i.example/l.jpg"}]}).image_url == (
        "https://i.example/l.jpg"
    )
    assert p.parse_json_ld_recipe({**RECIPE_LD, "image": "javascript:alert(1)"}).image_url is None


def test_missing_fields_are_tolerated():
    rec = p.parse_json_ld_recipe({"@type": "Recipe", "name": "Minimal", "recipeIngredient": ["Salz"]})
    assert rec.prep_minutes is None and rec.servings is None and rec.image_url is None


def test_recipe_without_name_or_ingredients_is_skipped():
    assert p.parse_json_ld_recipe({"@type": "Recipe", "recipeIngredient": ["Salz"]}) is None
    assert p.parse_json_ld_recipe({"@type": "Recipe", "name": "Leer"}) is None
    assert p.parse_json_ld_recipe({"@type": "Recipe", "name": "Leer", "recipeIngredient": []}) is None


def test_total_time_used_when_prep_and_cook_missing():
    rec = p.parse_json_ld_recipe({**RECIPE_LD, "prepTime": None, "cookTime": None, "totalTime": "PT40M"})
    assert rec.prep_minutes == 40


# ---------------------------------------------------------------- ChefkochSearchSource

BASE_ID = 1274521232808420  # real Chefkoch ids have 16 digits


def rid(n: int = 0) -> int:
    return BASE_ID + n


R = f"https://www.chefkoch.de/rezepte/{rid(9)}/Pasta-Pomodoro.html"  # a valid recipe URL


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    )


def _source(handler, monkeypatch, enabled=True):
    from app.core.recipe_sources import site_search
    from app.core.recipe_sources.chefkoch import ChefkochSearchSource

    settings = Settings(recipe_search_enabled=enabled)
    monkeypatch.setattr(site_search, "get_settings", lambda: settings)
    return ChefkochSearchSource(transport=httpx.MockTransport(handler))


def _recorder(routes):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return routes(request)

    return handler, seen


def _ok_recipe(_request):
    return httpx.Response(200, text=_page(RECIPE_LD))


def _fetch(src, url):
    return src._fetch_and_parse(url)


def test_rejects_http_scheme(monkeypatch):
    handler, seen = _recorder(_ok_recipe)
    src = _source(handler, monkeypatch)
    assert _fetch(src, f"http://www.chefkoch.de/rezepte/{rid()}/Pasta.html") is None
    assert seen == []


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.com/rezepte/{rid()}/Pasta.html",
        f"https://chefkoch.de.evil.com/rezepte/{rid()}/Pasta.html",
        f"https://chefkoch.de@evil.com/rezepte/{rid()}/Pasta.html",
        f"https://evilchefkoch.de/rezepte/{rid()}/Pasta.html",
        f"https://www.chefkoch.de:8443/rezepte/{rid()}/Pasta.html",
        f"https://127.0.0.1/rezepte/{rid()}/Pasta.html",
        f"https://www.chefkoch.de/rezepte/{rid()}.html",  # not the exact recipe shape
        f"https://www.chefkoch.de/forum/{rid()}/x.html",
        "https://www.chefkoch.de/rs/s0t3/Nudeln/Rezepte.html",  # robots.txt: sorted variants are off limits
    ],
)
def test_rejects_non_allowlisted_hosts_and_paths(url, monkeypatch):
    handler, seen = _recorder(_ok_recipe)
    src = _source(handler, monkeypatch)
    assert _fetch(src, url) is None
    assert seen == []


def test_accepts_allowlisted_domain_and_subdomain(monkeypatch):
    handler, seen = _recorder(_ok_recipe)
    src = _source(handler, monkeypatch)
    for url in (f"https://chefkoch.de/rezepte/{rid(1)}/A.html", R):
        rec = _fetch(src, url)
        assert rec is not None and rec.title == "Pasta Pomodoro"
        assert rec.source_url == url and rec.source_name.endswith("chefkoch.de")
        assert rec.language == "de"
    assert len(seen) == 2


def test_redirects_to_foreign_host_are_not_followed(monkeypatch):
    def routes(request):
        if request.url.host == "www.chefkoch.de":
            return httpx.Response(302, headers={"location": "https://evil.com/steal"})
        return httpx.Response(200, text=_page(RECIPE_LD))

    handler, seen = _recorder(routes)
    src = _source(handler, monkeypatch)
    assert _fetch(src, R) is None
    assert [r.url.host for r in seen] == ["www.chefkoch.de"]


def test_redirect_within_allowlist_is_followed_for_at_most_two_hops(monkeypatch):
    def routes(request):
        n = int(request.url.path.split("/")[2]) - BASE_ID
        if n < 3:
            return httpx.Response(301, headers={"location": f"/rezepte/{rid(n + 1)}/x.html"})
        return httpx.Response(200, text=_page(RECIPE_LD))

    handler, seen = _recorder(routes)
    src = _source(handler, monkeypatch)
    assert _fetch(src, f"https://www.chefkoch.de/rezepte/{rid(1)}/x.html") is not None  # 1 -> 2 -> 3 (two hops)
    assert len(seen) == 3
    seen.clear()
    assert _fetch(src, f"https://www.chefkoch.de/rezepte/{rid(0)}/x.html") is None  # 0 -> 1 -> 2 -> third hop refused
    assert len(seen) == 3


def test_redirect_to_non_recipe_path_is_refused(monkeypatch):
    handler, seen = _recorder(lambda r: httpx.Response(302, headers={"location": "/login"}))
    src = _source(handler, monkeypatch)
    assert _fetch(src, R) is None
    assert len(seen) == 1


@pytest.mark.parametrize("status", [201, 204, 301, 403, 404, 500])
def test_only_status_200_is_parsed(status, monkeypatch):
    handler, _ = _recorder(lambda r: httpx.Response(status, text=_page(RECIPE_LD)))
    src = _source(handler, monkeypatch)
    assert _fetch(src, R) is None


def test_oversize_body_is_rejected(monkeypatch):
    big = _page(RECIPE_LD) + ("x" * (2 * 1024 * 1024))
    handler, _ = _recorder(lambda r: httpx.Response(200, text=big))
    src = _source(handler, monkeypatch)
    assert _fetch(src, R) is None


def test_oversize_content_length_header_is_rejected(monkeypatch):
    handler, _ = _recorder(
        lambda r: httpx.Response(200, text=_page(RECIPE_LD), headers={"content-length": str(50 * 1024 * 1024)})
    )
    src = _source(handler, monkeypatch)
    assert _fetch(src, R) is None


def test_page_without_ingredients_or_json_ld_is_skipped(monkeypatch):
    handler, _ = _recorder(lambda r: httpx.Response(200, text=_page({**RECIPE_LD, "recipeIngredient": []})))
    assert _fetch(_source(handler, monkeypatch), R) is None
    handler, _ = _recorder(lambda r: httpx.Response(200, text="<html><body>kein JSON-LD</body></html>"))
    assert _fetch(_source(handler, monkeypatch), R) is None


def _ld_page_bytes(ld: dict) -> bytes:
    raw = json.dumps(ld, ensure_ascii=False)
    return f'<html><head><script type="application/ld+json">{raw}</script></head></html>'.encode()


def test_umlauts_survive_chunk_boundaries(monkeypatch):
    ld = {**RECIPE_LD, "name": "Käsespätzle mit Röstzwiebeln", "recipeIngredient": ["300 g Spätzle", "Käse"]}
    body = _ld_page_bytes(ld)

    def one_byte_chunks():
        for i in range(len(body)):  # splits every multi-byte character
            yield body[i : i + 1]

    handler, _ = _recorder(lambda r: httpx.Response(200, content=one_byte_chunks()))
    rec = _fetch(_source(handler, monkeypatch), R)
    assert rec is not None and rec.title == "Käsespätzle mit Röstzwiebeln"
    assert rec.ingredients == ["Spätzle", "Käse"]


def test_charset_from_content_type_header_is_honoured(monkeypatch):
    raw = json.dumps({**RECIPE_LD, "name": "Rösti"}, ensure_ascii=False)
    body = f'<script type="application/ld+json">{raw}</script>'.encode("latin-1")
    handler, _ = _recorder(
        lambda r: httpx.Response(200, content=body, headers={"content-type": "text/html; charset=iso-8859-1"})
    )
    assert _fetch(_source(handler, monkeypatch), R).title == "Rösti"


def test_invalid_bytes_are_replaced_not_silently_dropped(monkeypatch):
    body = _ld_page_bytes({**RECIPE_LD, "name": "Bad XX Byte"}).replace(b"XX", b"\xff\xfe")
    handler, _ = _recorder(lambda r: httpx.Response(200, content=body))
    rec = _fetch(_source(handler, monkeypatch), R)
    assert rec is not None and "�" in rec.title


# ---------------------------------------------------------------- og:image fallback


def test_og_image_used_when_json_ld_has_no_image(monkeypatch):
    ld = {k: v for k, v in RECIPE_LD.items() if k != "image"}
    page = _page(ld).replace("<head>", '<head><meta property="og:image" content="https://img.example/x.jpg">')
    handler, _ = _recorder(lambda r: httpx.Response(200, text=page))
    assert _fetch(_source(handler, monkeypatch), R).image_url == "https://img.example/x.jpg"


def test_json_ld_image_wins_over_og_image(monkeypatch):
    page = _page(RECIPE_LD).replace("<head>", '<head><meta property="og:image" content="https://o.example/o.jpg">')
    handler, _ = _recorder(lambda r: httpx.Response(200, text=page))
    assert _fetch(_source(handler, monkeypatch), R).image_url == "https://img.example/a.jpg"


@pytest.mark.parametrize(
    "content", ["http://insecure.example/x.jpg", "javascript:alert(1)", "https://x.example/" + "a" * 600, ""]
)
def test_og_image_must_be_https_and_short(content):
    assert p.extract_og_image(f'<meta property="og:image" content="{content}">') is None


# ---------------------------------------------------------------- search flow


def _search_page(*paths):
    links = "".join(f'<a href="{x}">r</a>' for x in paths)
    return f"<html><body>{links}</body></html>"


def _search_routes(request):
    if request.url.path.startswith("/rs/s0/"):
        return httpx.Response(
            200,
            text=_search_page(
                f"/rezepte/{rid(1)}/Einfaches-Moehrengemuese.html",
                f"https://www.chefkoch.de/rezepte/{rid(2)}/Karotten-Gem%C3%BCse-mit-Sahne.html?x=1#frag",
                f"https://evil.com/rezepte/{rid(3)}/c.html",  # foreign host: ignored
                f"https://www.chefkoch.de/rezepte/{rid(4)}/Moehren-Suppe.html",
                f"/forum/{rid(5)}/e.html",  # other path: ignored
                f"https://www.chefkoch.de/rezepte/{rid(6)}/Moehrenkuchen-Rueblitorte.html",
                f"https://www.chefkoch.de/rezepte/{rid(7)}/g.html",  # 4th valid link: over the per-term cap
                "/rs/s0t3/Nudeln/Rezepte.html",  # sorted variant: ignored
            ),
        )
    return httpx.Response(200, text=_page({**RECIPE_LD, "name": f"Rezept {request.url.path}"}))


def test_search_uses_plain_search_url_and_only_exact_recipe_links(monkeypatch):
    handler, seen = _recorder(_search_routes)
    src = _source(handler, monkeypatch)
    results = src.search(["Nudeln"], limit=10)
    assert str(seen[0].url) == "https://www.chefkoch.de/rs/s0/Nudeln/Rezepte.html"
    assert [r.url.raw_path.decode() for r in seen[1:]] == [
        f"/rezepte/{rid(1)}/Einfaches-Moehrengemuese.html",
        f"/rezepte/{rid(2)}/Karotten-Gem%C3%BCse-mit-Sahne.html",  # query/fragment stripped, %-escapes kept
        f"/rezepte/{rid(4)}/Moehren-Suppe.html",
    ]
    assert all(r.url.host == "www.chefkoch.de" for r in seen)
    assert len(results) == 3
    assert all(r.source_url.startswith("https://www.chefkoch.de/rezepte/") and "?" not in r.source_url for r in results)


@pytest.mark.parametrize(
    "href",
    [
        "/rezepte/1274521232808429/Einfaches-Moehrengemuese.html",
        "https://www.chefkoch.de/rezepte/3021711457340012/Moehren-Kartoffel-Puerree-mit-Sahne.html",
        "https://www.chefkoch.de/rezepte/1274521232808429/Gem%C3%BCse-Tarte_%28einfach%29.html?foo=bar#c",
        "//www.chefkoch.de/rezepte/1274521232808429/Kartoffelsuppe.html",
    ],
)
def test_real_shaped_recipe_links_survive_canonicalisation(href, monkeypatch):
    from urllib.parse import urljoin

    src = _source(lambda r: httpx.Response(200), monkeypatch)
    link = src._canonical_recipe_link(urljoin("https://www.chefkoch.de/rs/s0/Karotten/Rezepte.html", href))
    assert link is not None and link.startswith("https://www.chefkoch.de/rezepte/") and "?" not in link


def test_search_term_is_url_encoded(monkeypatch):
    handler, seen = _recorder(lambda r: httpx.Response(200, text="<html></html>"))
    _source(handler, monkeypatch).search(["Käse/Sahne ../x"], limit=5)
    assert seen[0].url.raw_path == b"/rs/s0/K%C3%A4se%2FSahne%20..%2Fx/Rezepte.html"


def test_search_request_count_is_bounded(monkeypatch):
    handler, seen = _recorder(_search_routes)
    _source(handler, monkeypatch).search(["a", "b", "c", "d"], limit=50)
    searches = [r for r in seen if r.url.path.startswith("/rs/s0/")]
    assert [r.url.path for r in searches] == ["/rs/s0/a/Rezepte.html"]  # max 1 term: the first (highest weight)
    assert len(seen) <= 4  # 1 search page + 3 recipe pages


def test_user_agent_is_descriptive(monkeypatch):
    handler, seen = _recorder(lambda r: httpx.Response(200, text="<html></html>"))
    _source(handler, monkeypatch).search(["Reis"], limit=5)
    assert "Familienplaner" in seen[0].headers["user-agent"]


def test_search_disabled_makes_no_requests(monkeypatch):
    handler, seen = _recorder(_search_routes)
    src = _source(handler, monkeypatch, enabled=False)
    assert src.search(["tomate"], limit=5) == []
    assert seen == []


def test_search_survives_network_failure_and_reports_it(monkeypatch):
    def routes(request):
        raise httpx.ConnectError("boom")

    handler, _ = _recorder(routes)
    result = _source(handler, monkeypatch).search_with_status(["tomate"], limit=5)
    assert result.candidates == [] and result.failed is True


def test_non_200_search_page_is_a_failure_but_empty_results_are_not(monkeypatch):
    handler, _ = _recorder(lambda r: httpx.Response(502))
    assert _source(handler, monkeypatch).search_with_status(["reis"]).failed is True
    handler, _ = _recorder(lambda r: httpx.Response(200, text=_search_page()))
    result = _source(handler, monkeypatch).search_with_status(["reis"])
    assert result.candidates == [] and result.failed is False


def test_single_broken_recipe_page_is_not_a_source_failure(monkeypatch):
    def routes(request):
        if request.url.path.startswith("/rs/s0/"):
            return httpx.Response(
                200, text=_search_page(f"/rezepte/{rid(1)}/a.html", f"/rezepte/{rid(2)}/b.html")
            )
        if f"/{rid(1)}/" in request.url.path:
            return httpx.Response(404)
        return httpx.Response(200, text=_page(RECIPE_LD))

    handler, _ = _recorder(routes)
    result = _source(handler, monkeypatch).search_with_status(["reis"])
    assert len(result.candidates) == 1 and result.failed is False
