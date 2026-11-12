"""MealieClient._resolve_new_ingredient_refs / _resolve_ingredient_ref.

Mealie's own recipe-PATCH endpoint cannot create a brand-new food/unit on the fly —
confirmed live against a real Mealie instance: referencing a food/unit without an
existing id raises a server-side ValueError (HTTP 500), silently discarded by
improve_recipe_ingredients' broad except, which is why re-parsed ingredients never
actually gained quantity/unit/food before this fix. These tests exercise the
create-or-find logic directly against a MealieClient, without the full import flow.
"""
from custom_components.mealie_hub.api import MealieClient

from .conftest import MEALIE


def _client(hass):
    return MealieClient(hass, MEALIE, "mealie-key", True)


async def test_resolves_a_brand_new_food_by_creating_it(hass, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/foods", json={"id": "food-1", "name": "Spitzkohl"})
    ingredients = [{"food": {"name": "Spitzkohl"}, "unit": None}]
    await _client(hass)._resolve_new_ingredient_refs(ingredients)
    assert ingredients[0]["food"]["id"] == "food-1"


async def test_resolves_a_brand_new_unit_by_creating_it(hass, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/units", json={"id": "unit-1", "name": "Prise"})
    ingredients = [{"food": None, "unit": {"name": "Prise"}}]
    await _client(hass)._resolve_new_ingredient_refs(ingredients)
    assert ingredients[0]["unit"]["id"] == "unit-1"


async def test_an_ingredient_that_already_has_an_id_is_left_alone(hass, aioclient_mock):
    # No POST /api/foods mock registered at all: if the code tried to (re-)create a
    # food that already carries an id, aioclient_mock would raise for the unmocked call.
    ingredients = [{"food": {"id": "existing", "name": "Mehl"}, "unit": None}]
    await _client(hass)._resolve_new_ingredient_refs(ingredients)
    assert ingredients[0]["food"]["id"] == "existing"


async def test_a_name_collision_falls_back_to_finding_the_existing_food(hass, aioclient_mock):
    # Mealie's own uniqueness constraint on (name, group): creating a food whose name
    # already exists answers 409, with no id in the body — the real id has to come
    # from a follow-up search.
    aioclient_mock.post(f"{MEALIE}/api/foods", status=409, text="already exists")
    aioclient_mock.get(
        f"{MEALIE}/api/foods",
        json={"items": [{"id": "wrong", "name": "lemon juice"}, {"id": "right", "name": "Parmesan"}]},
    )
    ingredients = [{"food": {"name": "Parmesan"}, "unit": None}]
    await _client(hass)._resolve_new_ingredient_refs(ingredients)
    assert ingredients[0]["food"]["id"] == "right"


async def test_the_same_food_name_twice_is_only_created_once(hass, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/foods", json={"id": "food-1", "name": "Zwiebel"})
    ingredients = [
        {"food": {"name": "Zwiebel"}, "unit": None},
        {"food": {"name": "Zwiebel"}, "unit": None},
    ]
    await _client(hass)._resolve_new_ingredient_refs(ingredients)
    assert ingredients[0]["food"]["id"] == "food-1"
    assert ingredients[1]["food"]["id"] == "food-1"
    assert len([c for c in aioclient_mock.mock_calls if c[0].lower() == "post"]) == 1
