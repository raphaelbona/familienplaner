"""Vision client for timetable/letter image interpretation.

Supports two providers, selected via `Settings.vision_provider`:
- "anthropic" (default): Claude's vision API.
- "ollama": a self-hosted, subscription-free vision model reachable at
  `Settings.ollama_base_url` (see docker-compose.yml's optional `ollama` service).
"""

import base64
import json
import re
from typing import Any

import anthropic
import httpx

from app.core.config import Settings, get_settings

_OLLAMA_TIMEOUT_SECONDS = 120.0
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_json_fence(text: str) -> str:
    """Strip a ```json ... ``` markdown fence some models wrap JSON responses in."""
    stripped = text.strip()
    match = _JSON_FENCE_RE.match(stripped)
    return match.group(1) if match else stripped


def _call_anthropic_vision(image_bytes: bytes, media_type: str, prompt: str, settings: Settings) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic-API ist nicht konfiguriert.")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    image_data = base64.b64encode(image_bytes).decode()

    try:
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        raise RuntimeError("Anthropic-API-Fehler beim Verarbeiten des Bildes.") from exc

    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _call_ollama_vision(image_bytes: bytes, media_type: str, prompt: str, settings: Settings) -> str:
    image_data = base64.b64encode(image_bytes).decode()

    try:
        response = httpx.post(
            f"{settings.ollama_base_url}/api/chat",
            json={
                "model": settings.ollama_vision_model,
                "messages": [{"role": "user", "content": prompt, "images": [image_data]}],
                "stream": False,
                "format": "json",
            },
            timeout=_OLLAMA_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Ollama ist nicht erreichbar oder hat einen Fehler zurückgegeben.") from exc

    try:
        return response.json()["message"]["content"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Unerwartete Antwort von Ollama erhalten.") from exc


def _call_vision_and_parse_json(image_bytes: bytes, media_type: str, prompt: str) -> Any:
    """Private helper: call the configured vision provider and parse its JSON response.

    Args:
        image_bytes: The raw image file content.
        media_type: The image media type (e.g., "image/png", "image/jpeg").
        prompt: The prompt to send to the model for analyzing the image.

    Returns:
        The parsed JSON object/array from the model's response.

    Raises:
        RuntimeError: If the configured provider is not reachable/configured, or the call fails.
        ValueError: If the model response cannot be parsed as JSON.
    """
    settings = get_settings()

    if settings.vision_provider == "ollama":
        response_text = _call_ollama_vision(image_bytes, media_type, prompt, settings)
    else:
        response_text = _call_anthropic_vision(image_bytes, media_type, prompt, settings)

    try:
        return json.loads(_strip_json_fence(response_text))
    except json.JSONDecodeError as exc:
        raise ValueError("Konnte Bild nicht auswerten.") from exc


def interpret_timetable_image(image_bytes: bytes, media_type: str) -> list[dict]:
    """Interpret a timetable image using the configured vision provider.

    Args:
        image_bytes: The raw image file content.
        media_type: The image media type (e.g., "image/png", "image/jpeg").

    Returns:
        A list of timetable entries, each a dict with:
        - day_of_week: int (0=Monday, 6=Sunday)
        - start_time: str ("HH:MM")
        - end_time: str ("HH:MM")
        - subject: str
        - room: str | None

    Raises:
        RuntimeError: If the vision provider is not configured/reachable.
        ValueError: If the model response cannot be parsed as JSON.
    """
    # Create a prompt for the model to extract timetable information
    prompt = (
        "Du erhältst ein Bild eines Stundenplans. "
        "Analysiere das Bild und extrahiere alle Einträge. "
        "Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Array, ohne weitere Erklärungen oder Prosa. "
        "Jeder Eintrag sollte folgende Struktur haben:\n"
        "{\n"
        "  \"day_of_week\": 0-6 (0=Montag, 6=Sonntag),\n"
        "  \"start_time\": \"HH:MM\",\n"
        "  \"end_time\": \"HH:MM\",\n"
        "  \"subject\": \"Fachname\",\n"
        "  \"room\": \"Raumnummer oder null\"\n"
        "}\n"
        "Antworte nur mit dem JSON-Array, keine anderen Zeichen."
    )

    entries = _call_vision_and_parse_json(image_bytes, media_type, prompt)
    if not isinstance(entries, list):
        raise ValueError("Konnte Stundenplan-Bild nicht auswerten.")
    return entries


def extract_letter_events(image_bytes: bytes, media_type: str) -> list[dict]:
    """Extract events from a school/parent letter image using the configured vision provider.

    Args:
        image_bytes: The raw image file content.
        media_type: The image media type (e.g., "image/png", "image/jpeg").

    Returns:
        A list of extracted events, each a dict with:
        - title: str
        - event_date: str ("YYYY-MM-DD")
        - event_time: str ("HH:MM") | None
        - person_hint: str | None (best guess at who event concerns)

    Raises:
        RuntimeError: If the vision provider is not configured/reachable.
        ValueError: If the model response cannot be parsed as JSON.
    """
    prompt = (
        "Du erhältst ein Bild eines Schulbriefs oder einer Elternbenachrichtigung. "
        "Analysiere das Bild und extrahiere alle Ereignisse/Termine. "
        "Antworte AUSSCHLIESSLICH mit einem gültigen JSON-Array, ohne weitere Erklärungen oder Prosa. "
        "Jeder Eintrag sollte folgende Struktur haben:\n"
        "{\n"
        "  \"title\": \"Ereignistitel\",\n"
        "  \"event_date\": \"YYYY-MM-DD\",\n"
        "  \"event_time\": \"HH:MM\" oder null,\n"
        "  \"person_hint\": \"Kindsname/Klasse/Familie oder null\"\n"
        "}\n"
        "Antworte nur mit dem JSON-Array, keine anderen Zeichen."
    )

    events = _call_vision_and_parse_json(image_bytes, media_type, prompt)
    if not isinstance(events, list):
        raise ValueError("Konnte Brief-Bild nicht auswerten.")
    return events
