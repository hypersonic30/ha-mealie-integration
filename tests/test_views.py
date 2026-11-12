"""Proxy views: recipe list/detail, import routing (Mealie vs. Social-to-Mealie)."""
import asyncio

import aiohttp
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

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


async def _create_named_ref(method, url, data):
    """Stands in for Mealie's own /api/foods or /api/units: hands back a made-up but
    stable id for whatever name was posted — exactly what a brand-new food/unit needs
    before it can be referenced from a recipe's ingredient list."""
    return AiohttpClientMockResponse(method, url, json={"id": f"ref-{data['name']}", "name": data["name"]})


def _allow_tag_strip(aioclient_mock, slug, *, description="", servings=0, ingredient_notes=None):
    """A successful import always gets its tags stripped (+ an empty-tag sweep), its
    ingredients re-parsed, and a servings guess attempted — all best-effort cleanup.
    With no ingredients/description given, the latter two are no-ops (nothing to parse,
    nothing to match), which is what every test not specifically about them needs."""
    aioclient_mock.patch(f"{MEALIE}/api/recipes/{slug}", json={"slug": slug, "tags": []})
    aioclient_mock.get(f"{MEALIE}/api/organizers/tags", json={"items": []})
    notes = ingredient_notes or []
    aioclient_mock.get(
        f"{MEALIE}/api/recipes/{slug}",
        json={
            "slug": slug,
            "description": description,
            "recipeServings": servings,
            "recipeIngredient": [{"note": t, "display": t} for t in notes],
        },
    )
    if notes:
        aioclient_mock.post(
            f"{MEALIE}/api/parser/ingredients",
            json=[{"input": t, "ingredient": {"note": "", "quantity": 1, "food": {"name": t}, "display": t}} for t in notes],
        )
        # Every parsed food here is brand-new (no id) — Mealie can't create those on
        # the fly from the recipe PATCH itself (a real server-side bug), so the code
        # under test must create each one through /api/foods first.
        aioclient_mock.post(f"{MEALIE}/api/foods", side_effect=_create_named_ref)


async def _run_import(hass, client, url, *, force=False):
    """Submits a link (which now only *enqueues* it — the actual work runs as a
    background task, see import_queue.py), waits for the background worker to finish,
    and returns the resulting history entry."""
    resp = await client.post("/api/mealie_hub/import", json={"url": url, "force": force})
    assert resp.status in (202, 409)
    if resp.status == 409:
        return {"duplicate_response": await resp.json()}
    await hass.async_block_till_done()
    status = await (await client.get("/api/mealie_hub/import")).json()
    assert status["running"] is None
    assert status["queue"] == []
    return status["history"][0]


async def test_import_normal_url_goes_to_mealie(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    entry = await _run_import(hass, client, "https://example.com/soup")
    assert entry == {"url": "https://example.com/soup", "slug": "soup"}
    create_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/create/url"))
    assert create_call[2]["includeTags"] is False  # tagging is manual-only, never auto-assigned
    assert any(c[0] == "PATCH" and c[2].get("tags") == [] for c in aioclient_mock.mock_calls)


async def test_import_reparses_ingredients_left_as_plain_notes(hass, setup_entry, hass_client, aioclient_mock):
    # Mealie's create path never runs its own ingredient parser — every string lands
    # whole in `note`. The import should re-parse and PATCH the structured result back.
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup", ingredient_notes=["200g Mehl", "2 Eier"])
    client = await hass_client()
    await _run_import(hass, client, "https://example.com/soup")
    parse_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/parser/ingredients"))
    assert parse_call[2] == {"parser": "nlp", "ingredients": ["200g Mehl", "2 Eier"]}
    ingredient_patch = next(c for c in aioclient_mock.mock_calls if c[0] == "PATCH" and "recipeIngredient" in c[2])
    assert ingredient_patch[2]["recipeIngredient"] == [
        {"note": "", "quantity": 1, "food": {"name": "200g Mehl", "id": "ref-200g Mehl"}, "display": "200g Mehl"},
        {"note": "", "quantity": 1, "food": {"name": "2 Eier", "id": "ref-2 Eier"}, "display": "2 Eier"},
    ]


async def test_import_guesses_servings_from_description(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup", description="Leckeres Gericht für 4 Personen")
    client = await hass_client()
    await _run_import(hass, client, "https://example.com/soup")
    servings_patch = next(c for c in aioclient_mock.mock_calls if c[0] == "PATCH" and "recipeServings" in c[2])
    assert servings_patch[2] == {"recipeServings": 4.0}


async def test_import_never_overrides_an_existing_servings_value(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup", description="für 4 Personen", servings=2)
    client = await hass_client()
    await _run_import(hass, client, "https://example.com/soup")
    assert not any(c[0] == "PATCH" and "recipeServings" in c[2] for c in aioclient_mock.mock_calls)


def _social_created_recipe(slug):
    """Social-to-Mealie's real response shape (confirmed from its source): a
    hand-built {name, description, imageUrl, url} object with NO slug field at
    all — the slug only exists as the last path segment of `url`. Live bug: our
    code used to read a (nonexistent) top-level `slug` key, which meant every
    single Social-to-Mealie import silently skipped all post-import work, with
    nothing logged (no exception — `if slug:` was just always False)."""
    return {
        "name": slug,
        "description": "",
        "imageUrl": f"{MEALIE}/api/media/recipes/abc/images/original.webp",
        "url": f"{MEALIE}/g/home/r/{slug}",
    }


async def test_import_instagram_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": _social_created_recipe("insta-soup"), "progress": "done"},
    )
    _allow_tag_strip(aioclient_mock, "insta-soup")
    client = await hass_client()
    entry = await _run_import(hass, client, "https://www.instagram.com/reel/abc123/")
    assert entry == {"url": "https://www.instagram.com/reel/abc123/", "slug": "insta-soup"}
    # Social-to-Mealie hardcodes includeTags=true on its own end, so the strip
    # afterwards is what actually keeps this path tag-free, not a request flag.
    assert any(c[0] == "PATCH" and c[2].get("tags") == [] for c in aioclient_mock.mock_calls)


async def test_import_youtube_shorts_goes_to_social(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(
        f"{SOCIAL}/api/get-url",
        json={"createdRecipe": _social_created_recipe("short-soup"), "progress": "done"},
    )
    _allow_tag_strip(aioclient_mock, "short-soup")
    client = await hass_client()
    entry = await _run_import(hass, client, "https://www.youtube.com/shorts/abc123")
    assert entry == {"url": "https://www.youtube.com/shorts/abc123", "slug": "short-soup"}


class _TextPlainJsonResponse(AiohttpClientMockResponse):
    """Mimics Social-to-Mealie's real behavior: a JSON body sent without a
    Content-Type header, so real aiohttp treats it as text/plain and resp.json()
    raises ContentTypeError even though resp.text() holds a perfectly valid body."""

    async def json(self, encoding=None, content_type=None, loads=None):
        raise aiohttp.ContentTypeError(None, (), message="text/plain;charset=utf-8")


async def test_import_survives_social_to_mealies_mislabeled_json_response(
    hass, setup_entry, hass_client, aioclient_mock
):
    # Live bug: Social-to-Mealie's non-streaming /api/get-url response never sets
    # Content-Type, so aiohttp labels a successful (200) JSON body as text/plain and
    # resp.json() raises ContentTypeError — before anything even looks at the body.
    _no_existing_recipe(aioclient_mock)

    async def _mislabeled_success(method, url, data):
        return _TextPlainJsonResponse(
            method, url, json={"createdRecipe": _social_created_recipe("insta-soup"), "progress": "done"}
        )

    aioclient_mock.post(f"{SOCIAL}/api/get-url", side_effect=_mislabeled_success)
    _allow_tag_strip(aioclient_mock, "insta-soup")
    client = await hass_client()
    entry = await _run_import(hass, client, "https://www.instagram.com/reel/abc123/")
    assert entry == {"url": "https://www.instagram.com/reel/abc123/", "slug": "insta-soup"}


async def test_import_reports_a_clear_timeout_instead_of_unexpected_error(
    hass, setup_entry, hass_client, aioclient_mock
):
    # Live bug: the video pipeline can outrun IMPORT_TIMEOUT on slow hardware even
    # though Social-to-Mealie keeps working and the recipe may still get created —
    # a raw TimeoutError on the main import call must produce a clear "timeout"
    # entry (and never leave "running" stuck), not the generic unexpected_error.
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{SOCIAL}/api/get-url", exc=TimeoutError("boom"))
    client = await hass_client()
    entry = await _run_import(hass, client, "https://www.instagram.com/reel/abc123/")
    assert entry == {"url": "https://www.instagram.com/reel/abc123/", "error": "timeout"}
    status = await (await client.get("/api/mealie_hub/import")).json()
    assert status["running"] is None


async def test_import_normal_youtube_video_stays_with_mealie(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    await _run_import(hass, client, "https://www.youtube.com/watch?v=abc123")
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
    entry = await _run_import(hass, client, "https://example.com/soup")
    assert entry == {"url": "https://example.com/soup", "error": "boom"}


async def test_import_blocks_an_existing_recipe(hass, setup_entry, hass_client, aioclient_mock):
    aioclient_mock.get(
        f"{MEALIE}/api/recipes",
        json={"items": [{"slug": "soup", "orgURL": "https://example.com/soup"}]},
    )
    client = await hass_client()
    result = await _run_import(hass, client, "https://example.com/soup")
    assert result == {"duplicate_response": {"error": "duplicate", "slug": "soup"}}
    assert aioclient_mock.call_count == 1  # only the duplicate check — no import was attempted


async def test_import_survives_an_unexpected_error_in_cleanup(hass, setup_entry, hass_client, aioclient_mock):
    # A raw (non-UpstreamError) exception during the best-effort post-import cleanup
    # must never leave the queue's "running" marker stuck forever — that's exactly
    # what happened for real once, when only UpstreamError was caught around it.
    _no_existing_recipe(aioclient_mock)
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    aioclient_mock.patch(f"{MEALIE}/api/recipes/soup", json={"slug": "soup", "tags": []})
    aioclient_mock.get(f"{MEALIE}/api/organizers/tags", json={"items": []})
    aioclient_mock.get(f"{MEALIE}/api/recipes/soup", exc=TimeoutError("boom"))
    client = await hass_client()
    entry = await _run_import(hass, client, "https://example.com/soup")
    assert entry == {"url": "https://example.com/soup", "slug": "soup"}  # the import itself still succeeded
    status = await (await client.get("/api/mealie_hub/import")).json()
    assert status["running"] is None  # never stuck, even though cleanup blew up


async def test_import_force_skips_the_duplicate_check(hass, setup_entry, hass_client, aioclient_mock):
    # No GET /api/recipes mock is registered at all: if the duplicate check ran
    # despite force=true, aioclient_mock would raise for the unmocked call.
    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", json="soup")
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()
    entry = await _run_import(hass, client, "https://example.com/soup", force=True)
    assert entry == {"url": "https://example.com/soup", "slug": "soup"}


def _blocking_create_url_mock(aioclient_mock, release: asyncio.Event):
    """Registers a mocked create/url call that stays pending until `release` is set —
    lets a test observe a job as genuinely still "running", not just instantly finished."""

    async def _slow_create(method, url, data):
        await release.wait()
        return AiohttpClientMockResponse(method, url, json="soup")

    aioclient_mock.post(f"{MEALIE}/api/recipes/create/url", side_effect=_slow_create)


async def test_import_status_shows_running_and_queued_jobs(hass, setup_entry, hass_client, aioclient_mock):
    # Two imports submitted back to back: the first starts running immediately, the
    # second waits in the queue — this is exactly what survives a page reload now,
    # since both live in hass.data, not in the browser.
    _no_existing_recipe(aioclient_mock)
    release = asyncio.Event()
    _blocking_create_url_mock(aioclient_mock, release)
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()

    resp1 = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/first"})
    assert resp1.status == 202
    await asyncio.sleep(0)  # let the worker task actually start and reach the blocked call
    resp2 = await client.post("/api/mealie_hub/import", json={"url": "https://example.com/second"})
    assert resp2.status == 202

    status = await (await client.get("/api/mealie_hub/import")).json()
    assert status["running"]["url"] == "https://example.com/first"
    assert status["queue"] == [{"url": "https://example.com/second", "force": False}]

    release.set()  # let both jobs actually finish so no task is left dangling
    await hass.async_block_till_done()


async def test_import_cancel_removes_a_queued_job(hass, setup_entry, hass_client, aioclient_mock):
    _no_existing_recipe(aioclient_mock)
    release = asyncio.Event()
    _blocking_create_url_mock(aioclient_mock, release)
    _allow_tag_strip(aioclient_mock, "soup")
    client = await hass_client()

    await client.post("/api/mealie_hub/import", json={"url": "https://example.com/first"})
    await asyncio.sleep(0)
    await client.post("/api/mealie_hub/import", json={"url": "https://example.com/second"})

    resp = await client.post("/api/mealie_hub/import/cancel", json={"url": "https://example.com/second"})
    assert resp.status == 200
    assert (await resp.json()) == {"removed": True}
    status = await (await client.get("/api/mealie_hub/import")).json()
    assert status["queue"] == []

    resp_missing = await client.post("/api/mealie_hub/import/cancel", json={"url": "https://not-queued.example.com"})
    assert (await resp_missing.json()) == {"removed": False}

    release.set()
    await hass.async_block_till_done()


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
