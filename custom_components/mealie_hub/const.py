"""Constants for the Mealie Hub integration."""

DOMAIN = "mealie_hub"

CONF_MEALIE_URL = "mealie_url"
CONF_MEALIE_API_KEY = "mealie_api_key"
CONF_SOCIAL_URL = "social_url"
CONF_VERIFY_SSL = "verify_ssl"

DEFAULT_VERIFY_SSL = True

REQUEST_TIMEOUT = 30.0
# The import pipeline (video download + transcription + AI extraction) can run for
# a long time on modest/local-LLM hardware; a real timeout observed in practice (10
# min) was too short and produced a false failure for a recipe that still imported
# fine — the create-recipe call needs generous headroom.
IMPORT_TIMEOUT = 1800.0

# Domains routed to Social-to-Mealie (video platforms Mealie's own scraper can't read).
SOCIAL_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "facebook.com",
    "fb.watch",
    "pinterest.com",
    "pin.it",
)
# youtube.com is only routed to Social-to-Mealie for /shorts/ links; a normal YouTube
# video or a recipe blog on a free youtube.com-adjacent domain stays with Mealie's own import.
YOUTUBE_SHORTS_HOST = "youtube.com"
YOUTUBE_SHORTS_PREFIX = "/shorts/"
