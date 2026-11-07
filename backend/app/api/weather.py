from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user, require_parent
from app.core.weather import get_hourly_forecast, search_locations
from app.models.user import User
from app.schemas.weather import HourlyForecastEntry, LocationSearchResult

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/search-location", response_model=list[LocationSearchResult])
def search_location(
    query: Annotated[str, Query(min_length=2)],
    current_user: Annotated[User, Depends(require_parent)],
) -> list[dict]:
    try:
        return search_locations(query)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@router.get("/forecast", response_model=list[HourlyForecastEntry])
def forecast(current_user: Annotated[User, Depends(get_current_user)]) -> list[dict]:
    family = current_user.family
    if family.latitude is None or family.longitude is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Standort nicht konfiguriert.")

    try:
        return get_hourly_forecast(family.latitude, family.longitude)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
