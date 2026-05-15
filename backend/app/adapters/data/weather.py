"""Open-Meteo weather adapter — free, no API key, no rate limit worries.

Used to enrich upcoming game schedules with kickoff-time forecasts. Open-Meteo
returns up to 16-day forecasts, which covers the standard NFL schedule
release window.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential


# Approximate stadium coordinates by canonical team id. Domed stadiums get
# `is_indoor=True` and we return that instead of forecast data.
STADIUMS: dict[str, dict[str, Any]] = {
    "ARI": {"lat": 33.5275, "lon": -112.2625, "is_indoor": True},   # State Farm (retractable, closed often)
    "ATL": {"lat": 33.7553, "lon": -84.4006, "is_indoor": True},    # Mercedes-Benz (retractable)
    "BAL": {"lat": 39.2780, "lon": -76.6227, "is_indoor": False},
    "BUF": {"lat": 42.7738, "lon": -78.7868, "is_indoor": False},
    "CAR": {"lat": 35.2258, "lon": -80.8528, "is_indoor": False},
    "CHI": {"lat": 41.8623, "lon": -87.6167, "is_indoor": False},
    "CIN": {"lat": 39.0954, "lon": -84.5161, "is_indoor": False},
    "CLE": {"lat": 41.5061, "lon": -81.6995, "is_indoor": False},
    "DAL": {"lat": 32.7473, "lon": -97.0945, "is_indoor": True},    # AT&T (retractable)
    "DEN": {"lat": 39.7439, "lon": -105.0201, "is_indoor": False},
    "DET": {"lat": 42.3400, "lon": -83.0456, "is_indoor": True},    # Ford Field
    "GB":  {"lat": 44.5013, "lon": -88.0622, "is_indoor": False},
    "HOU": {"lat": 29.6847, "lon": -95.4107, "is_indoor": True},    # NRG (retractable)
    "IND": {"lat": 39.7601, "lon": -86.1639, "is_indoor": True},    # Lucas Oil
    "JAX": {"lat": 30.3239, "lon": -81.6373, "is_indoor": False},
    "KC":  {"lat": 39.0489, "lon": -94.4839, "is_indoor": False},
    "LAC": {"lat": 33.9535, "lon": -118.3392, "is_indoor": True},   # SoFi (fixed translucent roof)
    "LAR": {"lat": 33.9535, "lon": -118.3392, "is_indoor": True},
    "LV":  {"lat": 36.0908, "lon": -115.1833, "is_indoor": True},   # Allegiant
    "MIA": {"lat": 25.9580, "lon": -80.2389, "is_indoor": False},
    "MIN": {"lat": 44.9738, "lon": -93.2581, "is_indoor": True},    # US Bank
    "NE":  {"lat": 42.0909, "lon": -71.2643, "is_indoor": False},
    "NO":  {"lat": 29.9509, "lon": -90.0815, "is_indoor": True},    # Superdome
    "NYG": {"lat": 40.8136, "lon": -74.0744, "is_indoor": False},   # MetLife
    "NYJ": {"lat": 40.8136, "lon": -74.0744, "is_indoor": False},
    "PHI": {"lat": 39.9008, "lon": -75.1675, "is_indoor": False},
    "PIT": {"lat": 40.4468, "lon": -80.0158, "is_indoor": False},
    "SEA": {"lat": 47.5952, "lon": -122.3316, "is_indoor": False},
    "SF":  {"lat": 37.4030, "lon": -121.9700, "is_indoor": False},
    "TB":  {"lat": 27.9759, "lon": -82.5033, "is_indoor": False},
    "TEN": {"lat": 36.1665, "lon": -86.7713, "is_indoor": False},
    "WAS": {"lat": 38.9077, "lon": -76.8645, "is_indoor": False},
}


class OpenMeteoAdapter:
    BASE = "https://api.open-meteo.com/v1/forecast"

    def __init__(self) -> None:
        self.client = httpx.AsyncClient(timeout=10.0)

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4))
    async def _get(self, params: dict) -> dict | None:
        try:
            r = await self.client.get(self.BASE, params=params)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    async def forecast_for_game(
        self, home_team_id: str, kickoff: datetime | None,
    ) -> dict[str, Any]:
        """Return temp/precip/wind for the home stadium at kickoff (or near it).

        For indoor stadiums short-circuits to a constant. For outdoor games
        requests the hourly forecast and returns the slice closest to kickoff.
        """
        stadium = STADIUMS.get(home_team_id.upper())
        if not stadium:
            return {"available": False}
        if stadium["is_indoor"]:
            return {"available": True, "is_indoor": True, "temperature_f": 70, "summary": "Indoor"}

        if kickoff is None:
            return {"available": False}

        # Open-Meteo handles hourly forecasts up to 16d out by default
        params = {
            "latitude": stadium["lat"],
            "longitude": stadium["lon"],
            "hourly": "temperature_2m,precipitation,wind_speed_10m,weather_code",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
            "start_date": kickoff.strftime("%Y-%m-%d"),
            "end_date": kickoff.strftime("%Y-%m-%d"),
        }
        data = await self._get(params)
        if not data or "hourly" not in data:
            return {"available": False}
        times = data["hourly"].get("time", [])
        temps = data["hourly"].get("temperature_2m", [])
        precips = data["hourly"].get("precipitation", [])
        winds = data["hourly"].get("wind_speed_10m", [])
        codes = data["hourly"].get("weather_code", [])
        # Pick the hour closest to kickoff
        target_hour = kickoff.hour
        if not times:
            return {"available": False}
        best_i = min(range(len(times)), key=lambda i: abs(int(times[i][11:13]) - target_hour))
        return {
            "available": True,
            "is_indoor": False,
            "temperature_f": _safe(temps, best_i),
            "precipitation_in": _safe(precips, best_i),
            "wind_mph": _safe(winds, best_i),
            "weather_code": _safe(codes, best_i),
            "summary": _summary_from_code(_safe(codes, best_i)),
        }

    async def aclose(self) -> None:
        await self.client.aclose()


def _safe(arr, i):
    try:
        return arr[i]
    except (IndexError, TypeError):
        return None


# WMO weather codes -> short descriptions
WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    77: "Snow grains",
    80: "Rain showers", 81: "Heavy showers", 82: "Violent showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm + hail", 99: "Severe thunderstorm",
}


def _summary_from_code(code) -> str:
    try:
        return WMO.get(int(code), "—")
    except (TypeError, ValueError):
        return "—"
