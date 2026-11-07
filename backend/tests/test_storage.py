"""Tests for the storage module."""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.storage import read_upload, save_upload


def test_save_upload_creates_file_with_random_name():
    """Test that save_upload creates a file with a server-generated random name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.core.storage.get_settings") as mock_settings:
            mock_settings.return_value.upload_dir = tmpdir

            family_id = uuid.uuid4()
            content = b"test file content"
            stored_path = save_upload(family_id, "original_name.jpg", content)

            # Verify the stored path format
            assert str(family_id) in stored_path
            assert "original_name.jpg" not in stored_path  # Should NOT use client filename directly
            assert ".jpg" in stored_path  # Should preserve extension

            # Verify the file was created
            full_path = Path(tmpdir) / stored_path
            assert full_path.exists()
            assert full_path.read_bytes() == content


def test_save_upload_generates_different_names_for_same_filename():
    """Test that multiple uploads of files with the same name get different generated names."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.core.storage.get_settings") as mock_settings:
            mock_settings.return_value.upload_dir = tmpdir

            family_id = uuid.uuid4()
            content1 = b"file 1"
            content2 = b"file 2"

            path1 = save_upload(family_id, "schedule.png", content1)
            path2 = save_upload(family_id, "schedule.png", content2)

            # Different paths should be generated
            assert path1 != path2

            # Both files should exist with their respective content
            assert (Path(tmpdir) / path1).read_bytes() == content1
            assert (Path(tmpdir) / path2).read_bytes() == content2


def test_read_upload_returns_file_content():
    """Test that read_upload returns the correct file content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.core.storage.get_settings") as mock_settings:
            mock_settings.return_value.upload_dir = tmpdir

            family_id = uuid.uuid4()
            content = b"test content for reading"

            stored_path = save_upload(family_id, "test.png", content)
            read_content = read_upload(stored_path)

            assert read_content == content


def test_read_upload_rejects_path_traversal():
    """Test that read_upload rejects paths with traversal attempts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.core.storage.get_settings") as mock_settings:
            mock_settings.return_value.upload_dir = tmpdir

            # Try to read a path with traversal segments
            malicious_path = f"{uuid.uuid4()}/../../../etc/passwd"

            with pytest.raises(ValueError, match="escapes upload directory"):
                read_upload(malicious_path)


def test_read_upload_creates_family_directory():
    """Test that save_upload creates the family directory if it doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("app.core.storage.get_settings") as mock_settings:
            mock_settings.return_value.upload_dir = tmpdir

            family_id = uuid.uuid4()
            family_dir = Path(tmpdir) / str(family_id)

            # Directory shouldn't exist yet
            assert not family_dir.exists()

            save_upload(family_id, "test.jpg", b"content")

            # Directory should now exist
            assert family_dir.exists()
