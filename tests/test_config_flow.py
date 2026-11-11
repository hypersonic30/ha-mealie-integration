"""Config flow: validation, error mapping, reconfigure."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mealie_hub.const import DOMAIN

from .conftest import ENTRY_DATA, MEALIE


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_user_flow_success(hass, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes", json={"items": []})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA
    _, _, _, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"] == "Bearer mealie-key"


async def test_user_flow_bad_key(hass, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes", status=401)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_user_flow_unreachable(hass, aioclient_mock):
    aioclient_mock.get(f"{MEALIE}/api/recipes", exc=Exception("boom"))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
