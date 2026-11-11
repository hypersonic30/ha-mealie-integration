"""Mealie Hub — Home Assistant proxy for Mealie + Social-to-Mealie, used by the Mealie Hub card."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .views import (
    ImportCancelView,
    ImportView,
    RecipeDetailView,
    RecipeImageView,
    RecipesView,
    RecipeTagsView,
    RecipeToShoppingListView,
    ShoppingListClearCheckedView,
    ShoppingListItemView,
    ShoppingListView,
    TagsView,
)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_VIEWS = (
    RecipesView,
    RecipeDetailView,
    RecipeImageView,
    ImportView,
    ImportCancelView,
    TagsView,
    RecipeTagsView,
    ShoppingListView,
    ShoppingListItemView,
    ShoppingListClearCheckedView,
    RecipeToShoppingListView,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})["config"] = entry.data

    if not hass.data[DOMAIN].get("views_registered"):
        for view in _VIEWS:
            hass.http.register_view(view(hass))
        hass.data[DOMAIN]["views_registered"] = True

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The live config entry data reloads on save — no restart needed."""
    hass.data.setdefault(DOMAIN, {})["config"] = entry.data


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # A bug in the import worker could in principle leave "running" stuck forever
    # (hass.data persists across a config reload, unlike a full HA restart) —
    # dropping it here means reloading this integration in the UI is always a
    # working recovery path, not just a full restart of Home Assistant.
    hass.data.get(DOMAIN, {}).pop("import_state", None)
    return True
