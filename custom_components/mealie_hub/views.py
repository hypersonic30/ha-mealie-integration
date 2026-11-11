"""HTTP views bridging the Mealie Hub card to Mealie and Social-to-Mealie.

Every view requires a Home Assistant login. The card never sees the Mealie API
key or talks to either upstream service directly — both are added here,
server-side, which is also what keeps this usable from the Companion app
(no cross-origin cookies to lose).
"""
from __future__ import annotations

import logging

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import NoShoppingListError, UpstreamError, default_shopping_list_id, mealie_client, needs_social, social_client

_LOGGER = logging.getLogger(__name__)


class RecipesView(HomeAssistantView):
    url = "/api/mealie_hub/recipes"
    name = "api:mealie_hub:recipes"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        page = int(request.query.get("page", "1"))
        search = request.query.get("search", "")
        tags_param = request.query.get("tags", "")
        tags = [t for t in tags_param.split(",") if t] or None
        try:
            data = await mealie_client(self._hass).list_recipes(page=page, search=search, tags=tags)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        for item in data.get("items", []):
            if item.get("id"):
                item["image_url"] = f"/api/mealie_hub/recipes/{item['id']}/image"
        return web.json_response(data)


class RecipeDetailView(HomeAssistantView):
    url = "/api/mealie_hub/recipes/{slug}"
    name = "api:mealie_hub:recipe_detail"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, slug: str) -> web.Response:
        try:
            data = await mealie_client(self._hass).get_recipe(slug)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        if data.get("id"):
            data["image_url"] = f"/api/mealie_hub/recipes/{data['id']}/image"
        return web.json_response(data)

    async def delete(self, request: web.Request, slug: str) -> web.Response:
        try:
            await mealie_client(self._hass).delete_recipe(slug)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response({"ok": True})


class RecipeImageView(HomeAssistantView):
    """Streams a recipe's image through Home Assistant so the card never needs direct network access to Mealie."""

    url = "/api/mealie_hub/recipes/{recipe_id}/image"
    name = "api:mealie_hub:recipe_image"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request, recipe_id: str) -> web.StreamResponse:
        client = mealie_client(self._hass)
        async with client.open_stream(client.image_url(recipe_id)) as upstream:
            if upstream.status >= 400:
                return web.Response(status=upstream.status)
            response = web.StreamResponse(status=upstream.status, headers={
                "Content-Type": upstream.headers.get("Content-Type", "image/webp"),
            })
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(65536):
                await response.write(chunk)
            await response.write_eof()
            return response


class ImportView(HomeAssistantView):
    """Takes a single link from the card and routes it to the right backend."""

    url = "/api/mealie_hub/import"
    name = "api:mealie_hub:import"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        body = await request.json()
        url = (body or {}).get("url", "").strip()
        force = bool((body or {}).get("force", False))
        if not url.startswith(("http://", "https://")):
            return web.json_response({"error": "invalid_url"}, status=400)

        mealie = mealie_client(self._hass)
        if not force:
            try:
                existing = await mealie.find_recipe_by_url(url)
            except UpstreamError:
                existing = None  # the duplicate check itself failing shouldn't block a real import
            if existing:
                return web.json_response({"error": "duplicate", "slug": existing.get("slug")}, status=409)

        try:
            if needs_social(url):
                client = social_client(self._hass)
                if client is None:
                    return web.json_response({"error": "social_not_configured"}, status=400)
                result = await client.import_from_url(url)
                slug = (result.get("createdRecipe") or {}).get("slug")
            else:
                result = await mealie.import_from_url(url)
                slug = result if isinstance(result, str) else result.get("slug")
        except UpstreamError as err:
            _LOGGER.warning("Import failed for %s: HTTP %s", url, err.status)
            return web.json_response({"error": err.message}, status=err.status)

        if slug:
            try:
                # Social-to-Mealie hardcodes includeTags=true on its own side, so tags can
                # still slip in there even though the Mealie-own-import path already asks
                # for none — strip them unconditionally, so tagging stays fully manual.
                await mealie.set_recipe_tags(slug, [])
            except UpstreamError:
                pass  # the recipe still imported; losing the tag-strip isn't worth failing for
            # Both already catch their own errors — a recipe that imported fine should
            # never come back as a failure just because this best-effort cleanup didn't.
            await mealie.improve_recipe_ingredients(slug)
            await mealie.guess_and_set_servings(slug)

        return web.json_response({"slug": slug})


class TagsView(HomeAssistantView):
    url = "/api/mealie_hub/tags"
    name = "api:mealie_hub:tags"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        try:
            data = await mealie_client(self._hass).list_tags()
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response(data)


class RecipeTagsView(HomeAssistantView):
    url = "/api/mealie_hub/recipes/{slug}/tags"
    name = "api:mealie_hub:recipe_tags"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def patch(self, request: web.Request, slug: str) -> web.Response:
        body = await request.json()
        tags = (body or {}).get("tags", [])
        try:
            result = await mealie_client(self._hass).set_recipe_tags(slug, tags)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response(result)


class ShoppingListView(HomeAssistantView):
    url = "/api/mealie_hub/shopping-list"
    name = "api:mealie_hub:shopping_list"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(self, request: web.Request) -> web.Response:
        client = mealie_client(self._hass)
        try:
            list_id = await default_shopping_list_id(client)
            data = await client.shopping_list(list_id)
        except NoShoppingListError:
            return web.json_response({"error": "no_shopping_list"}, status=404)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        # Mealie's wire casing for this field isn't confirmed live; accept either.
        items = data.get("listItems", data.get("list_items", []))
        return web.json_response({"id": data.get("id"), "name": data.get("name"), "items": items})


class ShoppingListItemView(HomeAssistantView):
    url = "/api/mealie_hub/shopping-list/items/{item_id}"
    name = "api:mealie_hub:shopping_list_item"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def put(self, request: web.Request, item_id: str) -> web.Response:
        body = await request.json()
        client = mealie_client(self._hass)
        try:
            list_id = await default_shopping_list_id(client)
            data = await client.shopping_list(list_id)
            items = data.get("listItems", data.get("list_items", []))
            item = next((i for i in items if i.get("id") == item_id), None)
            if item is None:
                return web.json_response({"error": "item_not_found"}, status=404)
            item["checked"] = bool((body or {}).get("checked", False))
            result = await client.set_shopping_item(item)
        except NoShoppingListError:
            return web.json_response({"error": "no_shopping_list"}, status=404)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response(result)

    async def delete(self, request: web.Request, item_id: str) -> web.Response:
        try:
            await mealie_client(self._hass).delete_shopping_item(item_id)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response({"ok": True})


class ShoppingListClearCheckedView(HomeAssistantView):
    url = "/api/mealie_hub/shopping-list/clear-checked"
    name = "api:mealie_hub:shopping_list_clear_checked"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        client = mealie_client(self._hass)
        try:
            list_id = await default_shopping_list_id(client)
            removed = await client.clear_checked_shopping_items(list_id)
        except NoShoppingListError:
            return web.json_response({"error": "no_shopping_list"}, status=404)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response({"removed": removed})


class RecipeToShoppingListView(HomeAssistantView):
    url = "/api/mealie_hub/recipes/{recipe_id}/shopping-list"
    name = "api:mealie_hub:recipe_to_shopping_list"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request, recipe_id: str) -> web.Response:
        client = mealie_client(self._hass)
        try:
            list_id = await default_shopping_list_id(client)
            result = await client.add_recipe_to_shopping_list(list_id, recipe_id)
        except NoShoppingListError:
            return web.json_response({"error": "no_shopping_list"}, status=404)
        except UpstreamError as err:
            return web.json_response({"error": err.message}, status=err.status)
        return web.json_response(result)
