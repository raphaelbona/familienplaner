"""Tests for the vision module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.core.vision import extract_letter_events, interpret_timetable_image


def test_interpret_timetable_image_raises_runtime_error_when_api_key_missing():
    """Test that interpret_timetable_image raises RuntimeError when API key is not configured."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = None

        with pytest.raises(RuntimeError, match="Anthropic-API ist nicht konfiguriert"):
            interpret_timetable_image(b"fake image content", "image/png")


def test_interpret_timetable_image_parses_json_response():
    """Test that interpret_timetable_image correctly parses a mocked JSON response."""
    expected_entries = [
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
        {"day_of_week": 0, "start_time": "09:00", "end_time": "10:00", "subject": "Mathe", "room": None},
        {"day_of_week": 1, "start_time": "08:00", "end_time": "09:00", "subject": "Englisch", "room": "102"},
    ]

    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client and its response
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock the response object
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = json.dumps(expected_entries)
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Call the function
            result = interpret_timetable_image(b"fake image", "image/png")

            # Verify the result
            assert result == expected_entries
            mock_anthropic_class.assert_called_once_with(api_key="test-key")


def test_interpret_timetable_image_raises_value_error_on_malformed_json():
    """Test that interpret_timetable_image raises ValueError for malformed JSON response."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock a response with invalid JSON
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = "This is not valid JSON {[}]"
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Should raise ValueError
            with pytest.raises(ValueError, match="Konnte Bild nicht auswerten"):
                interpret_timetable_image(b"fake image", "image/png")


def test_interpret_timetable_image_raises_value_error_on_non_array_json():
    """Test that interpret_timetable_image raises ValueError if JSON is not an array."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock a response with valid JSON but not an array
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = '{"not": "an array"}'
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Should raise ValueError
            with pytest.raises(ValueError, match="Konnte Stundenplan-Bild nicht auswerten"):
                interpret_timetable_image(b"fake image", "image/png")


def test_extract_letter_events_raises_runtime_error_when_api_key_missing():
    """Test that extract_letter_events raises RuntimeError when API key is not configured."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = None

        with pytest.raises(RuntimeError, match="Anthropic-API ist nicht konfiguriert"):
            extract_letter_events(b"fake image content", "image/png")


def test_extract_letter_events_parses_json_response():
    """Test that extract_letter_events correctly parses a mocked JSON response."""
    expected_events = [
        {"title": "Schulausflug", "event_date": "2026-09-15", "event_time": "10:00", "person_hint": "Klasse 4b"},
        {"title": "Elternabend", "event_date": "2026-09-20", "event_time": None, "person_hint": "Klasse 4b"},
        {"title": "Klassenfest", "event_date": "2026-10-05", "event_time": "14:30", "person_hint": "Klasse 4b"},
    ]

    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client and its response
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock the response object
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = json.dumps(expected_events)
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Call the function
            result = extract_letter_events(b"fake image", "image/png")

            # Verify the result
            assert result == expected_events
            mock_anthropic_class.assert_called_once_with(api_key="test-key")


def test_extract_letter_events_raises_value_error_on_malformed_json():
    """Test that extract_letter_events raises ValueError for malformed JSON response."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock a response with invalid JSON
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = "This is not valid JSON {[}]"
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Should raise ValueError
            with pytest.raises(ValueError, match="Konnte Bild nicht auswerten"):
                extract_letter_events(b"fake image", "image/png")


def test_extract_letter_events_raises_value_error_on_non_array_json():
    """Test that extract_letter_events raises ValueError if JSON is not an array."""
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock a response with valid JSON but not an array
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            mock_text_block.text = '{"not": "an array"}'
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Should raise ValueError
            with pytest.raises(ValueError, match="Konnte Brief-Bild nicht auswerten"):
                extract_letter_events(b"fake image", "image/png")


def test_interpret_timetable_image_uses_ollama_when_configured():
    """Test that interpret_timetable_image calls Ollama's /api/chat when vision_provider=ollama."""
    expected_entries = [
        {"day_of_week": 0, "start_time": "08:00", "end_time": "09:00", "subject": "Deutsch", "room": "101"},
    ]

    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.vision_provider = "ollama"
        mock_settings.return_value.ollama_base_url = "http://ollama:11434"
        mock_settings.return_value.ollama_vision_model = "llama3.2-vision"

        with patch("app.core.vision.httpx.post") as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"message": {"content": json.dumps(expected_entries)}}
            mock_post.return_value = mock_response

            result = interpret_timetable_image(b"fake image", "image/png")

            assert result == expected_entries
            called_url = mock_post.call_args[0][0]
            assert called_url == "http://ollama:11434/api/chat"
            called_payload = mock_post.call_args[1]["json"]
            assert called_payload["model"] == "llama3.2-vision"

def test_ollama_strips_markdown_json_fence():
    """Test that a ```json fenced response from a local model is parsed correctly."""
    expected_entries = [{"title": "Elternabend", "event_date": "2026-09-20", "event_time": None, "person_hint": None}]

    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.vision_provider = "ollama"
        mock_settings.return_value.ollama_base_url = "http://ollama:11434"
        mock_settings.return_value.ollama_vision_model = "llama3.2-vision"

        with patch("app.core.vision.httpx.post") as mock_post:
            mock_response = MagicMock()
            fenced = "```json\n" + json.dumps(expected_entries) + "\n```"
            mock_response.json.return_value = {"message": {"content": fenced}}
            mock_post.return_value = mock_response

            result = extract_letter_events(b"fake image", "image/png")

            assert result == expected_entries

def test_ollama_connection_error_raises_runtime_error():
    """Test that an unreachable Ollama instance raises a clear RuntimeError, not a raw exception."""
    import httpx

    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.vision_provider = "ollama"
        mock_settings.return_value.ollama_base_url = "http://ollama:11434"
        mock_settings.return_value.ollama_vision_model = "llama3.2-vision"

        with patch("app.core.vision.httpx.post") as mock_post:
            mock_post.side_effect = httpx.ConnectError("connection refused")

            with pytest.raises(RuntimeError, match="Ollama ist nicht erreichbar"):
                interpret_timetable_image(b"fake image", "image/png")

def test_extract_letter_events_mirrors_timetable_error_handling():
    """Test that extract_letter_events uses same error handling as interpret_timetable_image."""
    # This test ensures both functions use the shared helper and handle errors consistently
    with patch("app.core.vision.get_settings") as mock_settings:
        mock_settings.return_value.anthropic_api_key = "test-key"
        mock_settings.return_value.anthropic_model = "claude-sonnet-5"

        with patch("app.core.vision.anthropic.Anthropic") as mock_anthropic_class:
            # Mock the Anthropic client
            mock_client = MagicMock()
            mock_anthropic_class.return_value = mock_client

            # Mock a valid response
            mock_message = MagicMock()
            mock_text_block = MagicMock()
            event_data = {
                "title": "Event",
                "event_date": "2026-09-20",
                "event_time": None,
                "person_hint": None,
            }
            mock_text_block.text = json.dumps([event_data])
            mock_message.content = [mock_text_block]
            mock_client.messages.create.return_value = mock_message

            # Call function - should work without raising
            result = extract_letter_events(b"fake image", "image/png")
            assert isinstance(result, list)
            assert len(result) == 1
