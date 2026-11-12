"""Pure unit tests for extract_social_slug (no HA fixtures needed) — see its
docstring for the live bug this closes: Social-to-Mealie's own response never
carries the created recipe's slug directly, so every single Social-to-Mealie
import used to silently skip all post-import work (tag strip, ingredient
re-parse, servings guess), with no exception and nothing logged."""
import pytest

from custom_components.mealie_hub.api import extract_social_slug


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"createdRecipe": {"url": "http://mealie.test/g/home/r/insta-soup"}}, "insta-soup"),
        ({"createdRecipe": {"url": "http://mealie.test/g/home/r/insta-soup/"}}, "insta-soup"),
        ({"createdRecipe": {"url": "http://mealie.test/g/home/r/insta-soup?x=1"}}, "insta-soup"),
        ({"createdRecipe": {}}, None),  # getRecipe's url was somehow missing
        ({"createdRecipe": {"url": ""}}, None),
        ({"createdRecipe": {"url": "http://mealie.test/"}}, None),  # no /r/ segment at all
        ({}, None),  # Mealie/Social-to-Mealie error path: no createdRecipe key at all
    ],
)
def test_extract_social_slug(result, expected):
    assert extract_social_slug(result) == expected
