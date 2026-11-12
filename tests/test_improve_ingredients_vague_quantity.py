"""MealieClient.improve_recipe_ingredients: vague-quantity words ("eine kleine
Menge", "etwas", ...) must not end up baked into the food name — Mealie's own
parser has no concept of them and would otherwise store "eine kleine Menge
Butter" as one food, quantity 0, no unit (confirmed live). They must also not
just disappear: the amount info ("don't use a lot of this") gets moved onto
the parsed ingredient's note instead.
"""
from custom_components.mealie_hub.api import MealieClient

from .conftest import MEALIE


def _client(hass):
    return MealieClient(hass, MEALIE, "mealie-key", True)


async def test_vague_quantity_moves_to_the_note_instead_of_the_food_name(hass, aioclient_mock):
    aioclient_mock.get(
        f"{MEALIE}/api/recipes/zwiebelsuppe",
        json={"recipeIngredient": [{"note": "eine kleine Menge Butter", "display": ""}]},
    )
    aioclient_mock.post(
        f"{MEALIE}/api/parser/ingredients",
        json=[
            {
                "ingredient": {
                    "note": "",
                    "quantity": 0,
                    "unit": None,
                    "food": {"name": "Butter"},
                    "display": "Butter",
                }
            }
        ],
    )
    aioclient_mock.post(f"{MEALIE}/api/foods", json={"id": "food-butter", "name": "Butter"})
    aioclient_mock.patch(f"{MEALIE}/api/recipes/zwiebelsuppe", json={})

    await _client(hass).improve_recipe_ingredients("zwiebelsuppe")

    parse_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/parser/ingredients"))
    assert parse_call[2]["ingredients"] == ["Butter"]  # the prefix never reached the parser

    patch_call = next(c for c in aioclient_mock.mock_calls if c[0].lower() == "patch")
    patched_ingredient = patch_call[2]["recipeIngredient"][0]
    assert patched_ingredient["food"]["name"] == "Butter"
    assert patched_ingredient["note"] == "eine kleine Menge"


async def test_a_vague_quantity_combines_with_an_existing_note(hass, aioclient_mock):
    aioclient_mock.get(
        f"{MEALIE}/api/recipes/zwiebelsuppe",
        json={"recipeIngredient": [{"note": "etwas frischer Knoblauch ohne Keim/Strunk", "display": ""}]},
    )
    aioclient_mock.post(
        f"{MEALIE}/api/parser/ingredients",
        json=[
            {
                "ingredient": {
                    "note": "ohne Keim/Strunk",
                    "quantity": 0,
                    "unit": None,
                    "food": {"name": "frischer Knoblauch"},
                    "display": "frischer Knoblauch ohne Keim/Strunk",
                }
            }
        ],
    )
    aioclient_mock.post(f"{MEALIE}/api/foods", json={"id": "food-knoblauch", "name": "frischer Knoblauch"})
    aioclient_mock.patch(f"{MEALIE}/api/recipes/zwiebelsuppe", json={})

    await _client(hass).improve_recipe_ingredients("zwiebelsuppe")

    patch_call = next(c for c in aioclient_mock.mock_calls if c[0].lower() == "patch")
    patched_ingredient = patch_call[2]["recipeIngredient"][0]
    assert patched_ingredient["food"]["name"] == "frischer Knoblauch"
    assert patched_ingredient["note"] == "etwas, ohne Keim/Strunk"


async def test_a_real_quantity_is_left_completely_untouched(hass, aioclient_mock):
    aioclient_mock.get(
        f"{MEALIE}/api/recipes/zwiebelsuppe",
        json={"recipeIngredient": [{"note": "300g Schleifchennudeln", "display": ""}]},
    )
    aioclient_mock.post(
        f"{MEALIE}/api/parser/ingredients",
        json=[
            {
                "ingredient": {
                    "note": "",
                    "quantity": 300,
                    "unit": {"name": "gram"},
                    "food": {"name": "Schleifchennudeln"},
                    "display": "300 grams Schleifchennudeln",
                }
            }
        ],
    )
    aioclient_mock.post(f"{MEALIE}/api/foods", json={"id": "food-nudeln", "name": "Schleifchennudeln"})
    aioclient_mock.post(f"{MEALIE}/api/units", json={"id": "unit-gram", "name": "gram"})
    aioclient_mock.patch(f"{MEALIE}/api/recipes/zwiebelsuppe", json={})

    await _client(hass).improve_recipe_ingredients("zwiebelsuppe")

    parse_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/api/parser/ingredients"))
    assert parse_call[2]["ingredients"] == ["300g Schleifchennudeln"]

    patch_call = next(c for c in aioclient_mock.mock_calls if c[0].lower() == "patch")
    patched_ingredient = patch_call[2]["recipeIngredient"][0]
    assert patched_ingredient["note"] == ""
