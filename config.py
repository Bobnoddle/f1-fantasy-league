import os
import pathlib
from dotenv import load_dotenv

load_dotenv()

TOKEN: str = os.getenv("DISCORD_TOKEN", "")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is not set.")

_raw_db_path: str = os.getenv("DB_PATH", "f1-fantasy-league.db")
try:
    _resolved = pathlib.Path(_raw_db_path).resolve()
except Exception as _e:
    raise ValueError(f"DB_PATH is invalid: {_e}") from _e
if _raw_db_path.startswith("..") or "/../" in _raw_db_path or _raw_db_path.startswith("/etc") or _raw_db_path.startswith("/sys"):
    raise ValueError(f"DB_PATH '{_raw_db_path}' looks unsafe — refusing to start.")
DB_PATH: str = _raw_db_path
SEASON_YEAR: int = int(os.getenv("SEASON_YEAR", "2026"))

# ---------------------------------------------------------------------------
# Scoring constants
# ---------------------------------------------------------------------------

# Race finish points — P1..P20
RACE_POINTS: dict[int, int] = {
    1: 20,
    2: 16,
    3: 13,
    4: 11,
    5: 9,
    6: 7,
    7: 6,
    8: 5,
    9: 4,
    10: 3,
    11: 3,
    12: 3,
    13: 2,
    14: 2,
    15: 2,
    16: 2,
    17: 2,
    18: 2,
    19: 2,
    20: 2,
}

# Qualifying points — P1..P15 score; P16..P20 = 0
QUALI_POINTS: dict[int, int] = {
    1: 8,
    2: 6,
    3: 5,
    4: 4,
    5: 3,
    6: 2,
    7: 2,
    8: 1,
    9: 1,
    10: 1,
    11: 1,
    12: 1,
    13: 1,
    14: 1,
    15: 1,
    16: 0,
    17: 0,
    18: 0,
    19: 0,
    20: 0,
}

# Bonus / penalty constants
COMPLETION_BONUS: int = 3       # Any non-DNF, non-DSQ finish
POSITION_GAIN_BONUS: int = 4    # Per place gained (grid → finish)
FASTEST_LAP_BONUS: int = 5
DSQ_PENALTY: int = -15

# Draft defaults
DRAFT_TIMEOUT: int = 600        # Seconds before a pick expires
