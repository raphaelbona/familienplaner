"""Discovery: matching, filtering, ranking, mixing, LLM fallback and the end-to-end endpoint."""

import time
from datetime import date

import pytest

from app.core.config import Settings
from app.models.user import UserRole
from tests.food_helpers import (
    FakeSource,
    add_member,
    admin_user,
    cand,
    make_recipe,
    rate,
    register_family,
    set_prefs,
)

THIS_YEAR = date.today().year


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch):
    """LLM off unless a test switches it on; never touch real settings/.env."""
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(llm_recipe_check_enabled=False))


def _use_sources(monkeypatch, *sources):
    from app.core import recipe_discovery

    monkeypatch.setattr(recipe_discovery, "build_default_sources", lambda: list(sources))


def _family(client, db, email, child_birth=THIS_YEAR - 8):
    headers, fam = register_family(client, email)
    parent = admin_user(db, fam)
    child = add_member(db, fam, UserRole.CHILD, "Anna", child_birth)
    return headers, fam, parent, child


def _discover(client, headers, eaters, **extra):
    resp = client.post(
        "/recipes/discover",
        headers=headers,
        json={"eater_user_ids": [str(e.id) for e in eaters], "meal_type": "lunch", **extra},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------- matching unit tests


def test_term_matching_is_case_insensitive_and_compound_tolerant():
    from app.core.recipe_discovery import matches_term

    assert matches_term("nuss", "Haselnüsse")
    assert matches_term("Haselnüsse", "gehackte Nuss")
    assert matches_term("PEANUT", "peanuts")
    assert matches_term("milch", "Vollmilch 3.5%")
    assert matches_term("ei", "Eier")
    assert matches_term("ei", "2 Eier, verquirlt")
    assert matches_term("olive oil", "extra virgin olive oil")


def test_short_terms_do_not_overmatch_ordinary_words():
    from app.core.recipe_discovery import matches_term

    for word in ("Schweinefleisch", "Reis", "Feigen", "Eis", "Weizenmehl", "ein bisschen Salz", "Kleie"):
        assert not matches_term("ei", word), word


def test_allergy_alias_matches_english_names():
    from app.core.recipe_discovery import matches_term

    assert matches_term("Ei", "Eggs")
    assert matches_term("Nuss", "Walnuts")
    assert matches_term("Milch", "Milk")


def test_allergy_filter_checks_title_and_all_eaters():
    from app.core.recipe_discovery import filter_candidates_by_allergies

    assert filter_candidates_by_allergies(["Mehl"], [[], ["Nuss"]], title="Nusskuchen") is False
    assert filter_candidates_by_allergies(["Mehl", "Haselnüsse"], [[], ["Nuss"]]) is False
    assert filter_candidates_by_allergies(["Mehl"], [["Nuss"]]) is True


def test_age_check_without_birth_years_is_permissive():
    from app.core.recipe_discovery import check_age_suitability

    assert check_age_suitability([], ["Honig", "Mandeln"]) == (True, "")


def test_age_check_german_words_and_no_pepper_false_positive():
    from app.core.recipe_discovery import check_age_suitability

    assert check_age_suitability([THIS_YEAR - 2], ["Mehl", "Mandeln"])[0] is False
    assert check_age_suitability([THIS_YEAR], ["Honig"])[0] is False
    ok, hint = check_age_suitability([THIS_YEAR - 4], ["Rote Peperoni", "Paprika", "Hotdog-Brötchen"])
    assert ok and hint == ""
    ok, hint = check_age_suitability([THIS_YEAR - 4], ["Chili", "Reis"])
    assert ok and "würzig" in hint


def test_balance_heuristic():
    from app.core.recipe_discovery import assess_balance

    balanced, label = assess_balance(["Spaghetti", "Hackfleisch", "Tomaten"])
    assert balanced is True and label
    balanced, label = assess_balance(["Spaghetti", "Butter"])
    assert balanced is False and "Gemüse" in label and "Protein" in label
    assert assess_balance(["Preiselbeeren"])[0] is False  # 'reis' inside a word is not a carb


# ---------------------------------------------------------------- end to end (UUID bug)


def test_end_to_end_returns_nonempty_filtered_results(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-e2e@example.ch")
    set_prefs(db_session, fam, child, likes=["Tomate", "Pasta"], allergies=["Nuss"], dislikes=["Brokkoli"])
    source = FakeSource(
        [
            cand("Pasta Pomodoro", ["Pasta", "Tomaten", "Mozzarella", "Basilikum"], image="https://img.example/p.jpg"),
            cand("Nusskuchen", ["Mehl", "Haselnüsse", "Zucker"]),
            cand("Brokkoli Pfanne", ["BROKKOLI", "Reis"]),
            cand("Ei Salat", ["Salat", "Eier"], url="https://www.chefkoch.de/rezepte/1274521232835969/Ei.html"),
        ]
    )
    _use_sources(monkeypatch, source)
    result = _discover(client, headers, [child])
    titles = [i["title"] for i in result["discover"]]
    assert titles == ["Pasta Pomodoro", "Ei Salat"]
    item = result["discover"][0]
    assert item["ingredients"] == ["Pasta", "Tomaten", "Mozzarella", "Basilikum"]
    assert item["candidate_id"]
    assert item["age_ok"] is True and item["balanced"] is True
    assert set(item) >= {"age_hint", "balance_label", "title", "source_url", "prep_minutes"}
    assert source.calls, "the source was queried with the eaters' liked ingredients"
    assert {t.lower() for t in source.calls[0]} >= {"tomate", "pasta"}
    assert result["proven"] == []


def test_dislikes_of_any_selected_eater_filter_external_and_family_recipes(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-dis@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    set_prefs(db_session, fam, parent, dislikes=["Pilz"])
    fam_recipe = make_recipe(db_session, fam, "Pilzrisotto", ["Reis", "Champignons Pilze"])
    rate(db_session, fam, fam_recipe, parent, 5)
    ok_recipe = make_recipe(db_session, fam, "Reispfanne", ["Reis", "Erbsen"])
    rate(db_session, fam, ok_recipe, parent, 4)
    _use_sources(monkeypatch, FakeSource([cand("Pilzsuppe", ["Pilze", "Sahne"]), cand("Reissalat", ["Reis", "Mais"])]))
    result = _discover(client, headers, [child, parent])
    assert [r["title"] for r in result["proven"]] == ["Reispfanne"]
    assert [r["title"] for r in result["discover"]] == ["Reissalat"]


def test_allergy_of_selected_eater_beats_everything(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-all@example.ch")
    set_prefs(db_session, fam, parent, allergies=["Erdnuss"], likes=["Reis"])
    r = make_recipe(db_session, fam, "Satay", ["Erdnussbutter", "Reis"])
    rate(db_session, fam, r, parent, 5)
    _use_sources(monkeypatch, FakeSource([cand("Peanut Curry", ["Peanuts", "Rice"])]))
    result = _discover(client, headers, [parent])
    assert result["proven"] == [] and result["discover"] == []


def test_prefs_of_unselected_members_are_ignored(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-unsel@example.ch")
    set_prefs(db_session, fam, parent, allergies=["Reis"])
    set_prefs(db_session, fam, child, likes=["Tomate"])
    _use_sources(monkeypatch, FakeSource([cand("Reis Tomate", ["Reis", "Tomate"])]))
    assert len(_discover(client, headers, [child])["discover"]) == 1


# ---------------------------------------------------------------- proven recipes


def test_proven_uses_rating_rules_and_real_ingredients(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-proven@example.ch")
    good_url = "https://www.chefkoch.de/rezepte/1274521232864385/Gut.html"
    good = make_recipe(db_session, fam, "Gut", ["Pasta", "Tomaten"], source_url=good_url)
    edge = make_recipe(db_session, fam, "Grenzfall", ["Reis"])
    bad = make_recipe(db_session, fam, "Abgelehnt", ["Reis"], source_url="https://www.chefkoch.de/rezepte/1274521232870971/Bad.html")
    unrated = make_recipe(db_session, fam, "Unbewertet", ["Reis"])
    rate(db_session, fam, good, parent, 4)
    rate(db_session, fam, edge, parent, 2)  # avg == 2.0 -> rejected, not proven
    rate(db_session, fam, bad, parent, 1)
    rate(db_session, fam, bad, child, 2)
    _use_sources(
        monkeypatch,
        FakeSource(
            [
                cand("Nochmal Abgelehnt", ["Reis"], url="https://www.chefkoch.de/rezepte/1274521232870971/Bad.html"),
                cand("Neu", ["Reis"]),
            ]
        ),
    )
    result = _discover(client, headers, [child])
    assert [r["title"] for r in result["proven"]] == ["Gut"]
    assert sorted(result["proven"][0]["ingredients"]) == ["Pasta", "Tomaten"]
    assert result["proven"][0]["avg_stars"] == 4
    assert result["proven"][0]["id"] == str(good.id)
    # rejected recipe never comes back, neither as proven nor re-suggested via its source_url
    assert [r["title"] for r in result["discover"]] == ["Neu"]
    del unrated


def test_ratings_of_other_families_do_not_count(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-rate-a@example.ch")
    headers_b, fam_b = register_family(client, "disc-rate-b@example.ch")
    parent_b = admin_user(db_session, fam_b)
    shared = make_recipe(db_session, None, "Global", ["Reis"])
    rate(db_session, fam_b, shared, parent_b, 5)  # loved by B, unknown to A
    rate(db_session, fam, shared, parent, 1)  # rejected by A
    b_recipe = make_recipe(db_session, fam_b, "Nur B", ["Reis"])
    rate(db_session, fam_b, b_recipe, parent_b, 5)
    _use_sources(monkeypatch, FakeSource([]))
    result = _discover(client, headers, [child])
    assert result["proven"] == []


def test_proven_ranked_by_liked_ingredient_match_and_stars(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-rank@example.ch")
    set_prefs(db_session, fam, child, likes=["Tomate", "Pasta"])
    plain = make_recipe(db_session, fam, "Kartoffelsuppe", ["Kartoffeln"])
    matching = make_recipe(db_session, fam, "Pasta Pomodoro", ["Pasta", "Tomaten"])
    rate(db_session, fam, plain, parent, 5)
    rate(db_session, fam, matching, parent, 4)
    _use_sources(monkeypatch, FakeSource([]))
    result = _discover(client, headers, [child])
    assert [r["title"] for r in result["proven"]] == ["Pasta Pomodoro", "Kartoffelsuppe"]


def test_age_filter_applies_to_proven_and_discovery(client, db_session, monkeypatch):
    headers, fam, parent, baby = _family(client, db_session, "disc-age@example.ch", child_birth=THIS_YEAR)
    honey = make_recipe(db_session, fam, "Honigkuchen", ["Honig", "Mehl"])
    rate(db_session, fam, honey, parent, 5)
    honey_yogurt = cand("Honig Joghurt", ["Honig", "Joghurt"])
    _use_sources(monkeypatch, FakeSource([honey_yogurt, cand("Brei", ["Haferflocken", "Apfel"])]))
    result = _discover(client, headers, [baby])
    assert result["proven"] == []
    assert [r["title"] for r in result["discover"]] == ["Brei"]


def test_age_hint_and_balance_returned_for_proven(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-hint@example.ch", child_birth=THIS_YEAR - 4)
    r = make_recipe(db_session, fam, "Chili Reis", ["Chili", "Reis"])
    rate(db_session, fam, r, parent, 5)
    _use_sources(monkeypatch, FakeSource([]))
    item = _discover(client, headers, [child])["proven"][0]
    assert item["age_ok"] is True and "würzig" in item["age_hint"]
    assert item["balanced"] is False and "Gemüse" in item["balance_label"]


# ---------------------------------------------------------------- mixing


def _many_candidates(n):
    return [cand(f"Kandidat {i}", ["Reis", "Tomaten", "Huhn"]) for i in range(n)]


def test_mix_three_proven_and_at_most_two_discovery(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-mix1@example.ch")
    for i in range(5):
        rate(db_session, fam, make_recipe(db_session, fam, f"Bewaehrt {i}", ["Reis"]), parent, 5 - (i % 2))
    _use_sources(monkeypatch, FakeSource(_many_candidates(9)))
    result = _discover(client, headers, [child], extra_ingredients=["Reis"])
    assert len(result["proven"]) == 3 and len(result["discover"]) == 2
    assert len(result["proven"]) + len(result["discover"]) <= 8


def test_fewer_proven_are_filled_with_discovery(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-mix2@example.ch")
    rate(db_session, fam, make_recipe(db_session, fam, "Einziges", ["Reis"]), parent, 5)
    _use_sources(monkeypatch, FakeSource(_many_candidates(9)))
    result = _discover(client, headers, [child], extra_ingredients=["Reis"])
    assert len(result["proven"]) == 1
    assert len(result["discover"]) == 4
    assert len(result["proven"]) + len(result["discover"]) <= 8


def test_only_discovery_when_no_proven(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-mix3@example.ch")
    _use_sources(monkeypatch, FakeSource(_many_candidates(9)))
    result = _discover(client, headers, [child], extra_ingredients=["Reis"])
    assert result["proven"] == [] and 1 <= len(result["discover"]) <= 8


# ---------------------------------------------------------------- sources robustness


def test_failing_and_slow_sources_do_not_break_result(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "disc-src@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])

    class Slow:
        def search(self, ingredients, limit=5):
            time.sleep(2)
            return [cand("Zu langsam", ["Reis"])]

    monkeypatch.setattr(recipe_discovery, "SOURCE_TIMEOUT_SECONDS", 0.2)
    _use_sources(
        monkeypatch, FakeSource(error=RuntimeError("down")), Slow(), FakeSource([cand("Rettung", ["Reis", "Erbsen"])])
    )
    start = time.monotonic()
    result = _discover(client, headers, [child])
    assert time.monotonic() - start < 1.5
    assert [r["title"] for r in result["discover"]] == ["Rettung"]


def test_default_sources_include_site_source_only_when_enabled(monkeypatch):
    from app.core import recipe_discovery
    from app.core.recipe_sources import TheMealDB
    from app.core.recipe_sources.chefkoch import ChefkochSearchSource

    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(recipe_search_enabled=True))
    kinds = [type(s) for s in recipe_discovery.build_default_sources()]
    assert kinds == [TheMealDB, ChefkochSearchSource]
    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: Settings(recipe_search_enabled=False))
    assert [type(s) for s in recipe_discovery.build_default_sources()] == [TheMealDB]


def test_default_search_domain_is_chefkoch_only_and_unknown_domains_get_no_source(monkeypatch):
    from app.core import recipe_discovery
    from app.core.recipe_sources import TheMealDB

    assert Settings().recipe_search_domains == "chefkoch.de"
    only_swissmilk = Settings(recipe_search_enabled=True, recipe_search_domains="swissmilk.ch")
    monkeypatch.setattr(recipe_discovery, "get_settings", lambda: only_swissmilk)
    assert [type(s) for s in recipe_discovery.build_default_sources()] == [TheMealDB]


# ---------------------------------------------------------------- source health / language


class _Named(FakeSource):
    def __init__(self, name, candidates=(), error=None, failed=False):
        super().__init__(candidates, error)
        self.display_name = name
        self._failed = failed

    def search_with_status(self, ingredients, limit=5):
        from app.core.recipe_sources import SourceResult

        return SourceResult(self.search(ingredients, limit), failed=self._failed)


def test_failed_source_produces_german_warning(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "warn-fail@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    _use_sources(
        monkeypatch,
        _Named("Chefkoch.de", failed=True),
        _Named("TheMealDB", error=RuntimeError("tls")),
        _Named("Gesund", [cand("Reis Erbsen", ["Reis", "Erbsen"])]),
    )
    result = _discover(client, headers, [child])
    assert result["source_warnings"] == ["Chefkoch.de war nicht erreichbar.", "TheMealDB war nicht erreichbar."]
    assert [r["title"] for r in result["discover"]] == ["Reis Erbsen"]


def test_empty_result_gives_no_warning(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "warn-empty@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    _use_sources(monkeypatch, _Named("Chefkoch.de"), FakeSource([]))
    assert _discover(client, headers, [child])["source_warnings"] == []


def test_timed_out_source_produces_warning(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "warn-slow@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])

    class Slow(_Named):
        def search_with_status(self, ingredients, limit=5):
            time.sleep(1.5)
            return super().search_with_status(ingredients, limit)

    monkeypatch.setattr(recipe_discovery, "SOURCE_TIMEOUT_SECONDS", 0.2)
    _use_sources(monkeypatch, Slow("Chefkoch.de"))
    assert _discover(client, headers, [child])["source_warnings"] == ["Chefkoch.de war nicht erreichbar."]


def test_no_eaters_result_still_has_source_warnings_key(client, db_session, monkeypatch):
    from app.core.recipe_discovery import RecipeDiscoveryService

    result, _ = RecipeDiscoveryService([]).discover_with_candidates(
        db_session, register_family(client, "warn-none@example.ch")[1], [], "lunch"
    )
    assert result == {"proven": [], "discover": [], "source_warnings": []}


def test_items_carry_language(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "lang@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    en = cand("Rice Peas", ["Rice", "Peas"])
    en.language = "en"
    make_recipe(db_session, fam, "Reispfanne", ["Reis", "Erbsen"])
    _use_sources(monkeypatch, FakeSource([cand("Reis Mais", ["Reis", "Mais"]), en]))
    result = _discover(client, headers, [child])
    assert {i["title"]: i["language"] for i in result["discover"]} == {"Reis Mais": "de", "Rice Peas": "en"}
    assert all(i["language"] == "de" for i in result["proven"])


def test_source_failures_are_logged_at_warning_once_per_minute(caplog):
    import logging

    from app.core.recipe_sources import health

    health._last_logged.clear()
    times = iter([0.0, 10.0, 61.0, 62.0])
    original = health._clock
    health._clock = lambda: next(times)
    try:
        with caplog.at_level(logging.WARNING, logger="app.core.recipe_sources.health"):
            health.log_source_failure("Chefkoch.de", ConnectionError("secret-token-123"))
            health.log_source_failure("Chefkoch.de", ConnectionError("again"))  # rate-limited
            health.log_source_failure("Chefkoch.de", ConnectionError("later"))
            health.log_source_failure("TheMealDB", TimeoutError())
    finally:
        health._clock = original
        health._last_logged.clear()
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 3
    assert "ConnectionError" in records[0].getMessage()
    assert "secret-token-123" not in caplog.text


# ---------------------------------------------------------------- LLM


def _llm_settings(**kw):
    return Settings(llm_recipe_check_enabled=True, vision_provider="anthropic", anthropic_api_key="k", **kw)


def test_llm_not_called_when_disabled(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "disc-llm0@example.ch")
    calls = []
    monkeypatch.setattr(recipe_discovery, "check_recipe_with_llm", lambda *a, **k: calls.append(1) or {"age_ok": False})
    _use_sources(monkeypatch, FakeSource([cand("Reis", ["Reis", "Erbsen"])]))
    assert len(_discover(client, headers, [child], extra_ingredients=["Reis"])["discover"]) == 1
    assert calls == []


def test_llm_not_called_when_provider_not_configured(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "disc-llm1@example.ch")
    monkeypatch.setattr(
        recipe_discovery,
        "get_settings",
        lambda: Settings(llm_recipe_check_enabled=True, vision_provider="anthropic", anthropic_api_key=None),
    )
    calls = []
    monkeypatch.setattr(recipe_discovery, "check_recipe_with_llm", lambda *a, **k: calls.append(1) or {})
    _use_sources(monkeypatch, FakeSource([cand("Reis", ["Reis", "Erbsen"])]))
    _discover(client, headers, [child], extra_ingredients=["Reis"])
    assert calls == []


def test_llm_result_is_merged_and_can_drop_items(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "disc-llm2@example.ch")
    monkeypatch.setattr(recipe_discovery, "get_settings", _llm_settings)

    def fake_llm(age, title, ingredients, settings=None):
        if title == "Riskant":
            return {"age_ok": False, "age_hint": "zu scharf", "balanced": True}
        return {"age_ok": True, "age_hint": "Gut fuer Kinder", "balanced": False}

    monkeypatch.setattr(recipe_discovery, "check_recipe_with_llm", fake_llm)
    _use_sources(monkeypatch, FakeSource([cand("Riskant", ["Reis"]), cand("Sanft", ["Reis", "Tomaten", "Huhn"])]))
    result = _discover(client, headers, [child], extra_ingredients=["Reis"])
    assert [r["title"] for r in result["discover"]] == ["Sanft"]
    item = result["discover"][0]
    assert item["age_hint"] == "Gut fuer Kinder" and item["balanced"] is False


def test_llm_provider_failure_falls_back_to_rules(client, db_session, monkeypatch):
    from app.core import llm_text, recipe_discovery

    headers, fam, parent, child = _family(client, db_session, "disc-llm3@example.ch")
    monkeypatch.setattr(recipe_discovery, "get_settings", _llm_settings)

    def boom(*a, **k):
        raise TimeoutError("provider down")

    monkeypatch.setattr(llm_text, "_check_anthropic", boom)
    _use_sources(monkeypatch, FakeSource([cand("Sanft", ["Reis", "Tomaten", "Huhn"])]))
    item = _discover(client, headers, [child], extra_ingredients=["Reis"])["discover"][0]
    assert item["title"] == "Sanft" and item["age_ok"] is True and item["balanced"] is True


def test_llm_never_overrides_rule_based_age_block(client, db_session, monkeypatch):
    from app.core import recipe_discovery

    headers, fam, parent, baby = _family(client, db_session, "disc-llm4@example.ch", child_birth=THIS_YEAR)
    monkeypatch.setattr(recipe_discovery, "get_settings", _llm_settings)
    monkeypatch.setattr(
        recipe_discovery, "check_recipe_with_llm", lambda *a, **k: {"age_ok": True, "age_hint": "", "balanced": True}
    )
    _use_sources(monkeypatch, FakeSource([cand("Honigbrot", ["Honig", "Brot"])]))
    assert _discover(client, headers, [baby], extra_ingredients=["Brot"])["discover"] == []


def test_llm_text_module_validates_and_falls_back(monkeypatch):
    import httpx

    from app.core import llm_text

    ollama = Settings(llm_recipe_check_enabled=True, vision_provider="ollama")
    assert llm_text.is_llm_configured(ollama) is True
    assert llm_text.is_llm_configured(Settings(llm_recipe_check_enabled=False, vision_provider="ollama")) is False
    assert llm_text.is_llm_configured(_llm_settings()) is True
    no_key = Settings(llm_recipe_check_enabled=True, vision_provider="anthropic", anthropic_api_key=None)
    assert llm_text.is_llm_configured(no_key) is False

    class Resp:
        def __init__(self, text):
            self._t = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"response": self._t}

    good = 'Sure! {"age_ok": true, "age_hint": "ok", "balanced": false} bye'
    monkeypatch.setattr(httpx, "post", lambda *a, **k: Resp(good))
    verdict = llm_text.check_recipe_with_llm(5, "T", ["x"], ollama)
    assert verdict == {"age_ok": True, "age_hint": "ok", "balanced": False}
    for garbage in ("no json here", '{"age_ok": "yes"}', "{broken", '["list"]'):
        monkeypatch.setattr(httpx, "post", lambda *a, _g=garbage, **k: Resp(_g))
        assert llm_text.check_recipe_with_llm(5, "T", ["x"], ollama) is None

    def boom(*a, **k):
        raise httpx.ConnectError("x")

    monkeypatch.setattr(httpx, "post", boom)
    assert llm_text.check_recipe_with_llm(5, "T", ["x"], ollama) is None


# ---------------------------------------------------------------- endpoint validation


def test_discover_rejects_invalid_input_with_422(client, db_session):
    headers, fam, parent, child = _family(client, db_session, "disc-val@example.ch")
    ok = {"eater_user_ids": [str(child.id)], "meal_type": "lunch"}
    bad_bodies = [
        {**ok, "meal_type": "brunch"},
        {**ok, "eater_user_ids": []},
        {**ok, "eater_user_ids": ["not-a-uuid"]},
        {**ok, "eater_user_ids": [str(child.id)] * 11},
        {**ok, "extra_ingredients": ["x" * 61]},
        {**ok, "extra_ingredients": ["a"] * 11},
        {"meal_type": "lunch"},
    ]
    for body in bad_bodies:
        assert client.post("/recipes/discover", headers=headers, json=body).status_code == 422, body


def test_discover_eater_from_other_family_is_404(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "disc-x-a@example.ch")
    _, fam_b, _, child_b = _family(client, db_session, "disc-x-b@example.ch")
    _use_sources(monkeypatch, FakeSource([]))
    resp = client.post("/recipes/discover", headers=headers, json={"eater_user_ids": [str(child_b.id)]})
    assert resp.status_code == 404


def test_discover_requires_auth_and_parent(client, db_session):
    assert client.post("/recipes/discover", json={"eater_user_ids": []}).status_code == 401


def test_search_terms_are_ordered_by_weight_most_wanted_first(client, db_session, monkeypatch):
    headers, fam, parent, child = _family(client, db_session, "terms-order@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis", "Erbsen", "Mais"])
    set_prefs(db_session, fam, parent, likes=["Erbsen", "Mais"])
    source = FakeSource([])
    _use_sources(monkeypatch, source)
    _discover(client, headers, [child, parent], extra_ingredients=["Karotten"])
    assert source.calls[0] == ["Karotten", "Erbsen", "Mais", "Reis"]  # extra (+1) > liked by both > liked by one


def test_overload_warning_text_is_passed_through(client, db_session, monkeypatch):
    from app.core.recipe_sources import SourceResult

    class Overloaded(_Named):
        def search_with_status(self, ingredients, limit=5):
            return SourceResult(
                [], failed=True, warning="Chefkoch.de ist gerade überlastet – bitte später erneut versuchen."
            )

    headers, fam, parent, child = _family(client, db_session, "overload-warn@example.ch")
    set_prefs(db_session, fam, child, likes=["Reis"])
    _use_sources(monkeypatch, Overloaded("Chefkoch.de"))
    assert _discover(client, headers, [child])["source_warnings"] == [
        "Chefkoch.de ist gerade überlastet – bitte später erneut versuchen."
    ]
