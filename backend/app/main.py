import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    calendar,
    day_phases,
    devices,
    documents,
    earnings,
    families,
    food_preferences,
    meal_plan,
    points,
    push,
    recipes,
    school_breaks,
    shopping,
    tasks,
    timetables,
    weather,
)
from app.core.config import get_settings
from app.core.error_handlers import request_validation_error_handler

logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(RequestValidationError, request_validation_error_handler)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(families.router)
app.include_router(calendar.router)
app.include_router(documents.router)
app.include_router(food_preferences.router)
app.include_router(recipes.router)
app.include_router(meal_plan.router)
app.include_router(shopping.router)
app.include_router(tasks.router)
app.include_router(timetables.router)
app.include_router(school_breaks.router)
app.include_router(day_phases.router)
app.include_router(points.router)
app.include_router(earnings.router)
app.include_router(push.router)
app.include_router(weather.router)


@app.on_event("startup")
def check_secret_key() -> None:
    """Warn if secret_key is using a weak/default value."""
    known_weak_values = {"change-me-in-.env"}
    if settings.secret_key in known_weak_values or len(settings.secret_key) < 32:
        logger.warning(
            "WARNING: secret_key is weak or using a default value. "
            "This compromises the security of OAuth tokens and JWT signing. "
            "Set a strong secret_key in your .env file (min 32 characters)."
        )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "hosting_mode": settings.hosting_mode}
