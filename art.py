"""
art.py — optional AI scene illustration. OFF by default: set ART_ENABLED=true
to turn it on. Art is only ever generated on demand (the /scene or !scene
command), never automatically, so it costs nothing unless you ask for it.

Image model names change over time; override with ART_MODEL if you get an
"unknown model" error. Image generation typically requires a paid Gemini tier.
"""

import os
from google import genai
from google.genai import types

ART_ENABLED = os.environ.get("ART_ENABLED", "false").lower() in ("1", "true", "yes")
ART_MODEL = os.environ.get("ART_MODEL", "imagen-3.0-generate-002")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY", "MISSING"))
    return _client


def is_enabled():
    return ART_ENABLED


def generate_scene(description):
    """Return PNG bytes for a scene illustration, or raise on failure."""
    prompt = (
        "Dark fantasy digital painting, moody and atmospheric, painterly style. "
        "Setting: Odrun Fell, a city built on a buried god-weapon — lacquered towers, "
        "ichor-slick markets, bone-lit tunnels. No text, no words in the image. "
        f"Scene: {description}"
    )
    resp = _get_client().models.generate_images(
        model=ART_MODEL,
        prompt=prompt,
        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
    )
    return resp.generated_images[0].image.image_bytes
