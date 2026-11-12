"""Background queue for recipe imports.

Runs inside Home Assistant itself (via `hass.async_create_task`), independent of any
browser tab — reloading or closing the page that started an import never interrupts
it and never loses what's still waiting its turn. The card only ever reads `status()`
and writes through `enqueue()`/`cancel_queued()`; nothing here is awaited by a view
for the actual import duration.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

from .api import UpstreamError, extract_social_slug, mealie_client, needs_social, social_client
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HISTORY_MAX = 20


def _state(hass: HomeAssistant) -> dict[str, Any]:
    domain_data = hass.data.setdefault(DOMAIN, {})
    return domain_data.setdefault(
        "import_state", {"queue": [], "running": None, "history": [], "worker_active": False}
    )


def status(hass: HomeAssistant) -> dict[str, Any]:
    state = _state(hass)
    return {"queue": list(state["queue"]), "running": state["running"], "history": list(state["history"])}


def enqueue(hass: HomeAssistant, url: str, force: bool) -> None:
    state = _state(hass)
    state["queue"].append({"url": url, "force": force})
    if not state["worker_active"]:
        state["worker_active"] = True
        hass.async_create_task(_worker(hass))


def cancel_queued(hass: HomeAssistant, url: str) -> bool:
    """Removes the first still-waiting (not yet started) job matching this URL."""
    state = _state(hass)
    for i, job in enumerate(state["queue"]):
        if job["url"] == url:
            del state["queue"][i]
            return True
    return False


async def _worker(hass: HomeAssistant) -> None:
    state = _state(hass)
    try:
        while state["queue"]:
            job = state["queue"].pop(0)
            state["running"] = {"url": job["url"], "started_at": time.time()}
            try:
                entry = await _run_one(hass, job["url"], job["force"])
            except Exception:  # noqa: BLE001 - a bug here must never leave "running" stuck forever
                _LOGGER.exception("Unexpected error importing %s", job["url"])
                entry = {"url": job["url"], "error": "unexpected_error"}
            state["running"] = None
            state["history"].insert(0, entry)
            del state["history"][HISTORY_MAX:]
    finally:
        state["running"] = None
        state["worker_active"] = False


async def _run_one(hass: HomeAssistant, url: str, force: bool) -> dict[str, Any]:
    mealie = mealie_client(hass)
    try:
        if needs_social(url):
            client = social_client(hass)
            if client is None:
                return {"url": url, "error": "social_not_configured"}
            result = await client.import_from_url(url)
            slug = extract_social_slug(result)
        else:
            result = await mealie.import_from_url(url)
            slug = result if isinstance(result, str) else result.get("slug")
    except UpstreamError as err:
        _LOGGER.warning("Import failed for %s: HTTP %s", url, err.status)
        return {"url": url, "error": err.message}
    except (TimeoutError, aiohttp.ClientError) as err:
        # The video pipeline (download + transcription + AI extraction) can outrun
        # even IMPORT_TIMEOUT on slow hardware — Social-to-Mealie keeps working and
        # may still create the recipe, we've just stopped waiting for the answer.
        _LOGGER.warning("Import timed out or failed for %s: %s", url, err)
        return {"url": url, "error": "timeout"}

    if slug:
        try:
            # Social-to-Mealie hardcodes includeTags=true on its own side, so tags can
            # still slip in there even though the Mealie-own-import path already asks
            # for none — strip them unconditionally, so tagging stays fully manual.
            await mealie.set_recipe_tags(slug, [])
        except Exception:  # noqa: BLE001 - the recipe still imported; this cleanup failing isn't worth losing that
            _LOGGER.warning("Stripping tags failed for %s", slug, exc_info=True)
        # Both already catch their own errors (broadly, on purpose) — a recipe that
        # imported fine should never come back as a failure just because this
        # best-effort cleanup didn't, and the worker must never get stuck over it.
        await mealie.improve_recipe_ingredients(slug)
        await mealie.guess_and_set_servings(slug)

    return {"url": url, "slug": slug}
