"""Pure unit tests for _split_vague_quantity (no HA fixtures needed) — see its
docstring for why this exists: Mealie's ingredient parser bakes word-based
quantities ("etwas", "eine kleine Menge", ...) into the food name verbatim
instead of recognizing them, so we strip them ourselves before parsing and
re-attach them to the parsed ingredient's note afterwards."""
import pytest

from custom_components.mealie_hub.api import _split_vague_quantity


@pytest.mark.parametrize(
    "text,expected_clean,expected_vague",
    [
        ("eine kleine Menge Butter", "Butter", "eine kleine Menge"),
        ("etwas frischer Knoblauch ohne Keim/Strunk", "frischer Knoblauch ohne Keim/Strunk", "etwas"),
        ("ein wenig Salz", "Salz", "ein wenig"),
        ("eine große Menge Wasser", "Wasser", "eine große Menge"),
        ("eine Menge Mehl", "Mehl", "eine Menge"),
        ("500g Mehl", "500g Mehl", None),  # a real quantity is left untouched
        ("Pfeffer", "Pfeffer", None),  # no quantity at all
        ("Etwas Zimt", "Zimt", "etwas"),  # case-insensitive match
    ],
)
def test_split_vague_quantity(text, expected_clean, expected_vague):
    clean, vague = _split_vague_quantity(text)
    assert clean == expected_clean
    assert vague == expected_vague
