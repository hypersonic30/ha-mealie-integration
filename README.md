# Mealie Hub (Recipe Card) — Home Assistant integration

Server-side proxy for a self-hosted [Mealie](https://mealie.io) instance (and
optionally [Social-to-Mealie](https://github.com/GerardPolloRebozado/social-to-mealie)
for Instagram/TikTok recipe videos), built for the companion
[Mealie Hub card](https://github.com/hypersonic30/ha-mealie-card).

Your Mealie API key (and, if configured, your Social-to-Mealie URL) never
reach the browser — the integration holds them server-side and proxies every
request, which is also what makes this reliable in the Home Assistant
Companion app (no cross-origin cookies to lose, unlike embedding either app's
own web UI in an iframe).

## What it does

- `GET /api/mealie_hub/recipes` — paginated recipe list (with a
  Home-Assistant-signed image URL per recipe)
- `GET /api/mealie_hub/recipes/{slug}` — full recipe (ingredients, steps,
  image)
- `GET /api/mealie_hub/recipes/{id}/image` — streams the recipe image
- `POST /api/mealie_hub/import` — one endpoint for the card's "paste a link"
  field: Instagram/TikTok/Facebook/Pinterest links (and `youtube.com/shorts/`)
  go to Social-to-Mealie, everything else goes to Mealie's own
  scraper + AI-provider fallback

## Setup

1. In Mealie: profile → API tokens → create a token.
2. In Home Assistant: Settings → Devices & services → Add integration →
   **Mealie Hub**. Enter the Mealie URL, the API token, and (optional) your
   Social-to-Mealie URL.
3. Install the [Mealie Hub card](https://github.com/hypersonic30/ha-mealie-card)
   via HACS and add it to a dashboard.

## Requires

Home Assistant ≥ 2024.8.0. Mealie ≥ 1.9.0 with an AI provider configured if
you want recipes extracted from pages without `schema.org` markup.

## Status

v0.1.0 — initial scaffold. Not yet tested against a real Mealie/Social-to-Mealie
instance; the recipe image path (`/api/media/recipes/{id}/images/original.webp`)
in particular needs confirming against a live Mealie version.
