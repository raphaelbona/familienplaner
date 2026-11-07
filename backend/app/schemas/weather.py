from pydantic import BaseModel


class LocationSearchResult(BaseModel):
    name: str
    admin1: str | None = None
    country: str | None = None
    latitude: float
    longitude: float


class HourlyForecastEntry(BaseModel):
    time: str
    temperature: float
    precipitation_probability: int | None = None
    weathercode: int
