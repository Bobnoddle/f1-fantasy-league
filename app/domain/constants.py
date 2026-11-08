"""Scoring constants. Single source of truth for both the engine and /rules.

Any change here is a scoring change. Version them if you ever need to replay a
past season under the old rules.
"""

from __future__ import annotations

from typing import Final

# ── Race finish points — all 20 classified positions score ──────────────────
RACE_POINTS: Final[dict[int, int]] = {
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

# ── Qualifying — P1..P15 score, P16..P20 score nothing ──────────────────────
QUALI_POINTS: Final[dict[int, int]] = {
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

# ── Bonuses and penalties ───────────────────────────────────────────────────
COMPLETION_BONUS: Final[int] = 3  # classified finish, not DNF/DSQ
POSITION_GAIN_BONUS: Final[int] = 4  # per place gained, grid → finish
FASTEST_LAP_BONUS: Final[int] = 5
DSQ_PENALTY: Final[int] = -15  # zeroed, then this applied

# ── Sprint ──────────────────────────────────────────────────────────────────
SPRINT_DIVISOR: Final[int] = 2  # finish points halved, rounded down

# ── Draft ───────────────────────────────────────────────────────────────────
MAX_TEAM_SIZE: Final[int] = 10
DEFAULT_PICK_DEADLINE: Final[int] = 600  # seconds


def rules_markdown() -> str:
    """Render the scoring rules from the constants above.

    Generated, never hand-written, so /rules cannot drift from the engine.
    """
    lines: list[str] = ["## Race finish", ""]
    lines.append(_compact(RACE_POINTS, "P{pos}"))

    lines += ["", "## Qualifying", "", _compact(QUALI_POINTS, "P{pos}")]
    lines.append("")
    lines.append("P16-P20 = 0 pts")

    lines += [
        "",
        "## Bonuses",
        "",
        f"- Classified finish: **+{COMPLETION_BONUS}**",
        f"- Position gained: **+{POSITION_GAIN_BONUS}** per place (grid to finish)",
        f"- Fastest lap: **+{FASTEST_LAP_BONUS}**",
        "- DNF: **0** (natural floor, no extra penalty)",
        f"- DSQ: **{DSQ_PENALTY}** (zeroed, then penalised)",
        "",
        "## Sprint",
        "",
        f"Finish points at **1/{SPRINT_DIVISOR}** value (rounded down). Completion,",
        "position-gain and fastest-lap bonuses apply at full value. No qualifying points.",
        "",
        "## Draft",
        "",
        "Snake order, randomised. Team size auto-scales to",
        f"`floor(drivers / players)`, capped at {MAX_TEAM_SIZE}.",
        f"Default pick timer: **{DEFAULT_PICK_DEADLINE // 60} minutes**, then auto-pick.",
    ]
    return "\n".join(lines)


def _compact(table: dict[int, int], fmt: str) -> str:
    """Collapse a point table into ranges: 'P2-P4 = 16 pts'."""
    grouped: dict[int, list[int]] = {}
    for pos, pts in sorted(table.items()):
        grouped.setdefault(pts, []).append(pos)

    chunks: list[str] = []
    for pts, positions in sorted(grouped.items(), key=lambda kv: -kv[0]):
        lo, hi = positions[0], positions[-1]
        label = (
            fmt.format(pos=lo)
            if lo == hi
            else f"{fmt.format(pos=lo)}-{fmt.format(pos=hi).removeprefix('P')}"
        )
        chunks.append(f"{label} = {pts} pts")
    return " · ".join(chunks)
