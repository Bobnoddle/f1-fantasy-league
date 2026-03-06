from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import aiohttp

log = logging.getLogger(__name__)


class JolpicaClient:
    """Async HTTP client for the Jolpica Ergast F1 API.

    All methods return plain dicts with normalised keys so the rest of the
    application never has to know the API's response envelope shape.
    """

    BASE = "https://api.jolpi.ca/ergast/f1"

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def get_calendar(self, year: int) -> list[dict]:
        """Return race-calendar entries for *year*.

        Each dict has keys:
          round_number (int), name (str), race_date (str ISO-8601 UTC),
          sprint_date (str | None)
        """
        try:
            data = await self._get(f"/{year}/races.json?limit=100")
            races: list[dict] = (
                data.get("MRData", {})
                    .get("RaceTable", {})
                    .get("Races", [])
            )
        except Exception as exc:
            log.error("get_calendar(%s) failed: %s", year, exc)
            return []

        results: list[dict] = []
        for race in races:
            sprint_date: str | None = None
            sprint = race.get("Sprint")
            if sprint:
                sprint_date = f"{sprint['date']}T{sprint['time']}"

            results.append(
                {
                    "round_number": int(race["round"]),
                    "name": race["raceName"],
                    "race_date": f"{race['date']}T{race['time']}",
                    "sprint_date": sprint_date,
                }
            )
        return results

    async def get_drivers(self, year: int) -> list[dict]:
        """Return driver entries for *year* with constructor info from round 1.

        Each dict has keys: code (str), name (str), team_name (str)
        """
        try:
            drivers_data = await self._get(f"/{year}/drivers.json?limit=100")
            drivers_raw: list[dict] = (
                drivers_data.get("MRData", {})
                             .get("DriverTable", {})
                             .get("Drivers", [])
            )
        except Exception as exc:
            log.error("get_drivers(%s) failed: %s", year, exc)
            return []

        # Build a lookup: driverId -> constructor from round-1 results
        constructor_map: dict[str, str] = {}
        try:
            r1_data = await self._get(f"/{year}/1/results.json?limit=100")
            r1_results: list[dict] = (
                r1_data.get("MRData", {})
                        .get("RaceTable", {})
                        .get("Races", [{}])[0]
                        .get("Results", [])
            )
            for entry in r1_results:
                driver_id: str = entry.get("Driver", {}).get("driverId", "")
                team: str = entry.get("Constructor", {}).get("name", "Unknown")
                if driver_id:
                    constructor_map[driver_id] = team
        except (KeyError, IndexError, Exception) as exc:
            log.warning("Could not fetch round-1 constructors for %s: %s", year, exc)

        results: list[dict] = []
        for d in drivers_raw:
            driver_id = d.get("driverId", "")
            code = d.get("code") or driver_id[:3].upper()
            full_name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
            team_name = constructor_map.get(driver_id, "Unknown")
            results.append({"code": code, "name": full_name, "team_name": team_name})

        return results

    async def get_race_results(self, year: int, round_number: int) -> list[dict]:
        """Return race results for the given round.

        Each dict has keys:
          driver_code (str), finish_position (int), grid_position (int),
          dnf (bool), dsq (bool), fastest_lap (bool), constructor (str)
        """
        try:
            data = await self._get(f"/{year}/{round_number}/results.json?limit=100")
            raw: list[dict] = (
                data.get("MRData", {})
                    .get("RaceTable", {})
                    .get("Races", [{}])[0]
                    .get("Results", [])
            )
        except (KeyError, IndexError):
            return []
        except Exception as exc:
            log.error("get_race_results(%s, %s) failed: %s", year, round_number, exc)
            return []

        return [self._parse_result(r) for r in raw]

    async def get_qualifying_results(self, year: int, round_number: int) -> list[dict]:
        """Return qualifying results for the given round.

        Each dict has keys: driver_code (str), quali_position (int)
        """
        try:
            data = await self._get(f"/{year}/{round_number}/qualifying.json?limit=100")
            raw: list[dict] = (
                data.get("MRData", {})
                    .get("RaceTable", {})
                    .get("Races", [{}])[0]
                    .get("QualifyingResults", [])
            )
        except (KeyError, IndexError):
            return []
        except Exception as exc:
            log.error(
                "get_qualifying_results(%s, %s) failed: %s", year, round_number, exc
            )
            return []

        return [
            {
                "driver_code": r.get("Driver", {}).get("code", ""),
                "quali_position": int(r.get("position", 0)),
            }
            for r in raw
        ]

    async def get_sprint_results(self, year: int, round_number: int) -> list[dict]:
        """Return sprint results for the given round, or [] if no sprint."""
        try:
            data = await self._get(f"/{year}/{round_number}/sprint.json?limit=100")
            races = data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
            if not races:
                return []
            raw: list[dict] = races[0].get("SprintResults", [])
        except (KeyError, IndexError):
            return []
        except Exception as exc:
            log.error(
                "get_sprint_results(%s, %s) failed: %s", year, round_number, exc
            )
            return []

        return [self._parse_result(r) for r in raw]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict[str, Any]:
        """Issue a GET request and return the parsed JSON body.

        Retries once after 5 s on HTTP 429 (rate-limited) or 503.
        Raises ``aiohttp.ClientResponseError`` for other non-2xx responses.
        """
        url = self.BASE + path
        for attempt in range(2):
            try:
                async with self.session.get(url) as resp:
                    if resp.status in (429, 503) and attempt == 0:
                        log.warning(
                            "_get: HTTP %d for %s — retrying in 5s", resp.status, url
                        )
                        await asyncio.sleep(5)
                        continue
                    resp.raise_for_status()
                    return await resp.json(content_type=None)
            except aiohttp.ClientError as exc:
                log.error("HTTP error fetching %s: %s", url, exc)
                raise
        # Unreachable in practice, but satisfies the type checker
        raise RuntimeError(f"_get: exhausted retries for {url}")

    @staticmethod
    def _parse_result(r: dict) -> dict:
        """Normalise a single race/sprint result entry."""
        status: str = r.get("status", "")
        dsq = status == "Disqualified"
        finished = (
            not dsq
            and (status == "Finished" or bool(re.match(r"^\+\d+ Laps?$", status)))
        )
        dnf = not dsq and not finished
        fastest_lap = r.get("FastestLap", {}).get("rank") == "1"

        return {
            "driver_code": r.get("Driver", {}).get("code", ""),
            "finish_position": int(r.get("position", 0)),
            "grid_position": int(r.get("grid", 0)),
            "dnf": dnf,
            "dsq": dsq,
            "fastest_lap": fastest_lap,
            "constructor": r.get("Constructor", {}).get("name", "Unknown"),
        }


# ---------------------------------------------------------------------------
# Startup cache helpers
# ---------------------------------------------------------------------------


async def seed_calendar(client: JolpicaClient, db, year: int) -> None:
    """Upsert the full race calendar for *year* into the DB.

    Uses INSERT OR REPLACE so it is safe to call on every bot restart.
    """
    races = await client.get_calendar(year)
    if not races:
        log.warning("seed_calendar: no races returned for %s", year)
        return

    await db.executemany(
        """
        INSERT OR REPLACE INTO calendar (round_number, name, race_date, sprint_date)
        VALUES (:round_number, :name, :race_date, :sprint_date)
        """,
        races,
    )
    await db.commit()
    log.info("seed_calendar: upserted %d races for %s", len(races), year)


async def seed_drivers(client: JolpicaClient, db, year: int) -> None:
    """Upsert driver roster for *year* into the DB.

    Uses INSERT OR REPLACE so it is safe to call on every bot restart.
    """
    drivers = await client.get_drivers(year)
    if not drivers:
        log.warning("seed_drivers: no drivers returned for %s", year)
        return

    await db.executemany(
        """
        INSERT OR REPLACE INTO driver (code, name, team_name, active)
        VALUES (:code, :name, :team_name, 1)
        """,
        drivers,
    )
    await db.commit()
    log.info("seed_drivers: upserted %d drivers for %s", len(drivers), year)
