import { FormEvent, useEffect, useState } from "react";
import { apiFetch, decodeRoleFromToken, getToken } from "../api/client";

type ForecastEntry = {
  time: string;
  temperature: number;
  precipitation_probability: number | null;
  weathercode: number;
};

type LocationResult = {
  name: string;
  admin1: string | null;
  country: string | null;
  latitude: number;
  longitude: number;
};

const WEATHER_ICONS: Record<number, string> = {
  0: "☀️",
  1: "🌤️",
  2: "⛅",
  3: "☁️",
  45: "🌫️",
  48: "🌫️",
  51: "🌦️",
  53: "🌦️",
  55: "🌦️",
  61: "🌧️",
  63: "🌧️",
  65: "🌧️",
  71: "🌨️",
  73: "🌨️",
  75: "🌨️",
  80: "🌦️",
  81: "🌧️",
  82: "⛈️",
  95: "⛈️",
  96: "⛈️",
  99: "⛈️"
};

function iconFor(code: number): string {
  return WEATHER_ICONS[code] ?? "🌡️";
}

const WEATHER_HOURS = [8, 10, 12, 15];

function isoDate(offsetDays = 0): string {
  const date = new Date();
  date.setDate(date.getDate() + offsetDays);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function entryAt(forecast: ForecastEntry[], dayIso: string, hour: number): ForecastEntry | null {
  const hourPrefix = `${dayIso}T${String(hour).padStart(2, "0")}:`;
  return forecast.find((entry) => entry.time.startsWith(hourPrefix)) ?? null;
}

function WeatherDayRow({ label, dayIso, forecast }: { label: string; dayIso: string; forecast: ForecastEntry[] }) {
  return (
    <div className="weather-day-row">
      <div className="weather-day-label">{label}</div>
      <div className="weather-slots">
        {WEATHER_HOURS.map((hour) => {
          const entry = entryAt(forecast, dayIso, hour);
          return (
            <div key={`${dayIso}-${hour}`} className="weather-slot">
              <div className="weather-slot-time">{String(hour).padStart(2, "0")}:00</div>
              {entry ? (
                <>
                  <div className="weather-slot-main">
                    <span aria-hidden="true">{iconFor(entry.weathercode)}</span>
                    <strong>{Math.round(entry.temperature)}°</strong>
                  </div>
                  <div className="weather-slot-rain">
                    <span aria-hidden="true">☔</span>
                    {entry.precipitation_probability ?? 0}%
                  </div>
                </>
              ) : (
                <div className="weather-slot-empty">keine Daten</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function WeatherCard() {
  const role = decodeRoleFromToken(getToken() ?? "");
  const isParent = role === "parent_admin" || role === "parent_member";

  const [forecast, setForecast] = useState<ForecastEntry[] | null>(null);
  const [needsLocation, setNeedsLocation] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LocationResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);

  function loadForecast() {
    setError(null);
    apiFetch<ForecastEntry[]>("/weather/forecast")
      .then((data) => {
        if (!Array.isArray(data)) {
          throw new Error("Unerwartete Antwort vom Server.");
        }
        setForecast(data);
        setNeedsLocation(false);
      })
      .catch((err) => {
        if (err instanceof Error && err.message.includes("Standort nicht konfiguriert")) {
          setNeedsLocation(true);
        } else {
          setError(err instanceof Error ? err.message : "Wetter konnte nicht geladen werden");
        }
      });
  }

  useEffect(loadForecast, []);

  async function handleSearch(event: FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2) return;
    setSearching(true);
    setError(null);
    try {
      const found = await apiFetch<LocationResult[]>(
        `/weather/search-location?query=${encodeURIComponent(query)}`
      );
      setResults(found);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Standort-Suche fehlgeschlagen");
    } finally {
      setSearching(false);
    }
  }

  async function handlePick(result: LocationResult) {
    setSaving(true);
    setError(null);
    try {
      const label = [result.name, result.admin1].filter(Boolean).join(", ");
      await apiFetch("/families/me/location", {
        method: "PATCH",
        body: JSON.stringify({ latitude: result.latitude, longitude: result.longitude, location_name: label })
      });
      setResults([]);
      setQuery("");
      loadForecast();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Standort konnte nicht gespeichert werden");
    } finally {
      setSaving(false);
    }
  }

  if (needsLocation) {
    return (
      <div className="card weather-card">
        <div className="icon-chip sky" aria-hidden="true">
          🌤️
        </div>
        <div className="card-body">
          <div className="card-title">Wetter</div>
          {isParent ? (
            <>
              <div className="card-meta">Noch kein Standort eingerichtet.</div>
              <form onSubmit={handleSearch} style={{ marginTop: 10, display: "flex", gap: 8 }}>
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Ort suchen, z.B. Zürich"
                  style={{ flex: 1 }}
                />
                <button type="submit" disabled={searching}>
                  {searching ? "…" : "Suchen"}
                </button>
              </form>
              {results.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {results.map((r, i) => (
                    <button
                      key={i}
                      type="button"
                      disabled={saving}
                      onClick={() => handlePick(r)}
                      style={{ textAlign: "left", fontSize: 13.5 }}
                    >
                      {[r.name, r.admin1, r.country].filter(Boolean).join(", ")}
                    </button>
                  ))}
                </div>
              )}
              {error && (
                <div className="error-banner" style={{ marginTop: 8 }}>
                  {error}
                </div>
              )}
            </>
          ) : (
            <div className="card-meta">Noch kein Standort eingerichtet. Ein Elternteil kann das einrichten.</div>
          )}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="card weather-card">
        <div className="icon-chip sky" aria-hidden="true">
          🌤️
        </div>
        <div className="card-body">
          <div className="card-title">Wetter</div>
          <div className="card-meta">{error}</div>
        </div>
      </div>
    );
  }

  if (!forecast) {
    return (
      <div className="card weather-card">
        <div className="icon-chip sky" aria-hidden="true">
          🌤️
        </div>
        <div className="card-body">
          <div className="card-title">Wetter</div>
          <div className="card-meta">wird geladen…</div>
        </div>
      </div>
    );
  }

  if (forecast.length === 0) {
    return (
      <div className="card weather-card">
        <div className="icon-chip sky" aria-hidden="true">
          🌤️
        </div>
        <div className="card-body">
          <div className="card-title">Wetter</div>
          <div className="card-meta">Keine Wetterdaten verfügbar.</div>
        </div>
      </div>
    );
  }

  const todayIso = isoDate();
  const tomorrowIso = isoDate(1);
  const firstSlot = entryAt(forecast, todayIso, WEATHER_HOURS[0]) ?? forecast[0];

  return (
    <div className="card weather-card weather-card--forecast">
      <div className="weather-card-heading">
        <div className="icon-chip sky" aria-hidden="true">
          {iconFor(firstSlot.weathercode)}
        </div>
        <div className="card-title">Wetter</div>
      </div>
      <div className="card-body">
        <div className="weather-days">
          <WeatherDayRow label="Heute" dayIso={todayIso} forecast={forecast} />
          <WeatherDayRow label="Morgen" dayIso={tomorrowIso} forecast={forecast} />
        </div>
      </div>
    </div>
  );
}
