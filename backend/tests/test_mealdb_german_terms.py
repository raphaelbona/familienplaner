"""TheMealDB only knows English ingredient names: German terms are mapped, unknown ones skipped. No live network."""

import httpx
import pytest

from app.core.recipe_sources import TheMealDB
from app.core.recipe_sources.de_en_ingredients import to_mealdb_ingredient


@pytest.mark.parametrize(
    ("term", "english"),
    [
        ("Karotten", "carrots"),
        ("karotte", "carrots"),
        ("Möhren", "carrots"),
        ("Nudeln", "spaghetti"),
        ("Pasta", "spaghetti"),
        ("Eier", "eggs"),
        ("Ei", "eggs"),
        ("Äpfel", "apples"),
        ("Kartoffeln", "potatoes"),
        ("Süßkartoffeln", "sweet_potatoes"),
        ("Käse", "cheese"),
        ("KÄSE", "cheese"),
        ("Hähnchenbrust", "chicken_breast"),
        ("Erbsen", "peas"),
        ("Zucchini", "courgettes"),
        ("Tomaten", "tomatoes"),
        ("  Reis ", "rice"),
        ("chicken", "chicken"),  # already English
        ("Tomato", "tomato"),
        ("Sweet potatoes", "sweet_potatoes"),
    ],
)
def test_terms_are_mapped(term, english):
    assert to_mealdb_ingredient(term) == english


@pytest.mark.parametrize("term", ["", "  ", "Xylophon", "Schwarzwälder Kirschtorte", "a", "x" * 200])
def test_unknown_terms_are_not_mapped(term):
    assert to_mealdb_ingredient(term) is None


def _fake_get(calls):
    class Resp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

        def raise_for_status(self):
            pass

    def get(url, *args, **kwargs):
        calls.append((url, kwargs.get("params")))
        if "filter.php" in url:
            return Resp({"meals": [{"idMeal": "1"}]})
        return Resp({"meals": [{"idMeal": "1", "strMeal": "Carrot Soup", "strIngredient1": "Carrots"}]})

    return get


def test_german_term_is_translated_for_the_request_and_result_is_marked_english(monkeypatch):
    calls: list = []
    monkeypatch.setattr(httpx, "get", _fake_get(calls))
    results = TheMealDB().search(["Karotten"], limit=5)
    assert calls[0][1] == {"i": "carrots"}
    assert [r.language for r in results] == ["en"]
    assert results[0].title == "Carrot Soup"  # never machine-translated


def test_unknown_terms_are_skipped_without_http_call(monkeypatch):
    calls: list = []
    monkeypatch.setattr(httpx, "get", _fake_get(calls))
    result = TheMealDB().search_with_status(["Xylophon", "Schwarzwälder Kirschtorte"], limit=5)
    assert calls == []
    assert result.candidates == [] and result.failed is False  # nothing to ask is not a failure


def test_only_mapped_terms_cause_requests_and_duplicates_collapse(monkeypatch):
    calls: list = []
    monkeypatch.setattr(httpx, "get", _fake_get(calls))
    TheMealDB().search(["Xylophon", "Karotten", "Möhren", "carrots"], limit=5)
    assert [c[1] for c in calls if "filter.php" in c[0]] == [{"i": "carrots"}]


def test_network_failure_is_reported_but_empty_result_is_not(monkeypatch):
    def boom(url, *args, **kwargs):
        raise httpx.ConnectError("tls")

    monkeypatch.setattr(httpx, "get", boom)
    assert TheMealDB().search_with_status(["Reis"]).failed is True

    class Empty:
        def json(self):
            return {"meals": None}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Empty())
    result = TheMealDB().search_with_status(["Reis"])
    assert result.candidates == [] and result.failed is False
