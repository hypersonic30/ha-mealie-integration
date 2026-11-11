"""Shared fixtures for the Mealie Hub integration tests."""
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mealie_hub.const import (
    CONF_MEALIE_API_KEY,
    CONF_MEALIE_URL,
    CONF_SOCIAL_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

MEALIE = "http://mealie.test"
SOCIAL = "http://social.test"

ENTRY_DATA = {
    CONF_MEALIE_URL: MEALIE,
    CONF_MEALIE_API_KEY: "mealie-key",
    CONF_SOCIAL_URL: SOCIAL,
    CONF_VERIFY_SSL: True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, entry_id="test-entry")


@pytest.fixture
async def setup_entry(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
