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


async def test_import_normal_url_goes_to_mealie(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/soup"})
    assert resp.status == 200
    assert await resp.json() == {"slug": "soup"}
    assert aioclient_mock.call_count == 1


async def test_import_instagram_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": {"slug": "insta-soup"}, "progress": "done"},
    )
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.instagram.com/reel/abc123/"}
    )
    assert resp.status == 200
    assert await resp.json() == {"slug": "insta-soup"}
    assert aioclient_mock.call_count == 1


async def test_import_youtube_shorts_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": {"slug": "short-soup"}, "progress": "done"},
    )
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.youtube.com/shorts/abc123"}
    )
    assert resp.status == 200
    assert await resp.json() == {"slug": "short-soup"}


async def test_import_normal_youtube_video_stays_with_mealie(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    client = await hass_client()
    resp = await client.post(
        "/api/mealie_hub/import", json={"url": "https://www.youtube.com/watch?v=abc123"}
    )
    assert resp.status == 200
    assert aioclient_mock.call_count == 1


async def test_import_rejects_non_url(hass, setup_entry, hass_client, aioclient_mock):
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "not a url"})
    assert resp.status == 400
    assert aioclient_mock.call_count == 0


async def test_import_upstream_error_is_forwarded(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", status=500, text="boom")
    client = await hass_client()
    resp = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/soup"})
    assert resp.status == 500
