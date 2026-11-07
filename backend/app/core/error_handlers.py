"""Custom error handlers for FastAPI exceptions."""

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _serialize_errors(errors: list) -> list:
    """Convert error objects to JSON-serializable format.

    Handles Pydantic ValidationError objects and other exceptions
    that may not be directly JSON serializable.
    """
    serialized = []
    for error in errors:
        # Create a copy to avoid modifying the original
        error_dict = dict(error)

        # Convert exception objects to strings
        if "ctx" in error_dict and isinstance(error_dict["ctx"], dict):
            ctx = {}
            for key, value in error_dict["ctx"].items():
                if isinstance(value, Exception):
                    ctx[key] = str(value)
                else:
                    ctx[key] = value
            error_dict["ctx"] = ctx

        serialized.append(error_dict)

    return serialized


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle validation errors, redacting sensitive fields like PIN and password.

    FastAPI's default validation error response includes the submitted input value
    for each validation error. For sensitive fields (pin, password, admin_password),
    we replace the input value with [redacted] to prevent credential attempts from
    being logged in proxy logs, APM tools, or browser history.
    """
    # Get the default error structure from FastAPI
    errors = exc.errors()

    # Serialize errors to handle non-JSON-serializable objects
    errors = _serialize_errors(errors)

    # Process each error to redact sensitive fields
    for error in errors:
        loc = error.get("loc", ())
        # Check if any location component is a sensitive field name
        if isinstance(loc, tuple):
            for field_name in ("pin", "password", "admin_password"):
                if field_name in loc:
                    # Redact the input value
                    error["input"] = "[redacted]"
                    break

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": errors},
    )
