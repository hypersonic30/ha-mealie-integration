"""Proxy views: recipe list/detail, import routing (Mealie vs. Social-to-Mealie)."""
from .conftest import MEALIE, SOCIAL


async def test_recipes_adds_auth_header_and_image_url(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes", json={"items": [{"id": "abc", "slug": "soup", "name": "Soup"}]})
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/recipes")
    assert resp.status == 200
    body = await resp.json()
    assert body["items"][0]["image_url"] == "/api/mealie_hub/recipes/abc/image"
    _, _, _, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"] == "Bearer mealie-key"


async def test_recipe_detail(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes/soup", json={"id": "abc", "slug": "soup", "recipeIngredient": []})
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/recipes/soup")
    assert resp.status == 200
    body = await resp.json()
    assert body["image_url"] == "/api/mealie_hub/recipes/abc/image"


async def test_delete_recipe(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.delete(f"{MEALIE}/api/recipes/soup", json={"id": "abc", "slug": "soup"})
    client = await hass_client()
    resp = await client.delete("/api/mealie_hub/recipes/soup")
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True}


async def test_delete_recipe_upstream_error_is_forwarded(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.delete(f"{MEALIE}/api/recipes/soup", status=404, text="not found")
    client = await hass_client()
    resp = await client.delete("/api/mealie_hub/recipes/soup")
    assert resp.status == 404


def _no_existing_recipe(aioclient_mock):
    """The duplicate check (GET /api/recipes) finds nothing by this URL."""
    aioclient_mock.get(f"{MEALIE}/api/recipes", json={"items": []})


def _allow_tag_strip(aioclient_mock, slug):
    """A successful import always gets its tags stripped, then an empty-tag sweep."""
    aioclient_mock.patch(f"{MEALIE}/api/recipes/{slug}", json={"slug": slug, "tags": []})
    aioclient_mock.get(f"{MEALIE}/api/organizers/tags", json={"items": []})


async def test_import_normal_url_goes_to_mealie(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/soup"})
    assert resp.status == 200
    assert await resp.json() == {"slug": "soup"}
    create_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/create/url"))
    assert create_call[2]["includeTags"] is False  # tagging is manual-only, never auto-assigned
    assert any(c[0] == "PATCH" and c[2].get("tags") == [] for c in aioclient_mock.mock_calls)


async def test_import_instagram_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": {"slug": "insta-soup"}, "progress": "done"},
    )
    _allow_tag_strip(aioclient_mock, "insta-soup")
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.instagram.com/reel/abc123/"}
    )
    assert resp.status == 200
    assert await resp.json() == {"slug": "insta-soup"}
    # Social-to-Mealie hardcodes includeTags=true on its own end, so the strip
    # afterwards is what actually keeps this path tag-free, not a request flag.
    assert any(c[0] == "PATCH" and c[2].get("tags") == [] for c in aioclient_mock.mock_calls)


async def test_import_youtube_shorts_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": {"slug": "short-soup"}, "progress": "done"},
    )
    _allow_tag_strip(aioclient_mock, "short-soup")
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.youtube.com/shorts/abc123"}
    )
    assert resp.status == 200
    assert await resp.json() == {"slug": "short-soup"}


async def test_import_normal_youtube_video_stays_with_mealie(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.youtube.com/watch?v=abc123"}
    )
    assert resp.status == 200
    assert not any(str(c[1]).endswith("/get-url") for c in aioclient_mock.mock_calls)


async def test_import_rejects_non_url(hass, setup_entry, hass_client, aioclient_mock):
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "not a url"})
    assert resp.status == 400
    assert aioclient_mock.call_count == 0


async def test_import_upstream_error_is_forwarded(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", status=500, text="boom")
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/soup"})
    assert resp.status == 500


async def test_import_blocks_an_existing_recipe(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(
        f"{MEALIE}/api/recipes",
        json={"items": [{"slug": "soup", "orgURL": "https://example.com/soup"}]},
    )
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/soup"})
    assert resp.status == 409
    assert await resp.json() == {"error": "duplicate", "slug": "soup"}
    assert aioclient_mock.call_count == 1  # only the duplicate check — no import was attempted


async def test_import_force_skips_the_duplicate_check(hass, setup_entry, hass_client, aioclient_mock):
    # No GET /api/recipes mock is registered at all: if the duplicate check ran
    # despite force=true, aioclient_mock would raise for the unmocked call.
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://example.com/soup", "force": True}
    )
    assert resp.status == 200
    assert await resp.json() == {"slug": "soup"}


async def test_recipes_forwards_tag_filter(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes", json={"items": []})
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/recipes?tags=vegan,quick")
    assert resp.status == 200
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query.getall("tags") == ["vegan", "quick"]


async def test_tags_list(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/organizers/tags", json={"items": [{"id": "t1", "name": "vegan", "slug": "vegan"}]})
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/tags")
    assert resp.status == 200
    assert (await resp.json())["items"][0]["name"] == "vegan"


async def test_set_recipe_tags(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.patch(f"{MEALIE}/api/recipes/soup", json={"slug": "soup", "tags": [{"name": "vegan"}]})
    aioclient_mock.get(f"{MEALIE}/api/organizers/tags", json={"items": [{"id": "t-vegan", "name": "vegan", "recipeCount": 1}]})
    client = await hass_client()
    resp = await client.patch("/api/mealie_hub/recipes/soup/tags", json={"tags": ["vegan"]})
    assert resp.status == 200
    patch_call = next(c for c in aioclient_mock.mock_calls if c[0] == "PATCH")
    assert patch_call[2] == {"tags": [{"name": "vegan"}]}
    # "vegan" still has a recipe (count 1), so the empty-tag sweep leaves it alone.
    assert not any(c[0] == "DELETE" for c in aioclient_mock.mock_calls)


async def test_set_recipe_tags_deletes_now_empty_tags(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.patch(f"{MEALIE}/api/recipes/soup", json={"slug": "soup", "tags": []})
    aioclient_mock.get(
        f"{MEALIE}/api/organizers/tags",
        json={"items": [
            {"id": "t-vegan", "name": "vegan", "recipeCount": 0},
            {"id": "t-schnell", "name": "schnell", "recipeCount": 2},
        ]},
    )
    aioclient_mock.delete(f"{MEALIE}/api/organizers/tags/t-vegan", status=204)
    client = await hass_client()
    resp = await client.patch("/api/mealie_hub/recipes/soup/tags", json={"tags": []})
    assert resp.status == 200
    deletes = [str(c[1]) for c in aioclient_mock.mock_calls if c[0] == "DELETE"]
    assert deletes == [f"{MEALIE}/api/organizers/tags/t-vegan"]  # only the orphaned one, not "schnell"


async def test_shopping_list_returns_items(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": [{"id": "l1"}]})
    aioclient_mock.get(
        f"{MEALIE}/api/households/shopping/lists/l1",
        json={"id": "l1", "name": "Einkaufsliste", "listItems": [{"id": "i1", "display": "1 Banane", "checked": False}]},
    )
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/shopping-list")
    assert resp.status == 200
    body = await resp.json()
    assert body["items"][0]["display"] == "1 Banane"


async def test_shopping_list_missing_is_404(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": []})
    client = await hass_client()
    resp = await client.get("/api/mealie_hub/shopping-list")
    assert resp.status == 404
    assert (await resp.json())["error"] == "no_shopping_list"


async def test_check_shopping_item(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": [{"id": "l1"}]})
    aioclient_mock.get(
        f"{MEALIE}/api/households/shopping/lists/l1",
        json={"id": "l1", "listItems": [{"id": "i1", "display": "1 Banane", "checked": False}]},
    )
    aioclient_mock.put(f"{MEALIE}/api/households/shopping/items/i1", json={"id": "i1", "checked": True})
    client = await hass_client()
    resp = await client.put("/api/mealie_hub/shopping-list/items/i1", json={"checked": True})
    assert resp.status == 200
    _, _, body, _ = aioclient_mock.mock_calls[-1]
    assert body["checked"] is True
    assert body["display"] == "1 Banane"  # the full known item is sent back, not just the changed field


async def test_check_shopping_item_not_found(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": [{"id": "l1"}]})
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists/l1", json={"id": "l1", "listItems": []})
    client = await hass_client()
    resp = await client.put("/api/mealie_hub/shopping-list/items/missing", json={"checked": True})
    assert resp.status == 404


async def test_add_recipe_to_shopping_list(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": [{"id": "l1"}]})
    aioclient_mock.post(f"{MEALIE}/api/households/shopping/lists/l1/recipe", json={"id": "l1"})
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/recipes/r1/shopping-list")
    assert resp.status == 200
    _, _, body, _ = aioclient_mock.mock_calls[-1]
    assert body == [{"recipeId": "r1", "recipe_id": "r1"}]


async def test_delete_shopping_item(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.delete(f"{MEALIE}/api/households/shopping/items/i1", status=204)
    client = await hass_client()
    resp = await client.delete("/api/mealie_hub/shopping-list/items/i1")
    assert resp.status == 200
    assert (await resp.json()) == {"ok": True}


async def test_delete_shopping_item_upstream_error(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.delete(f"{MEALIE}/api/households/shopping/items/i1", status=404, text="not found")
    client = await hass_client()
    resp = await client.delete("/api/mealie_hub/shopping-list/items/i1")
    assert resp.status == 404


async def test_clear_checked_shopping_items(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": [{"id": "l1"}]})
    aioclient_mock.get(
        f"{MEALIE}/api/households/shopping/lists/l1",
        json={"id": "l1", "listItems": [
            {"id": "i1", "display": "1 Banane", "checked": True},
            {"id": "i2", "display": "1 Apfel", "checked": False},
            {"id": "i3", "display": "Milch", "checked": True},
        ]},
    )
    aioclient_mock.delete(f"{MEALIE}/api/households/shopping/items/i1", status=204)
    aioclient_mock.delete(f"{MEALIE}/api/households/shopping/items/i3", status=204)
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/shopping-list/clear-checked")
    assert resp.status == 200
    assert (await resp.json()) == {"removed": 2}
    deleted = sorted(str(c[1]).rsplit("/", 1)[-1] for c in aioclient_mock.mock_calls if c[0] == "DELETE")
    assert deleted == ["i1", "i3"]  # the unchecked item (i2) is never touched


async def test_clear_checked_shopping_items_no_list(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/households/shopping/lists", json={"items": []})
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/shopping-list/clear-checked")
    assert resp.status == 404
    assert (await resp.json())["error"] == "no_shopping_list"
