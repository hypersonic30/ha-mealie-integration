"""Pure unit tests for the best-effort servings guess (no HA fixtures needed) — see
`guess_servings`'s docstring for why this exists: Social-to-Mealie's own recipe schema
has no field for servings at all, so this is a regex guess from the description text,
not a real extraction."""
import pytest

from custom_components.mealie_hub.api import guess_servings


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Rezept für 4 Personen, schnell gemacht", 4.0),
        ("4 Personen", 4.0),
        ("Ergibt 12 Portionen", 12.0),
        ("This serves 6 people easily", 6.0),
        ("Yields nothing useful here", None),
        ("8 servings of pure joy", 8.0),
        ("Für 2–3 Portionen: cremige Pasta", 3.0),  # a range picks the upper bound
        ("Kein Hinweis auf Mengen hier", None),
        ("", None),
        (None, None),
    ],
)
def test_guess_servings(text, expected):
    assert guess_servings(text) == expected
