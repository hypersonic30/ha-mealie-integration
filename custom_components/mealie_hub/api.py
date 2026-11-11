"""Small async clients for Mealie and Social-to-Mealie, shared by the views."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_MEALIE_API_KEY,
    CONF_MEALIE_URL,
    CONF_SOCIAL_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    IMPORT_TIMEOUT,
    REQUEST_TIMEOUT,
    SOCIAL_DOMAINS,
    YOUTUBE_SHORTS_HOST,
    YOUTUBE_SHORTS_PREFIX,
)


class UpstreamError(Exception):
    """Raised when Mealie or Social-to-Mealie answers with an error status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class NoShoppingListError(Exception):
    """Raised when the household has no shopping list yet (create one in Mealie first)."""


def get_config(hass: HomeAssistant) -> dict:
    """The live config entry data — updated on reconfigure without a restart."""
    return hass.data.get(DOMAIN, {}).get("config", {})


def needs_social(url: str) -> bool:
    """True for links Mealie's own scraper cannot read (video platforms)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = parts.hostname or ""
    host = host.removeprefix("www.").removeprefix("m.")
    if host == YOUTUBE_SHORTS_HOST:
        return parts.path.startswith(YOUTUBE_SHORTS_PREFIX)
    return any(host == d or host.endswith(f".{d}") for d in SOCIAL_DOMAINS)


class MealieClient:
    """Talks to a self-hosted Mealie instance."""

    def __init__(self, hass: HomeAssistant, base_url: str, api_key: str, verify_ssl: bool) -> None:
        self._hass = hass
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._verify_ssl = verify_ssl

    @property
    def _session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass, verify_ssl=self._verify_ssl)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Any = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Any:
        async with self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers,
            json=json,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise UpstreamError(resp.status, text)
            if resp.status == 204:
                return None
            return await resp.json()

    async def list_recipes(
        self, *, page: int = 1, per_page: int = 50, search: str = "", tags: list[str] | None = None
    ) -> Any:
        params: list[tuple[str, str]] = [("page", str(page)), ("perPage", str(per_page))]
        if search:
            params.append(("search", search))
        for tag in tags or []:
            params.append(("tags", tag))
        return await self._request("GET", "/api/recipes", params=params)

    async def get_recipe(self, slug: str) -> Any:
        return await self._request("GET", f"/api/recipes/{slug}")

    async def delete_recipe(self, slug: str) -> Any:
        return await self._request("DELETE", f"/api/recipes/{slug}")

    async def list_tags(self) -> Any:
        return await self._request("GET", "/api/organizers/tags", params={"perPage": 200})

    async def delete_tag(self, tag_id: str) -> None:
        await self._request("DELETE", f"/api/organizers/tags/{tag_id}")

    async def prune_empty_tags(self) -> None:
        """Deletes every tag with no recipes left — Mealie doesn't garbage-collect these itself."""
        data = await self.list_tags()
        for tag in data.get("items", []):
            if tag.get("recipeCount", 0) == 0:
                try:
                    await self.delete_tag(tag["id"])
                except UpstreamError:
                    pass  # best-effort cleanup; shouldn't fail the caller's actual action

    async def set_recipe_tags(self, slug: str, tag_names: list[str]) -> Any:
        """Replaces a recipe's whole tag list (Mealie has no add/remove-one endpoint)."""
        result = await self._request("PATCH", f"/api/recipes/{slug}", json={"tags": [{"name": t} for t in tag_names]})
        await self.prune_empty_tags()
        return result

    async def find_recipe_by_url(self, url: str) -> dict | None:
        """Scans the recipe list for one already imported from this exact URL — Mealie has
        no server-side filter for this field, but it IS on the list response (`orgURL`),
        so one call covers it without needing each recipe's full detail."""
        data = await self.list_recipes(per_page=500)
        for item in data.get("items", []):
            if item.get("orgURL") == url:
                return item
        return None

    async def shopping_lists(self) -> Any:
        return await self._request("GET", "/api/households/shopping/lists")

    async def shopping_list(self, list_id: str) -> Any:
        return await self._request("GET", f"/api/households/shopping/lists/{list_id}")

    async def add_recipe_to_shopping_list(self, list_id: str, recipe_id: str) -> Any:
        # Mealie's wire format is camelCase per its source (a camelize alias
        # generator), but that is not confirmed live — both casings are sent
        # so an unverified guess here doesn't silently drop the field.
        return await self._request(
            "POST",
            f"/api/households/shopping/lists/{list_id}/recipe",
            json=[{"recipeId": recipe_id, "recipe_id": recipe_id}],
        )

    async def set_shopping_item(self, item: dict) -> Any:
        """`item` is a full item dict from `shopping_list()`, with `checked` already updated — Mealie's item PUT is unconfirmed as partial-update, so the whole known object is sent back."""
        return await self._request("PUT", f"/api/households/shopping/items/{item['id']}", json=item)

    async def delete_shopping_item(self, item_id: str) -> None:
        await self._request("DELETE", f"/api/households/shopping/items/{item_id}")

    async def clear_checked_shopping_items(self, list_id: str) -> int:
        """Deletes every checked item on the list, one by one (no confirmed bulk-delete query format). Returns how many were removed."""
        data = await self.shopping_list(list_id)
        items = data.get("listItems", data.get("list_items", []))
        checked = [i for i in items if i.get("checked")]
        for item in checked:
            await self.delete_shopping_item(item["id"])
        return len(checked)

    def image_url(self, recipe_id: str) -> str:
        """Mealie serves the original recipe image at this path (not yet confirmed against every Mealie version)."""
        return f"{self.base_url}/api/media/recipes/{recipe_id}/images/original.webp"

    def open_stream(self, url: str):
        """A GET whose response the caller streams out itself (recipe images)."""
        return self._session.get(url, headers=self._headers)

    async def import_from_url(self, url: str, *, include_tags: bool = False) -> Any:
        """Mealie's own scraper + AI-provider fallback (normal recipe web pages). Tags stay
        off by default — the user wants tagging to be fully manual, not auto-assigned from
        whatever categories/keywords the source page happens to carry."""
        return await self._request(
            "POST",
            "/api/recipes/create/url",
            json={"url": url, "includeTags": include_tags},
            timeout=IMPORT_TIMEOUT,
        )


class SocialClient:
    """Talks to a self-hosted Social-to-Mealie instance (Instagram/TikTok/… recipe videos)."""

    def __init__(self, hass: HomeAssistant, base_url: str, verify_ssl: bool) -> None:
        self._hass = hass
        self.base_url = base_url.rstrip("/")
        self._verify_ssl = verify_ssl

    @property
    def _session(self) -> aiohttp.ClientSession:
        return async_get_clientsession(self._hass, verify_ssl=self._verify_ssl)

    async def import_from_url(self, url: str, *, tags: list[str] | None = None) -> Any:
        async with self._session.post(
            f"{self.base_url}/api/get-url",
            json={"url": url, "tags": tags or []},
            timeout=aiohttp.ClientTimeout(total=IMPORT_TIMEOUT),
        ) as resp:
            body = await resp.json()
            if resp.status >= 400:
                raise UpstreamError(resp.status, body.get("error", str(body)))
            return body


async def default_shopping_list_id(client: MealieClient) -> str:
    """The household's first shopping list — picking a specific one isn't exposed yet."""
    data = await client.shopping_lists()
    items = data.get("items", [])
    if not items:
        raise NoShoppingListError
    return items[0]["id"]


def mealie_client(hass: HomeAssistant) -> MealieClient:
    cfg = get_config(hass)
    return MealieClient(hass, cfg[CONF_MEALIE_URL], cfg[CONF_MEALIE_API_KEY], cfg.get(CONF_VERIFY_SSL, True))


def social_client(hass: HomeAssistant) -> SocialClient | None:
    cfg = get_config(hass)
    url = cfg.get(CONF_SOCIAL_URL)
    if not url:
        return None
    return SocialClient(hass, url, cfg.get(CONF_VERIFY_SSL, True))
