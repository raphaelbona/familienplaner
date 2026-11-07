"""File storage module for uploaded files (self-hosted deployments)."""

import uuid
from pathlib import Path

from fastapi import Request, UploadFile

from app.core.config import get_settings


def save_upload(family_id: uuid.UUID, filename: str, content: bytes) -> str:
    """Save an uploaded file with a server-side random filename.

    Args:
        family_id: The family UUID for organizing uploads.
        filename: The original client-supplied filename.
        content: The file content as bytes.

    Returns:
        The relative stored path (e.g., "{family_id}/{generated_name}") to persist in the DB.
        Never returns an absolute filesystem path.
    """
    settings = get_settings()

    # Generate a server-side random filename to prevent path traversal attacks
    # Use the original file extension but a random name
    file_suffix = Path(filename).suffix
    generated_name = f"{uuid.uuid4()}{file_suffix}"

    # Build the directory path
    family_dir = Path(settings.upload_dir) / str(family_id)
    family_dir.mkdir(parents=True, exist_ok=True)

    # Write the file
    file_path = family_dir / generated_name
    file_path.write_bytes(content)

    # Return the relative stored path (not absolute) for database persistence
    return f"{family_id}/{generated_name}"


def read_upload(stored_path: str) -> bytes:
    """Read an uploaded file from storage.

    Args:
        stored_path: The stored path from the database (e.g., "{family_id}/{generated_name}").

    Returns:
        The file content as bytes.

    Raises:
        ValueError: If the resolved path escapes the upload directory (defense against
                    corrupted/tampered stored_path values).
    """
    settings = get_settings()

    # Resolve both paths to absolute paths for containment check
    upload_root = Path(settings.upload_dir).resolve()
    requested_file = (upload_root / stored_path).resolve()

    # Verify the requested file is still within the upload directory
    # This prevents a corrupted stored_path like "../../../etc/passwd" from escaping
    if not requested_file.is_relative_to(upload_root):
        raise ValueError(f"Stored path escapes upload directory: {stored_path}")

    # Read and return the file content
    return requested_file.read_bytes()


def read_bounded_upload(file: UploadFile, request: Request, max_size: int) -> bytes:
    """Read an uploaded file with size bounds, preventing unbounded memory allocation.

    Args:
        file: The uploaded file from FastAPI.
        request: The HTTP request (used to check Content-Length header).
        max_size: Maximum size in bytes allowed for the file.

    Returns:
        The file content as bytes if within bounds.

    Raises:
        ValueError: If the file exceeds max_size (checked via Content-Length first,
                   then by bounded chunked reads).
    """
    # Check Content-Length header first; if present and exceeds max_size, reject immediately
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            content_length_int = int(content_length)
            if content_length_int > max_size:
                raise ValueError("Datei zu groß.")
        except ValueError as e:
            # If it's our size check error, re-raise it
            if "Datei zu groß" in str(e):
                raise
            # Otherwise it's an invalid content-length header; continue with bounded read

    # Read file in bounded chunks to prevent unbounded buffering
    content = bytearray()
    chunk_size = 256 * 1024  # 256 KB chunks
    while True:
        chunk = file.file.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_size:
            raise ValueError("Datei zu groß.")

    return bytes(content)
