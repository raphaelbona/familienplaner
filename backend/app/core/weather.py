"""Weather forecast via Open-Meteo (no API key, no subscription -- fits the
project's self-hosted/no-cloud-dependency philosophy).
"""

import httpx

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_REQUEST_TIMEOUT_SECONDS = 10.0
_FORECAST_WINDOW_HOURS = 48


def search_locations(query: str) -> list[dict]:
    """Look up candidate locations by name, for a parent to pick the family's home town."""
    try:
        response = httpx.get(
            _GEOCODING_URL,
            params={"name": query, "count": 5, "language": "de", "format": "json"},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Standort-Suche ist aktuell nicht erreichbar.") from exc

    results = response.json().get("results") or []
    return [
        {
            "name": item["name"],
            "admin1": item.get("admin1"),
            "country": item.get("country"),
            "latitude": item["latitude"],
            "longitude": item["longitude"],
        }
        for item in results
    ]


def get_hourly_forecast(latitude: float, longitude: float) -> list[dict]:
    """Returns hourly forecast entries for today and tomorrow."""
    try:
        response = httpx.get(
            _FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "hourly": "temperature_2m,precipitation_probability,weathercode",
                "current_weather": "true",
                "forecast_days": 2,
                "timezone": "auto",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Wetterdaten sind aktuell nicht erreichbar.") from exc

    data = response.json()
    hourly = data.get("hourly", {})
    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    precipitation = hourly.get("precipitation_probability")
    weathercodes = hourly.get("weathercode")

    if not times or not temperatures or not weathercodes:
        raise ValueError("Unerwartete Wetterdaten erhalten.")

    precipitation = precipitation or [None] * len(times)

    return [
        {"time": t, "temperature": temp, "precipitation_probability": pop, "weathercode": code}
        for t, temp, pop, code in zip(
            times[:_FORECAST_WINDOW_HOURS],
            temperatures[:_FORECAST_WINDOW_HOURS],
            precipitation[:_FORECAST_WINDOW_HOURS],
            weathercodes[:_FORECAST_WINDOW_HOURS],
            strict=False,
        )
    ]
