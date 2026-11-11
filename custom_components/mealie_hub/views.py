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

from .api import UpstreamError, mealie_client, needs_social, social_client

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
        try:
            data = await mealie_client(self._hass).list_recipes(page=page, search=search)
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
        if not url.startswith(("http://", "https://")):
            return web.json_response({"error": "invalid_url"}, status=400)

        try:
            if needs_social(url):
                client = social_client(self._hass)
                if client is None:
                    return web.json_response({"error": "social_not_configured"}, status=400)
                result = await client.import_from_url(url)
                slug = (result.get("createdRecipe") or {}).get("slug")
            else:
                result = await mealie_client(self._hass).import_from_url(url)
                slug = result if isinstance(result, str) else result.get("slug")
        except UpstreamError as err:
            _LOGGER.warning("Import failed for %s: HTTP %s", url, err.status)
            return web.json_response({"error": err.message}, status=err.status)

        return web.json_response({"slug": slug})
