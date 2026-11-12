"""Small async clients for Mealie and Social-to-Mealie, shared by the views."""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

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


# Social-to-Mealie's own recipe schema has no yield/servings field at all (confirmed
# from its source), so a caption saying e.g. "für 4 Personen" never reaches Mealie —
# there's nowhere in the structured output for the LLM to put it. This is a best-effort
# guess from the recipe's (already-imported) description text, not a real extraction.
_SERVINGS_PATTERNS = [
    re.compile(r"für\s+(\d+)\s*personen", re.IGNORECASE),
    re.compile(r"(\d+)\s*personen", re.IGNORECASE),
    re.compile(r"(\d+)\s*portionen", re.IGNORECASE),
    re.compile(r"ergibt\s+(\d+)", re.IGNORECASE),
    re.compile(r"serves?\s+(\d+)", re.IGNORECASE),
    re.compile(r"(\d+)\s*servings", re.IGNORECASE),
]


def guess_servings(text: str) -> float | None:
    for pattern in _SERVINGS_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return float(match.group(1))
    return None


_VAGUE_QUANTITY_PREFIXES = (
    "eine kleine Menge ",
    "eine große Menge ",
    "eine Menge ",
    "ein wenig ",
    "etwas ",
)


def _split_vague_quantity(text: str) -> tuple[str, str | None]:
    """Strips a leading word-based quantity ("etwas", "eine kleine Menge", ...) off an
    ingredient's raw text before it goes to Mealie's parser — confirmed live: the parser
    has no concept of these, so it bakes the whole phrase into the food name verbatim
    ("eine kleine Menge Butter" as one food, quantity 0, no unit) instead of recognizing
    a quantity at all. Dropping the phrase outright would lose real information though
    (it tells you not to use a lot of it, not just "some amount"), so the caller re-attaches
    it to the parsed ingredient's note instead — same signal, just off the food name."""
    lowered = text.lower()
    for prefix in _VAGUE_QUANTITY_PREFIXES:
        if lowered.startswith(prefix.lower()):
            return text[len(prefix):].lstrip(), prefix.strip()
    return text, None


def extract_social_slug(result: dict) -> str | None:
    """Social-to-Mealie's /api/get-url response never carries the created recipe's
    slug directly — confirmed from its source: `createdRecipe` is a hand-built
    {name, description, imageUrl, url} object, not the Mealie recipe itself. The
    slug only shows up as the last path segment of `url` (".../g/<group>/r/<slug>"),
    which meant every single Social-to-Mealie import silently skipped all of our
    post-import work (tag strip, ingredient re-parse, servings guess) — `slug` was
    always None, so `if slug:` never ran, with no exception and nothing logged."""
    created = result.get("createdRecipe") or {}
    path = urlsplit(created.get("url") or "").path
    segments = [s for s in path.split("/") if s]
    if len(segments) >= 2 and segments[-2] == "r":
        return segments[-1]
    return None


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

    async def parse_ingredients(self, ingredient_texts: list[str]) -> Any:
        return await self._request("POST", "/api/parser/ingredients", json={"parser": "nlp", "ingredients": ingredient_texts})

    async def improve_recipe_ingredients(self, slug: str) -> bool:
        """Mealie's create-from-scrape/JSON path never runs ingredient strings through its
        own parser — it just dumps each one whole into `note`, with quantity/unit/food left
        empty. Best-effort: re-parse every ingredient's text and PATCH the structured
        result back. Never raises — the recipe already imported successfully by the time
        this runs, and a bug or hiccup here must never look like the import itself failed
        (it did, for real, once: left the import queue's "running" marker stuck forever
        because only UpstreamError was caught here, not e.g. a parser response shaped
        differently than expected). A parsing failure just leaves the plain-text notes,
        which still display fine, only without separate quantity/unit.

        Returns whether the PATCH actually happened — so the caller can log it. The import
        queue used to have no way to tell "ran and improved nothing" apart from "silently
        never ran at all", which is exactly how the missing-slug bug (see `extract_social_slug`)
        went unnoticed for so long."""
        try:
            recipe = await self.get_recipe(slug)
            ingredients = recipe.get("recipeIngredient", [])
            texts = [i.get("note") or i.get("display") or "" for i in ingredients if isinstance(i, dict)]
            texts = [t for t in texts if t]
            if not texts:
                return False
            split = [_split_vague_quantity(t) for t in texts]
            stripped_texts = [clean for clean, _vague in split]
            vague_quantities = [vague for _clean, vague in split]
            parsed = await self.parse_ingredients(stripped_texts)
            if len(parsed) != len(vague_quantities):
                return False  # a count mismatch means something unexpected happened; safer to leave as-is
            new_ingredients = []
            for item, vague in zip(parsed, vague_quantities):
                ingredient = item.get("ingredient")
                if not ingredient:
                    return False  # same reasoning as above
                if vague:
                    existing_note = ingredient.get("note") or ""
                    ingredient["note"] = f"{vague}, {existing_note}" if existing_note else vague
                new_ingredients.append(ingredient)
            if len(new_ingredients) != len(ingredients):
                return False  # a count mismatch means something unexpected happened; safer to leave as-is
            await self._resolve_new_ingredient_refs(new_ingredients)
            await self._request("PATCH", f"/api/recipes/{slug}", json={"recipeIngredient": new_ingredients})
            return True
        except Exception:  # noqa: BLE001 - deliberately broad, see docstring
            _LOGGER.warning("Re-parsing ingredients failed for %s", slug, exc_info=True)
            return False

    async def _resolve_new_ingredient_refs(self, ingredients: list[dict]) -> None:
        """The parser returns a brand-new food/unit (id=None) for anything not already
        in Mealie's database — but Mealie's own recipe-PATCH endpoint can't create those
        on the fly (confirmed live: it raises a server-side ValueError/HTTP 500 for any
        food or unit without an id, silently discarding the whole re-parse under the
        broad except above). So we create them ourselves through their own endpoints
        first and patch in the resulting id, exactly as Mealie's own UI would."""
        food_cache: dict[str, str] = {}
        unit_cache: dict[str, str] = {}
        for ingredient in ingredients:
            food = ingredient.get("food")
            if food and not food.get("id"):
                food["id"] = await self._resolve_ingredient_ref("/api/foods", food["name"], food_cache)
            unit = ingredient.get("unit")
            if unit and not unit.get("id"):
                unit["id"] = await self._resolve_ingredient_ref("/api/units", unit["name"], unit_cache)

    async def _resolve_ingredient_ref(self, path: str, name: str, cache: dict[str, str]) -> str:
        """Creates a food/unit by name, or — if it already exists (409) — finds its id
        via a search (the 409 response itself doesn't carry the existing id)."""
        key = name.strip().lower()
        if key in cache:
            return cache[key]
        try:
            created = await self._request("POST", path, json={"name": name})
            ref_id = created["id"]
        except UpstreamError as err:
            if err.status != 409:
                raise
            data = await self._request("GET", path, params={"search": name, "perPage": 50})
            match = next(
                (i for i in data.get("items", []) if i.get("name", "").strip().lower() == key), None
            )
            if match is None:
                raise
            ref_id = match["id"]
        cache[key] = ref_id
        return ref_id

    async def guess_and_set_servings(self, slug: str) -> float | None:
        """See `guess_servings` — a best-effort regex guess from the recipe's description,
        since Social-to-Mealie's own schema has nowhere to put servings info at all.
        Broad except for the same reason as `improve_recipe_ingredients` above.

        Returns the value it set, or None if it set nothing — see that docstring for why
        this is worth telling the caller instead of just swallowing it."""
        try:
            recipe = await self.get_recipe(slug)
            if recipe.get("recipeServings"):
                return None  # already has a real value — never override it with a guess
            servings = guess_servings(recipe.get("description") or "")
            if servings is None:
                return None
            await self._request("PATCH", f"/api/recipes/{slug}", json={"recipeServings": servings})
            return servings
        except Exception:  # noqa: BLE001 - deliberately broad, see docstring
            _LOGGER.warning("Guessing servings failed for %s", slug, exc_info=True)
            return None

    async def list_tags(self) -> Any:
        return await self._request("GET", "/api/organizers/tags", params={"perPage": 200})

    async def delete_tag(self, tag_id: str) -> None:
        await self._request("DELETE", f"/api/organizers/tags/{tag_id}")

    async def prune_empty_tags(self) -> None:
        """Deletes every tag with no recipes left — Mealie doesn't garbage-collect these
        itself. Never raises: this is cleanup after a tag was already successfully set,
        in both the interactive (card) and best-effort (import) callers."""
        try:
            data = await self.list_tags()
            for tag in data.get("items", []):
                if tag.get("recipeCount", 0) == 0:
                    try:
                        await self.delete_tag(tag["id"])
                    except Exception:  # noqa: BLE001
                        pass  # best-effort cleanup; shouldn't fail the caller's actual action
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Pruning empty tags failed", exc_info=True)

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

    async def add_recipe_to_shopping_list(self, list_id: str, recipe_id: str, scale: float = 1.0) -> Any:
        # Mealie's wire format is camelCase per its source (a camelize alias
        # generator), but that is not confirmed live — both casings are sent
        # so an unverified guess here doesn't silently drop the field.
        # `recipeIncrementQuantity` confirmed live (2026-11-12): it's a plain
        # multiplier on the recipe's stored ingredient quantities, not a target
        # servings count, and repeated calls merge into the existing list item
        # by food rather than duplicating it.
        return await self._request(
            "POST",
            f"/api/households/shopping/lists/{list_id}/recipe",
            json=[{"recipeId": recipe_id, "recipe_id": recipe_id, "recipeIncrementQuantity": scale}],
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
            # Social-to-Mealie's non-streaming response always sends its JSON body
            # without a Content-Type header (so aiohttp sees text/plain), on both
            # success and error — resp.json() would reject it before we ever see
            # the status, so we read the raw text and parse it ourselves instead.
            text = await resp.text()
            try:
                body = json.loads(text) if text else {}
            except ValueError:
                raise UpstreamError(resp.status, text) from None
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
