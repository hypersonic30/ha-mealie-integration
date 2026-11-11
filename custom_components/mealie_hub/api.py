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

    async def _request(self, method: str, path: str, *, json: Any = None, timeout: float = REQUEST_TIMEOUT) -> Any:
        async with self._session.request(
            method,
            f"{self.base_url}{path}",
            headers=self._headers,
            json=json,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise UpstreamError(resp.status, text)
            if resp.status == 204:
                return None
            return await resp.json()

    async def list_recipes(self, *, page: int = 1, per_page: int = 50, search: str = "") -> Any:
        params = {"page": page, "perPage": per_page}
        if search:
            params["search"] = search
        async with self._session.get(
            f"{self.base_url}/api/recipes",
            headers=self._headers,
            params=params,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            if resp.status >= 400:
                raise UpstreamError(resp.status, await resp.text())
            return await resp.json()

    async def get_recipe(self, slug: str) -> Any:
        return await self._request("GET", f"/api/recipes/{slug}")

    def image_url(self, recipe_id: str) -> str:
        """Mealie serves the original recipe image at this path (not yet confirmed against every Mealie version)."""
        return f"{self.base_url}/api/media/recipes/{recipe_id}/images/original.webp"

    def open_stream(self, url: str):
        """A GET whose response the caller streams out itself (recipe images)."""
        return self._session.get(url, headers=self._headers)

    async def import_from_url(self, url: str, *, include_tags: bool = True) -> Any:
        """Mealie's own scraper + AI-provider fallback (normal recipe web pages)."""
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


def mealie_client(hass: HomeAssistant) -> MealieClient:
    cfg = get_config(hass)
    return MealieClient(hass, cfg[CONF_MEALIE_URL], cfg[CONF_MEALIE_API_KEY], cfg.get(CONF_VERIFY_SSL, True))


def social_client(hass: HomeAssistant) -> SocialClient | None:
    cfg = get_config(hass)
    url = cfg.get(CONF_SOCIAL_URL)
    if not url:
        return None
    return SocialClient(hass, url, cfg.get(CONF_VERIFY_SSL, True))
