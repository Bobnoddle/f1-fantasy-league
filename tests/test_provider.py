"""Provider tests against captured real responses.

The ``Lapped`` cases come from actual 2026 rounds — a race where most of the
field was classified but reported with that status. They are here because the
previous project's regex (``^\\+\\d+ Laps?$``) matched neither "Lapped" nor
"+N Laps", which silently zeroed a quarter of all points.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.provider.base import (
    DriverResult,
    EventResult,
    Kind,
    PartialDataError,
    Status,
    validate_event_result,
)
from app.provider.jolpica import JolpicaProvider, _table

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def results_from(payload: dict) -> list[DriverResult]:
    """Run fixture rows through the real parser."""
    raw = _table(payload, "RaceTable", "Races")[0]["Results"]
    return [JolpicaProvider._parse(entry, None) for entry in raw]


# ── Envelope walking ────────────────────────────────────────────────────────


def test_table_walks_envelope() -> None:
    payload = load("race_results.json")
    rows = _table(payload, "RaceTable", "Races", "Results")
    assert len(rows) == 22  # a 2026 grid is 22 cars


def test_table_tolerates_missing_keys() -> None:
    assert _table({}, "MRData", "RaceTable", "Races") == []
    assert _table({"MRData": None}, "MRData", "RaceTable", "Races") == []


# ── The Lapped regression ───────────────────────────────────────────────────


def test_lapped_rows_parse_as_classified_finish() -> None:
    """Real fixture: Barcelona 2026, 10 of 22 drivers reported as 'Lapped'."""
    parsed = results_from(load("race_results.json"))
    lapped = [r for r in parsed if r.status is Status.LAPPED]

    assert lapped, "fixture should contain Lapped rows"
    for result in lapped:
        assert result.status.classified
        assert result.position is not None


def test_lapped_p6_would_score_under_old_parser() -> None:
    """The specific driver that scored zero before."""
    parsed = results_from(load("race_results.json"))
    hadjar = next(r for r in parsed if r.code == "HAD")

    assert hadjar.status is Status.LAPPED
    assert hadjar.position == 6
    # Would have been 0 under the old regex; now scores finish + completion.
    from app.domain.scoring import Result, score

    bd = score(
        Result(grid=hadjar.grid, position=hadjar.position, dnf=False, dsq=False, fastest_lap=False)
    )
    assert bd.total > 0


def test_retired_rows_parse_as_dnf() -> None:
    parsed = results_from(load("race_results.json"))
    retired = [r for r in parsed if r.status is Status.RETIRED]

    assert retired
    assert all(not r.status.classified for r in retired)


def test_finished_row_is_classified() -> None:
    parsed = results_from(load("race_results.json"))
    winner = next(r for r in parsed if r.position == 1)

    assert winner.status is Status.FINISHED
    assert winner.fastest_lap is True


def test_exactly_one_fastest_lap() -> None:
    parsed = results_from(load("race_results.json"))
    assert sum(1 for r in parsed if r.fastest_lap) == 1


def test_all_twenty_two_drivers_present() -> None:
    assert len(results_from(load("race_results.json"))) == 22


# ── Validation guards ───────────────────────────────────────────────────────


def test_validate_accepts_full_grid() -> None:
    parsed = results_from(load("race_results.json"))
    result = EventResult(2026, 7, Kind.RACE, "Barcelona GP", tuple(parsed))

    assert validate_event_result(result) is result
    assert result.classified_count >= 15


def test_validate_rejects_partial_payload() -> None:
    parsed = results_from(load("race_results.json"))[:5]
    result = EventResult(2026, 7, Kind.RACE, "Barcelona GP", tuple(parsed))

    with pytest.raises(PartialDataError, match="expected at least"):
        validate_event_result(result)


def test_validate_rejects_missing_fastest_lap() -> None:
    """A payload with no fastest lap is malformed — upstream always sets one."""
    parsed = results_from(load("race_results.json"))
    stripped = tuple(
        DriverResult(
            code=r.code,
            status=r.status,
            position=r.position,
            grid=r.grid,
            fastest_lap=False,
            quali=r.quali,
        )
        for r in parsed
    )
    result = EventResult(2026, 7, Kind.RACE, "Barcelona GP", stripped)

    with pytest.raises(PartialDataError, match="fastest lap"):
        validate_event_result(result)


def test_parse_rejects_unknown_status() -> None:
    """An unknown status must raise, not be guessed."""
    entry = {
        "Driver": {"code": "TST"},
        "status": "Hydrated",
        "position": "1",
        "grid": "1",
    }
    with pytest.raises(PartialDataError, match="Unhandled status"):
        JolpicaProvider._parse(entry, None)


# ── Other fixtures ──────────────────────────────────────────────────────────


def test_sprint_fixture_parses() -> None:
    raw = _table(load("sprint_results.json"), "RaceTable", "Races")[0]
    sprint = raw["SprintResults"]
    parsed = [JolpicaProvider._parse(e, None) for e in sprint]

    assert len(parsed) == 22
    assert sum(1 for r in parsed if r.fastest_lap) == 1
    assert all(
        r.status.classified or r.status in (Status.RETIRED, Status.DID_NOT_START, Status.DSQ)
        for r in parsed
    )


def test_calendar_fixture_parses_sprint_date() -> None:
    races = _table(load("calendar.json"), "RaceTable", "Races")
    with_sprint = [r for r in races if "Sprint" in r]

    assert with_sprint
    assert "date" in with_sprint[0]["Sprint"]


def test_drivers_fixture_shape() -> None:
    drivers = _table(load("drivers.json"), "DriverTable", "Drivers")
    assert len(drivers) >= 20
    assert all("driverId" in d for d in drivers)
