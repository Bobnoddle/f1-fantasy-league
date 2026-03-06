import os
from dotenv import load_dotenv

load_dotenv()

TOKEN: str = os.getenv("DISCORD_TOKEN", "")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is not set.")

DB_PATH: str = os.getenv("DB_PATH", "f1bot.db")
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
