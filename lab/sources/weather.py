"""Open-Meteo (free, no key): geocode a venue city, then read the hourly forecast at game time."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from .http import SourceError, get_json

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


class Weather:
    def __init__(self, db=None, transport=get_json):
        self.db, self.t = db, transport
        self._forecasts: dict[tuple, dict] = {}

    def coords(self, city: str | None, region: str | None = None, country: str | None = None) -> tuple[float, float] | None:
        if not city:
            return None
        key = f"geo:{city}|{region}|{country}"
        if self.db:
            hit = self.db.get(key)
            if hit is not None:
                return tuple(hit) if hit else None
        found = None
        try:
            data, _ = self.t("https://geocoding-api.open-meteo.com/v1/search?" + urlencode({"name": city, "count": 10, "language": "en"}))
            results = data.get("results") or []
            want_state = US_STATES.get((region or "").upper(), region)
            for r in results:
                if want_state and r.get("admin1") == want_state:
                    found = (r["latitude"], r["longitude"])
                    break
            if not found and country:
                for r in results:
                    if country.lower() in (r.get("country") or "").lower() or (country in ("England", "Scotland", "Wales") and r.get("country_code") == "GB"):
                        found = (r["latitude"], r["longitude"])
                        break
            if not found and results and not region and not country:
                found = (results[0]["latitude"], results[0]["longitude"])
        except SourceError:
            return None
        if self.db:
            self.db.put(key, list(found) if found else [])
        return found

    def at(self, lat: float, lon: float, when: datetime) -> dict | None:
        key = (round(lat, 2), round(lon, 2))
        if key not in self._forecasts:
            params = {
                "latitude": key[0], "longitude": key[1],
                "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m",
                "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "UTC", "forecast_days": 16,
            }
            try:
                self._forecasts[key], _ = self.t("https://api.open-meteo.com/v1/forecast?" + urlencode(params))
            except SourceError:
                self._forecasts[key] = {}
        h = self._forecasts[key].get("hourly") or {}
        times = h.get("time") or []
        if not times:
            return None
        target = when.astimezone(timezone.utc)
        best = min(range(len(times)), key=lambda i: abs(datetime.fromisoformat(times[i]).replace(tzinfo=timezone.utc) - target))
        if abs(datetime.fromisoformat(times[best]).replace(tzinfo=timezone.utc) - target).total_seconds() > 5400:
            return None

        def val(name):
            arr = h.get(name) or []
            return arr[best] if best < len(arr) else None

        return {"temp_f": val("temperature_2m"), "wind_mph": val("wind_speed_10m"), "gust_mph": val("wind_gusts_10m"), "precip_pct": val("precipitation_probability")}
