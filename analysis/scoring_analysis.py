"""
F1 Fantasy — Scoring Distribution Analysis
Uses real F1 season results from the Jolpica API (supports multiple seasons)
to validate that the proposed fantasy scoring system creates competitive
balance across the full grid and that the snake draft is fair.
"""

import requests
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from tabulate import tabulate
from collections import defaultdict
import time
import sys

matplotlib.use("Agg")  # headless rendering — saves to file

# ─── Scoring Tables ────────────────────────────────────────────────────────────

RACE_POINTS = {
    1: 25, 2: 20, 3: 16, 4: 13, 5: 11,
    6: 9,  7: 7,  8: 5,  9: 3,  10: 2,
    11: 2, 12: 2, 13: 1, 14: 1, 15: 1,
    16: 1, 17: 1, 18: 1, 19: 1, 20: 1,
}

QUALI_POINTS = {
    1: 10, 2: 8, 3: 6, 4: 5, 5: 4,
    6: 3,  7: 2, 8: 2, 9: 1, 10: 1,
    11: 1, 12: 1, 13: 1, 14: 1, 15: 1,
    # P16–P20: 0
}

POSITION_GAIN_BONUS = 2   # per place gained
DNF_PENALTY = -10
DSQ_PENALTY = -15
FASTEST_LAP_BONUS = 5
SPRINT_HALF = True  # sprint race finish pts at ½ (rounded down)

# ── Alternative scoring config (for comparison) ───────────────────────────────
ALT_RACE_POINTS = {
    1: 20, 2: 16, 3: 13, 4: 11, 5: 9,
    6: 7,  7: 6,  8: 5,  9: 4,  10: 3,
    11: 3, 12: 2, 13: 2, 14: 2, 15: 2,
    16: 1, 17: 1, 18: 1, 19: 1, 20: 1,
}
ALT_QUALI_POINTS = {
    1: 8, 2: 6, 3: 5, 4: 4, 5: 3,
    6: 2, 7: 2, 8: 1, 9: 1, 10: 1,
    11: 1, 12: 1, 13: 1, 14: 1, 15: 1,
}
ALT_POSITION_GAIN_BONUS = 3   # +3/place (up from +2)
ALT_DNF_PENALTY = -5          # -5 (down from -10)
ALT_FASTEST_LAP_BONUS = 5

# ── ALT2 scoring — eliminates double-punishment, raises backmarker floor ──────
# Key philosophy: a DNF already scores 0 finish + 0 gain — no extra penalty
# needed. Completing a race earns +3 flat bonus. P11-P20 floor raised.
ALT2_RACE_POINTS = {
    1: 20, 2: 16, 3: 13, 4: 11, 5: 9,
    6: 7,  7: 6,  8: 5,  9: 4,  10: 3,
    11: 3, 12: 3, 13: 2, 14: 2, 15: 2,
    16: 2, 17: 2, 18: 2, 19: 2, 20: 2,
}
ALT2_QUALI_POINTS = {
    1: 8, 2: 6, 3: 5, 4: 4, 5: 3,
    6: 2, 7: 2, 8: 1, 9: 1, 10: 1,
    11: 1, 12: 1, 13: 1, 14: 1, 15: 1,
}
ALT2_POSITION_GAIN_BONUS = 4   # +4/place — rewards overtaking more
ALT2_DNF_PENALTY = 0           # no extra penalty: DNF already scores 0 finish + 0 gain
ALT2_FASTEST_LAP_BONUS = 5
ALT2_COMPLETION_BONUS = 3      # flat +3 per completed race (not sprint)

# ─── Jolpica API ───────────────────────────────────────────────────────────────

BASE = "https://api.jolpi.ca/ergast/f1"

def fetch(url: str, retries=3) -> dict:
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt+1}/{retries}: {url}")
                time.sleep(2)
            else:
                print(f"  ERROR fetching {url}: {e}")
                return {}

def fetch_all_pages(endpoint: str, year: int) -> list:
    """Fetch all pages of a paginated Jolpica endpoint for a given season year."""
    url = f"{BASE}/{year}/{endpoint}.json?limit=100&offset=0"
    data = fetch(url)
    if not data:
        return []

    table = data.get("MRData", {})
    total = int(table.get("total", 0))
    items = _extract_items(table)

    offset = 100
    while offset < total:
        url = f"{BASE}/{year}/{endpoint}.json?limit=100&offset={offset}"
        data = fetch(url)
        if data:
            items += _extract_items(data.get("MRData", {}))
        offset += 100

    return items

def _extract_items(mrdata: dict) -> list:
    for key in ["RaceTable", "QualifyingTable", "SprintTable"]:
        if key in mrdata:
            return mrdata[key].get("Races", [])
    return []

# ─── Data Loading ──────────────────────────────────────────────────────────────

def load_race_results(year: int) -> list[dict]:
    print("  Fetching race results...")
    races = fetch_all_pages("results", year)
    rows = []
    for race in races:
        name = race["raceName"]
        round_n = int(race["round"])
        for r in race.get("Results", []):
            status = r.get("status", "")
            dnf = status not in ("Finished",) and not status.startswith("+")
            dsq = "Disqualified" in status or "DSQ" in status
            fl = r.get("FastestLap", {}).get("rank") == "1"
            rows.append({
                "race": name,
                "round": round_n,
                "driver_code": r["Driver"]["code"],
                "driver_name": f"{r['Driver']['givenName']} {r['Driver']['familyName']}",
                "constructor": r["Constructor"]["name"],
                "grid": int(r.get("grid", 0)) or None,
                "finish": int(r["position"]),
                "dnf": dnf and not dsq,
                "dsq": dsq,
                "fastest_lap": fl,
                "race_type": "race",
            })
    return rows

def load_quali_results(year: int) -> list[dict]:
    print("  Fetching qualifying results...")
    races = fetch_all_pages("qualifying", year)
    rows = []
    for race in races:
        name = race["raceName"]
        round_n = int(race["round"])
        for r in race.get("QualifyingResults", []):
            rows.append({
                "race": name,
                "round": round_n,
                "driver_code": r["Driver"]["code"],
                "quali_pos": int(r["position"]),
            })
    return rows

def load_sprint_results(year: int) -> list[dict]:
    print("  Fetching sprint results...")
    races = fetch_all_pages("sprint", year)
    rows = []
    for race in races:
        name = race["raceName"]
        round_n = int(race["round"])
        for r in race.get("SprintResults", []):
            status = r.get("status", "")
            dnf = status not in ("Finished",) and not status.startswith("+")
            rows.append({
                "race": name,
                "round": round_n,
                "driver_code": r["Driver"]["code"],
                "driver_name": f"{r['Driver']['givenName']} {r['Driver']['familyName']}",
                "constructor": r["Constructor"]["name"],
                "grid": int(r.get("grid", 0)) or None,
                "finish": int(r["position"]),
                "dnf": dnf,
                "dsq": False,
                "fastest_lap": False,
                "race_type": "sprint",
            })
    return rows

# ─── Scoring Engine ────────────────────────────────────────────────────────────

def score_race_row(row: dict, cfg: dict = None) -> dict:
    if cfg is None:
        cfg = {
            "race_pts": RACE_POINTS, "gain": POSITION_GAIN_BONUS,
            "fl": FASTEST_LAP_BONUS, "dnf": DNF_PENALTY, "dsq": DSQ_PENALTY,
            "completion_bonus": 0,
        }
    is_sprint = row["race_type"] == "sprint"
    finish = row["finish"]
    grid = row["grid"]
    dnf = row["dnf"]
    dsq = row["dsq"]

    if dsq:
        finish_pts = 0
    elif is_sprint:
        finish_pts = cfg["race_pts"].get(finish, 0) // 2
    else:
        finish_pts = cfg["race_pts"].get(finish, 0)

    delta_pts = 0
    if grid and grid > 0 and not dnf and not dsq:
        gain = grid - finish
        if gain > 0:
            delta_pts = gain * cfg["gain"]

    fastest_pts = cfg["fl"] if row.get("fastest_lap") and not is_sprint else 0
    dnf_pts  = cfg["dnf"] if dnf else 0
    dsq_pts  = cfg["dsq"] if dsq else 0
    # Completion bonus: flat reward for finishing any race (not sprint, not DNF/DSQ)
    comp_pts = cfg.get("completion_bonus", 0) if (not dnf and not dsq and not is_sprint) else 0
    total = finish_pts + delta_pts + fastest_pts + dnf_pts + dsq_pts + comp_pts

    return {
        "finish_pts": finish_pts,
        "delta_pts": delta_pts,
        "fastest_pts": fastest_pts,
        "penalty_pts": dnf_pts + dsq_pts,
        "completion_pts": comp_pts,
        "total_race_pts": total,
    }

def score_quali_row(pos: int, quali_pts: dict = None) -> int:
    if quali_pts is None:
        quali_pts = QUALI_POINTS
    return quali_pts.get(pos, 0)

def build_driver_df(race_rows, quali_rows, sprint_rows, driver_info,
                    cfg_race, cfg_quali, gain, fl_bonus, dnf_pen, dsq_pen,
                    completion_bonus: int = 0) -> pd.DataFrame:
    """Run scoring with a given config and return a summary DataFrame."""
    cfg = {"race_pts": cfg_race, "gain": gain, "fl": fl_bonus,
           "dnf": dnf_pen, "dsq": dsq_pen, "completion_bonus": completion_bonus}
    driver_scores = defaultdict(lambda: {
        "finish_pts": 0, "delta_pts": 0, "fastest_pts": 0, "completion_pts": 0,
        "penalty_pts": 0, "quali_pts": 0,
        "total": 0, "races": 0, "dnf_count": 0,
    })

    for row in race_rows + sprint_rows:
        code = row["driver_code"]
        s = score_race_row(row, cfg)
        ds = driver_scores[code]
        ds["finish_pts"]     += s["finish_pts"]
        ds["delta_pts"]      += s["delta_pts"]
        ds["fastest_pts"]    += s["fastest_pts"]
        ds["completion_pts"] += s["completion_pts"]
        ds["penalty_pts"]    += s["penalty_pts"]
        ds["total"]          += s["total_race_pts"]
        ds["races"]          += 1
        if row["dnf"]:
            ds["dnf_count"] += 1

    quali_by_round = defaultdict(dict)
    for r in quali_rows:
        quali_by_round[r["round"]][r["driver_code"]] = r["quali_pos"]
    for round_n, drivers in quali_by_round.items():
        for code, pos in drivers.items():
            pts = score_quali_row(pos, cfg_quali)
            driver_scores[code]["quali_pts"] += pts
            driver_scores[code]["total"]     += pts

    records = []
    for code, ds in driver_scores.items():
        info = driver_info.get(code, {"name": code, "constructor": "?"})
        records.append({
            "Code": code,
            "Driver": info["name"],
            "Team": info["constructor"],
            "Total": ds["total"],
            "Finish Pts": ds["finish_pts"],
            "Comp Pts": ds["completion_pts"],
            "Quali Pts": ds["quali_pts"],
            "Delta Pts": ds["delta_pts"],
            "FL Pts": ds["fastest_pts"],
            "Penalties": ds["penalty_pts"],
            "DNFs": ds["dnf_count"],
            "Avg/Race": round(ds["total"] / max(ds["races"], 1), 1),
        })
    return pd.DataFrame(records).sort_values("Total", ascending=False).reset_index(drop=True)

# ─── Draft Simulation ──────────────────────────────────────────────────────────

def simulate_snake_draft(df: pd.DataFrame, num_players: int = 5, n_sims: int = 1_000) -> dict:
    """
    Simulate a fantasy snake draft with `num_players` players.

    Two modes:
    • Deterministic optimal — everyone drafts best-available with perfect
      knowledge of final season points. Shows the theoretical ceiling for
      pick-slot advantage.
    • Monte Carlo (n_sims) — each sim adds Gaussian noise (σ=60 pts) to the
      true points before ranking, modelling realistic draft-day uncertainty.
      Reports mean ± std per pick slot to test fairness.
    """
    n_drivers     = len(df)
    picks_per     = min(10, n_drivers // num_players)
    total_picks   = picks_per * num_players

    # Snake pick order: which player slot takes each overall pick
    snake_order = []
    for rnd in range(picks_per):
        if rnd % 2 == 0:
            snake_order.extend(range(num_players))
        else:
            snake_order.extend(range(num_players - 1, -1, -1))

    drivers = df["Driver"].values
    points  = df["Total"].values  # already sorted desc

    # ── Deterministic optimal draft ───────────────────────────────────────────
    sort_idx      = np.argsort(points)[::-1]
    sorted_pts    = points[sort_idx][:total_picks]
    sorted_names  = drivers[sort_idx][:total_picks]

    opt_teams  = {p: [] for p in range(num_players)}
    opt_totals = {p: 0  for p in range(num_players)}
    for pick, player in enumerate(snake_order[:total_picks]):
        opt_teams[player].append((sorted_names[pick], int(sorted_pts[pick])))
        opt_totals[player] += int(sorted_pts[pick])

    # ── Monte Carlo: imperfect-knowledge draft ────────────────────────────────
    rng         = np.random.default_rng(42)
    noise_sigma = 60   # realistic fantasy-ranking uncertainty in pts
    slot_totals = {p: [] for p in range(num_players)}

    for _ in range(n_sims):
        noisy     = points + rng.normal(0, noise_sigma, len(points))
        order_idx = np.argsort(noisy)[::-1]
        real_pts  = points[order_idx]   # actual points of each driver in draft order
        p_totals  = {p: 0 for p in range(num_players)}
        for pick, player in enumerate(snake_order[:total_picks]):
            p_totals[player] += real_pts[pick]
        for p, t in p_totals.items():
            slot_totals[p].append(t)

    return {
        "picks_per":      picks_per,
        "num_players":    num_players,
        "snake_order":    snake_order,
        "optimal_totals": opt_totals,
        "optimal_teams":  opt_teams,
        "sim_means":      {p: float(np.mean(v)) for p, v in slot_totals.items()},
        "sim_stds":       {p: float(np.std(v))  for p, v in slot_totals.items()},
        "sim_all":        slot_totals,
    }

# ─── Helpers shared by analysis + report ──────────────────────────────────────

def balance_stats(df: pd.DataFrame, label: str) -> dict:
    totals = df["Total"].values
    top4   = df.iloc[0:4]["Total"].mean()
    mid    = df.iloc[4:16]["Total"].mean()
    back   = df.iloc[16:]["Total"].mean() if len(df) > 16 else 0
    negs   = int((df["Total"] < 0).sum())
    delta_share = df["Delta Pts"].sum() / max(df["Total"].clip(lower=0).sum(), 1) * 100
    ratio  = float(totals[0]) / max(float(df.iloc[9]["Total"]), 1)
    return {
        "label":       label,
        "p1":          int(totals[0]),
        "p10":         int(df.iloc[9]["Total"]),
        "last":        int(totals[-1]),
        "negs":        negs,
        "ratio":       ratio,
        "mid_pct":     mid / top4 * 100,
        "back_pct":    back / top4 * 100,
        "gain_share":  delta_share,
        "std":         float(np.std(totals)),
        "top4_avg":    top4,
    }

def verdict(stats: dict) -> str:
    if stats["mid_pct"] >= 60 and stats["negs"] == 0:
        return "✅  GOOD"
    elif stats["mid_pct"] >= 45 and stats["negs"] <= 2:
        return "⚠️  MODERATE"
    else:
        return "❌  POOR"

def draft_verdict(draft: dict) -> str:
    n_p = draft["num_players"]
    grand_mean = sum(draft["sim_means"].values()) / n_p
    max_bias   = max(abs(draft["sim_means"][p] - grand_mean) for p in range(n_p))
    pct = max_bias / grand_mean * 100
    if pct < 4:
        return f"✅  FAIR ({pct:.1f}% max bias)"
    elif pct < 8:
        return f"⚠️  MINOR BIAS ({pct:.1f}% max bias)"
    else:
        return f"❌  BIASED ({pct:.1f}% max bias)"

# ─── Analysis ──────────────────────────────────────────────────────────────────

def run_analysis(year: int = 2025):
    print(f"\n  [{year}] Fetching data from Jolpica API...")
    race_rows   = load_race_results(year)
    quali_rows  = load_quali_results(year)
    sprint_rows = load_sprint_results(year)

    if not race_rows:
        print("  ERROR: No race data returned. Check API availability.")
        sys.exit(1)

    num_races   = len(set(r["round"] for r in race_rows if r["race_type"] == "race"))
    num_sprints = len(set(r["round"] for r in sprint_rows))
    print(f"  [{year}] {num_races} races · {num_sprints} sprints · "
          f"{len(quali_rows) // 20 if quali_rows else 0} qualifying sessions\n")

    driver_info = {}
    for row in race_rows + sprint_rows:
        driver_info[row["driver_code"]] = {
            "name": row["driver_name"], "constructor": row["constructor"]
        }

    # ── Build 3 scoring variants ──────────────────────────────────────────────
    df_cur = build_driver_df(
        race_rows, quali_rows, sprint_rows, driver_info,
        RACE_POINTS, QUALI_POINTS, POSITION_GAIN_BONUS, FASTEST_LAP_BONUS,
        DNF_PENALTY, DSQ_PENALTY, completion_bonus=0,
    )
    df_alt = build_driver_df(
        race_rows, quali_rows, sprint_rows, driver_info,
        ALT_RACE_POINTS, ALT_QUALI_POINTS, ALT_POSITION_GAIN_BONUS, ALT_FASTEST_LAP_BONUS,
        ALT_DNF_PENALTY, DSQ_PENALTY, completion_bonus=0,
    )
    df_alt2 = build_driver_df(
        race_rows, quali_rows, sprint_rows, driver_info,
        ALT2_RACE_POINTS, ALT2_QUALI_POINTS, ALT2_POSITION_GAIN_BONUS, ALT2_FASTEST_LAP_BONUS,
        ALT2_DNF_PENALTY, DSQ_PENALTY, completion_bonus=ALT2_COMPLETION_BONUS,
    )
    for df in (df_cur, df_alt, df_alt2):
        df.index += 1

    st_cur  = balance_stats(df_cur,  "Current   (F1 pts, +2 gain, -10 DNF)")
    st_alt  = balance_stats(df_alt,  "Alt       (+3 gain, -5 DNF)")
    st_alt2 = balance_stats(df_alt2, "Alt2      (+4 gain, no DNF pen, +3 comp)")

    # ── Compact console summary ───────────────────────────────────────────────
    W = 26
    print(f"  {'Metric':<20} {'Current':>{W}} {'Alt':>{W}} {'Alt2 ★':>{W}}")
    print(f"  {'-'*92}")
    rows_summary = [
        ("P1 (leader)",  st_cur["p1"],              st_alt["p1"],              st_alt2["p1"]),
        ("P10",          st_cur["p10"],              st_alt["p10"],             st_alt2["p10"]),
        ("Last place",   st_cur["last"],             st_alt["last"],            st_alt2["last"]),
        ("Negatives",    st_cur["negs"],             st_alt["negs"],            st_alt2["negs"]),
        ("P1/P10 ratio", f"{st_cur['ratio']:.2f}x", f"{st_alt['ratio']:.2f}x", f"{st_alt2['ratio']:.2f}x"),
        ("Mid/Top-4 %",  f"{st_cur['mid_pct']:.0f}%", f"{st_alt['mid_pct']:.0f}%", f"{st_alt2['mid_pct']:.0f}%"),
        ("Back/Top-4 %", f"{st_cur['back_pct']:.0f}%", f"{st_alt['back_pct']:.0f}%", f"{st_alt2['back_pct']:.0f}%"),
        ("Std dev",      f"{st_cur['std']:.0f}",    f"{st_alt['std']:.0f}",    f"{st_alt2['std']:.0f}"),
        ("Verdict",      verdict(st_cur),            verdict(st_alt),           verdict(st_alt2)),
    ]
    for label, c, a, a2 in rows_summary:
        print(f"  {label:<20} {str(c):>{W}} {str(a):>{W}} {str(a2):>{W}}")

    # ── Draft simulation on Alt2 (best config) ────────────────────────────────
    draft = simulate_snake_draft(df_alt2, num_players=5, n_sims=1_000)
    n_p   = draft["num_players"]
    grand_mean = sum(draft["sim_means"].values()) / n_p
    print(f"\n  {'Draft (5p, Alt2)':<20} {'Picks/player':>{W}} {draft['picks_per']:>{W}}")
    mc_summary = "  " + "  ".join(
        f"P{p+1}: {draft['sim_means'][p]:.0f} ({(draft['sim_means'][p]-grand_mean)/grand_mean*100:+.1f}%)"
        for p in range(n_p)
    )
    print(mc_summary)
    print(f"  {'':<20} {'Fairness verdict':>{W}} {draft_verdict(draft):>{W}}")

    # ── Report + charts ───────────────────────────────────────────────────────
    import os
    os.makedirs("analysis/charts", exist_ok=True)
    report_path = generate_report(year, df_cur, df_alt, df_alt2,
                                  st_cur, st_alt, st_alt2, draft)
    print(f"\n  Report  → {report_path}")
    print(f"  Charts  → analysis/charts/{year}_*.png")

    _plot_leaderboard(df_alt2, year)
    _plot_stacked_breakdown(df_alt2, year)
    _plot_points_distribution(df_alt2, year)
    _plot_consistency(df_alt2, year)
    _plot_comparison(df_cur, df_alt, df_alt2, year)
    _plot_draft_fairness(draft, year)
    print()

    return df_cur, df_alt, df_alt2, draft


# ─── Report generator ─────────────────────────────────────────────────────────

def generate_report(year: int,
                    df_cur: pd.DataFrame, df_alt: pd.DataFrame, df_alt2: pd.DataFrame,
                    st_cur: dict, st_alt: dict, st_alt2: dict,
                    draft: dict) -> str:
    """Write a full markdown analysis report and return the file path."""
    import os
    os.makedirs("analysis", exist_ok=True)
    path = f"analysis/report_{year}.md"

    n_p = draft["num_players"]
    grand_mean = sum(draft["sim_means"].values()) / n_p
    tiers = [(1, 4, "Top 4"), (5, 10, "P5–10"), (11, 16, "P11–16"), (17, 999, "P17+")]

    lines = []
    a = lines.append

    a(f"# F1 Fantasy Scoring Analysis — {year} Season")
    a(f"\n_Generated automatically by `analysis/scoring_analysis.py`_\n")

    # Config reference
    a("## Scoring Configs Compared\n")
    a("| Config | Race pts top | Gain bonus | DNF penalty | Completion bonus |")
    a("|--------|-------------|------------|-------------|-----------------|")
    a(f"| **Current** | P1=25 (F1 standard) | +2/place | −10 | — |")
    a(f"| **Alt** | P1=20 (compressed) | +3/place | −5 | — |")
    a(f"| **Alt2 ★** | P1=20 (compressed) | +4/place | 0 | +3/race finish |")
    a("")

    # Balance summary table
    a("## Balance Summary\n")
    a(f"| Metric | Current | Alt | Alt2 ★ |")
    a(f"|--------|---------|-----|--------|")
    metrics = [
        ("P1 (leader)",    st_cur["p1"],               st_alt["p1"],              st_alt2["p1"]),
        ("P10",            st_cur["p10"],               st_alt["p10"],             st_alt2["p10"]),
        ("Last place",     st_cur["last"],              st_alt["last"],            st_alt2["last"]),
        ("Negatives",      st_cur["negs"],              st_alt["negs"],            st_alt2["negs"]),
        ("P1/P10 ratio",   f"{st_cur['ratio']:.2f}x",  f"{st_alt['ratio']:.2f}x", f"{st_alt2['ratio']:.2f}x"),
        ("Mid/Top-4",      f"{st_cur['mid_pct']:.0f}%",f"{st_alt['mid_pct']:.0f}%", f"{st_alt2['mid_pct']:.0f}%"),
        ("Back/Top-4",     f"{st_cur['back_pct']:.0f}%",f"{st_alt['back_pct']:.0f}%",f"{st_alt2['back_pct']:.0f}%"),
        ("Std dev",        f"{st_cur['std']:.0f}",     f"{st_alt['std']:.0f}",    f"{st_alt2['std']:.0f}"),
        ("**Verdict**",    verdict(st_cur),             verdict(st_alt),           verdict(st_alt2)),
    ]
    for row in metrics:
        a(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} |")
    a("")

    # Tier breakdown
    a("## Tier Gaps\n")
    a(f"| Tier | Cur avg | Cur range | Alt avg | Alt range | Alt2 avg | Alt2 range |")
    a(f"|------|---------|-----------|---------|-----------|----------|------------|")
    for lo, hi, label in tiers:
        cs  = df_cur.iloc[lo-1:hi]
        as_ = df_alt.iloc[lo-1:hi]
        a2s = df_alt2.iloc[lo-1:hi]
        if len(cs):
            a(f"| {label} "
              f"| {cs['Total'].mean():.0f} | {cs['Total'].min()}–{cs['Total'].max()} "
              f"| {as_['Total'].mean():.0f} | {as_['Total'].min()}–{as_['Total'].max()} "
              f"| {a2s['Total'].mean():.0f} | {a2s['Total'].min()}–{a2s['Total'].max()} |")
    a("")

    # Full leaderboard for each config
    for label, df in [("Current", df_cur), ("Alt", df_alt), ("Alt2 ★ (Recommended)", df_alt2)]:
        a(f"## {label} Leaderboard\n")
        a("| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |")
        a("|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|")
        for i, row in df.reset_index(drop=True).iterrows():
            a(f"| {i+1} | {row['Driver']} | {row['Team']} | **{row['Total']}** "
              f"| {row['Finish Pts']} | {row['Comp Pts']} | {row['Quali Pts']} "
              f"| {row['Delta Pts']} | {row['FL Pts']} | {row['Penalties']} "
              f"| {row['DNFs']} | {row['Avg/Race']} |")
        a("")

    # Rank shift current → alt2
    a("## Rank Shift: Current → Alt2\n")
    merged = df_cur[["Driver", "Team", "Total"]].rename(columns={"Total": "Current"})
    merged = merged.merge(df_alt2[["Driver", "Total"]].rename(columns={"Total": "Alt2"}), on="Driver")
    merged["Change"] = merged["Alt2"] - merged["Current"]
    merged = merged.reset_index(drop=True)
    a("| Rank | Driver | Team | Current | Alt2 | Change |")
    a("|------|--------|------|---------|------|--------|")
    for i, row in merged.iterrows():
        arrow = "▲" if row["Change"] > 0 else ("▼" if row["Change"] < 0 else "—")
        a(f"| {i+1} | {row['Driver']} | {row['Team']} | {row['Current']} "
          f"| {row['Alt2']} | {arrow} {abs(int(row['Change']))} |")
    a("")

    # DNF risk
    a("## DNF Risk Analysis\n")
    a("> DNF penalty: Current=−10 · Alt=−5 · **Alt2=0** (implicit: DNF scores 0 finish + 0 gain)\n")
    dnf_df = df_cur[df_cur["DNFs"] > 2][["Driver", "Team", "DNFs", "Penalties", "Total"]].copy()
    dnf_df = dnf_df.merge(df_alt[["Driver","Total"]].rename(columns={"Total":"Alt"}), on="Driver")
    dnf_df = dnf_df.merge(df_alt2[["Driver","Total"]].rename(columns={"Total":"Alt2"}), on="Driver")
    dnf_df = dnf_df.sort_values("DNFs", ascending=False)
    a("| Driver | Team | DNFs | Penalties (Cur) | Total Cur | Alt | Alt2 |")
    a("|--------|------|------|-----------------|-----------|-----|------|")
    for _, row in dnf_df.iterrows():
        a(f"| {row['Driver']} | {row['Team']} | {row['DNFs']} | {row['Penalties']} "
          f"| {row['Total']} | {row['Alt']} | {row['Alt2']} |")
    a("")

    # Draft simulation
    a("## Snake Draft Simulation (5 Players · Alt2 Scoring)\n")
    a(f"- Picks per player: **{draft['picks_per']}** (`min(10, {len(df_alt2)}//{n_p})`)")
    a(f"- Total drafted: **{draft['picks_per'] * n_p}** / {len(df_alt2)} drivers")
    a(f"- Simulation: 1,000 drafts with σ=60pt knowledge noise\n")

    a("### Optimal Draft (perfect knowledge)\n")
    a("| Slot | Drivers | Team Total |")
    a("|------|---------|------------|")
    for p in range(n_p):
        drivers_str = ", ".join(f"{n} ({v})" for n, v in draft["optimal_teams"][p])
        a(f"| Pick {p+1} | {drivers_str} | **{draft['optimal_totals'][p]}** |")
    opt_vals = [draft["optimal_totals"][p] for p in range(n_p)]
    a(f"\nOptimal spread: **{max(opt_vals) - min(opt_vals)} pts** "
      f"({(max(opt_vals)-min(opt_vals))/max(opt_vals)*100:.1f}% of top team)\n")

    a("### Monte Carlo Results\n")
    a("| Slot | Avg Total | Std Dev | vs Mean | % Diff |")
    a("|------|-----------|---------|---------|--------|")
    for p in range(n_p):
        m    = draft["sim_means"][p]
        s    = draft["sim_stds"][p]
        diff = m - grand_mean
        a(f"| Pick {p+1} | {m:.0f} | ±{s:.0f} | {diff:+.0f} | {diff/grand_mean*100:+.1f}% |")
    a(f"\n**Fairness verdict: {draft_verdict(draft)}**\n")

    # Charts index
    a("## Charts Generated\n")
    for n, desc in [
        (f"{year}_01_leaderboard", "Full grid leaderboard (Alt2 scoring)"),
        (f"{year}_02_stacked_breakdown", "Points source breakdown per driver"),
        (f"{year}_03_distribution", "Points distribution histogram + rank curve"),
        (f"{year}_04_consistency", "Consistency vs season total scatter"),
        (f"{year}_05_comparison", "3-way config comparison (Current / Alt / Alt2)"),
        (f"{year}_06_draft_fairness", "Snake draft fairness — optimal + Monte Carlo"),
    ]:
        a(f"- `analysis/charts/{n}.png` — {desc}")
    a("")

    with open(path, "w") as f:
        f.write("\n".join(lines))

    return path


# ─── Charts ────────────────────────────────────────────────────────────────────

TEAM_COLORS = {
    "Red Bull": "#1E41FF",
    "McLaren": "#FF8000",
    "Ferrari": "#DC0000",
    "Mercedes": "#00D2BE",
    "Aston Martin": "#006F62",
    "Alpine": "#0090FF",
    "Williams": "#005AFF",
    "Racing Bulls": "#2B4562",
    "Kick Sauber": "#52E252",
    "Haas F1 Team": "#B6BABD",
}

def _team_color(team: str) -> str:
    for k, v in TEAM_COLORS.items():
        if k.lower() in team.lower():
            return v
    return "#888888"

def _plot_leaderboard(df: pd.DataFrame, year: int = 2025):
    """Bar chart — total fantasy points by driver, coloured by team."""
    fig, ax = plt.subplots(figsize=(14, 8))
    colors = [_team_color(t) for t in df["Team"]]
    bars = ax.barh(df["Driver"][::-1], df["Total"][::-1], color=colors[::-1], edgecolor="white", linewidth=0.4)

    for bar, pts in zip(bars, df["Total"][::-1]):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                f"{pts}", va="center", fontsize=8, color="#333333")

    ax.set_xlabel("Fantasy Points", fontsize=11)
    ax.set_title(f"F1 Fantasy {year} — Full Season Leaderboard", fontsize=14, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlim(0, df["Total"].max() * 1.12)

    # Legend
    seen = {}
    for team, color in zip(df["Team"], colors):
        if team not in seen:
            seen[team] = color
    patches = [mpatches.Patch(color=c, label=t) for t, c in seen.items()]
    ax.legend(handles=patches, loc="lower right", fontsize=8, ncol=2)

    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_01_leaderboard.png", dpi=150)
    plt.close()
    print(f"    ✓ {year}_01_leaderboard.png")

def _plot_stacked_breakdown(df: pd.DataFrame, year: int = 2025):
    """Stacked bar — breakdown of point sources per driver."""
    fig, ax = plt.subplots(figsize=(14, 8))
    drivers = df["Driver"][::-1]
    y = np.arange(len(drivers))

    finish = df["Finish Pts"][::-1].values
    quali  = df["Quali Pts"][::-1].values
    delta  = df["Delta Pts"][::-1].values
    fl     = df["FL Pts"][::-1].values
    pen    = df["Penalties"][::-1].values  # negative

    ax.barh(y, finish, label="Race Finish",    color="#E63946", height=0.6)
    ax.barh(y, quali,  left=finish,            label="Qualifying",  color="#457B9D", height=0.6)
    ax.barh(y, delta,  left=finish+quali,      label="Pos Gain +2", color="#2A9D8F", height=0.6)
    ax.barh(y, fl,     left=finish+quali+delta,label="Fastest Lap", color="#E9C46A", height=0.6)
    ax.barh(y, pen,    label="DNF/DSQ Penalty",color="#6D1A36", height=0.6)

    ax.set_yticks(y)
    ax.set_yticklabels(drivers, fontsize=8)
    ax.set_xlabel("Fantasy Points", fontsize=11)
    ax.set_title(f"F1 Fantasy {year} — Points Source Breakdown per Driver", fontsize=14, fontweight="bold", pad=12)
    ax.axvline(0, color="black", linewidth=0.7)
    ax.legend(loc="lower right", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_02_stacked_breakdown.png", dpi=150)
    plt.close()
    print(f"    ✓ {year}_02_stacked_breakdown.png")

def _plot_points_distribution(df: pd.DataFrame, year: int = 2025):
    """Histogram + KDE of total points to check spread."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # Histogram
    ax1 = axes[0]
    totals = df["Total"].values
    ax1.hist(totals, bins=10, color="#457B9D", edgecolor="white", linewidth=0.5, alpha=0.9)
    ax1.axvline(np.mean(totals), color="#E63946", linewidth=1.5, linestyle="--", label=f"Mean: {np.mean(totals):.0f}")
    ax1.axvline(np.median(totals), color="#2A9D8F", linewidth=1.5, linestyle="--", label=f"Median: {np.median(totals):.0f}")
    ax1.set_xlabel("Total Fantasy Points", fontsize=11)
    ax1.set_ylabel("Drivers", fontsize=11)
    ax1.set_title("Points Distribution Across Grid", fontsize=12, fontweight="bold")
    ax1.legend(fontsize=9)
    ax1.spines[["top", "right"]].set_visible(False)

    # Rank vs Points scatter with trend
    ax2 = axes[1]
    ranks = df.index.values
    ax2.scatter(ranks, totals, c=[_team_color(t) for t in df["Team"]], s=80, zorder=3, edgecolors="white", linewidth=0.4)
    for i, (rank, pts, driver) in enumerate(zip(ranks, totals, df["Driver"])):
        ax2.annotate(driver.split()[-1], (rank, pts), textcoords="offset points",
                     xytext=(0, 6), ha="center", fontsize=7, color="#555555")
    z = np.polyfit(ranks, totals, 2)
    p = np.poly1d(z)
    xp = np.linspace(ranks.min(), ranks.max(), 100)
    ax2.plot(xp, p(xp), "--", color="#888888", alpha=0.6, linewidth=1.2, label="Trend")
    ax2.set_xlabel("Fantasy Rank", fontsize=11)
    ax2.set_ylabel("Total Fantasy Points", fontsize=11)
    ax2.set_title("Rank vs Points — Curve Shape", fontsize=12, fontweight="bold")
    ax2.spines[["top", "right"]].set_visible(False)
    ax2.legend(fontsize=9)

    plt.suptitle(f"F1 Fantasy {year} — Scoring Distribution Analysis", fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_03_distribution.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    ✓ {year}_03_distribution.png")

def _plot_consistency(df: pd.DataFrame, year: int = 2025):
    """Scatter: per-race average vs total — visualises consistency vs peak."""
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.scatter(
        df["Avg/Race"], df["Total"],
        c=[_team_color(t) for t in df["Team"]],
        s=120, zorder=3, edgecolors="white", linewidth=0.5,
    )
    for _, row in df.iterrows():
        ax.annotate(
            row["Driver"].split()[-1],
            (row["Avg/Race"], row["Total"]),
            textcoords="offset points", xytext=(6, 2),
            fontsize=8, color="#333333",
        )
    ax.set_xlabel("Avg Points per Race Weekend", fontsize=11)
    ax.set_ylabel("Total Season Points", fontsize=11)
    ax.set_title(f"F1 Fantasy {year} — Consistency vs Season Total", fontsize=13, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    avg_x = df["Avg/Race"].mean()
    avg_y = df["Total"].mean()
    ax.axvline(avg_x, color="#CCCCCC", linewidth=1, linestyle="--")
    ax.axhline(avg_y, color="#CCCCCC", linewidth=1, linestyle="--")
    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_04_consistency.png", dpi=150)
    plt.close()
    print(f"    ✓ {year}_04_consistency.png")


def _plot_comparison(df_cur: pd.DataFrame, df_alt: pd.DataFrame, df_alt2: pd.DataFrame, year: int = 2025):
    """3-way grouped bar comparison: Current / Alt / Alt2 per driver."""
    base = df_cur[["Driver", "Total"]].rename(columns={"Total": "Current"})
    m = base.merge(df_alt[["Driver", "Total"]].rename(columns={"Total": "Alt"}), on="Driver")
    m = m.merge(df_alt2[["Driver", "Total"]].rename(columns={"Total": "Alt2"}), on="Driver")
    m = m.sort_values("Current", ascending=True)

    fig, ax = plt.subplots(figsize=(14, 10))
    y     = np.arange(len(m))
    bar_h = 0.26

    ax.barh(y + bar_h,  m["Current"], height=bar_h, color="#E63946", label="Current (F1 pts, -10 DNF)",          alpha=0.85)
    ax.barh(y,          m["Alt"],     height=bar_h, color="#457B9D", label="Alt (compressed, -5 DNF)",          alpha=0.85)
    ax.barh(y - bar_h,  m["Alt2"],    height=bar_h, color="#2A9D8F", label="Alt2 ★ (+4 gain, no pen, +3 comp)", alpha=0.85)

    ax.axvline(0, color="black", linewidth=0.7)
    ax.set_yticks(y)
    ax.set_yticklabels(m["Driver"], fontsize=8)
    ax.set_xlabel("Total Fantasy Points", fontsize=11)
    ax.set_title(
        f"F1 Fantasy {year} — 3-Way Scoring Config Comparison",
        fontsize=13, fontweight="bold", pad=12,
    )
    ax.legend(fontsize=9, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_05_comparison.png", dpi=150)
    plt.close()
    print(f"    ✓ {year}_05_comparison.png")


def _plot_draft_fairness(draft: dict, year: int = 2025):
    """Two-pane: optimal team totals (bar) + Monte Carlo distribution (box) per pick slot."""
    n_p    = draft["num_players"]
    labels = [f"Pick {p+1}" for p in range(n_p)]
    opt_v  = [draft["optimal_totals"][p] for p in range(n_p)]
    colors = ["#E63946" if i == 0 else "#457B9D" for i in range(n_p)]

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    # Left: optimal draft totals
    ax1 = axes[0]
    bars = ax1.bar(labels, opt_v, color=colors, edgecolor="white", linewidth=0.5)
    for bar, v in zip(bars, opt_v):
        ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 6,
                 str(v), ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax1.axhline(float(np.mean(opt_v)), color="#888888", linewidth=1.4, linestyle="--",
                label=f"Mean: {np.mean(opt_v):.0f}")
    ax1.set_ylabel("Total Fantasy Points", fontsize=11)
    ax1.set_title(
        f"Optimal Draft — Team Totals\n(perfect knowledge, {draft['picks_per']} picks each)",
        fontsize=11, fontweight="bold",
    )
    ax1.legend(fontsize=9)
    ax1.spines[["top", "right"]].set_visible(False)
    ymin = min(opt_v) * 0.9
    ax1.set_ylim(ymin, max(opt_v) * 1.12)

    # Right: Monte Carlo box plot
    ax2 = axes[1]
    data = [draft["sim_all"][p] for p in range(n_p)]
    bp = ax2.boxplot(data, tick_labels=labels, patch_artist=True,
                     medianprops={"color": "white", "linewidth": 2},
                     whiskerprops={"linewidth": 1.2},
                     capprops={"linewidth": 1.2})
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)
    means = [float(np.mean(d)) for d in data]
    ax2.plot(range(1, n_p + 1), means, "o--", color="#E9C46A",
             zorder=5, label="Mean", markersize=7)
    ax2.axhline(float(np.mean(means)), color="#888888", linewidth=1.4, linestyle="--")
    ax2.set_ylabel("Total Fantasy Points", fontsize=11)
    ax2.set_title(
        f"Monte Carlo (1,000 drafts, σ=60 pts)\nExpected outcome per pick slot",
        fontsize=11, fontweight="bold",
    )
    ax2.legend(fontsize=9)
    ax2.spines[["top", "right"]].set_visible(False)

    fig.suptitle(f"F1 Fantasy {year} — Snake Draft Fairness (5 Players, {draft['picks_per']} picks each)",
                 fontsize=13, fontweight="bold", y=1.02)
    plt.tight_layout()
    plt.savefig(f"analysis/charts/{year}_06_draft_fairness.png", dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    ✓ {year}_06_draft_fairness.png")


# ───────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    os.makedirs("analysis/charts", exist_ok=True)

    print("═" * 62)
    print("  F1 Fantasy Scoring Analysis — 2024 & 2025 Seasons")
    print("═" * 62)

    results_2024 = run_analysis(2024)
    results_2025 = run_analysis(2025)

    print("═" * 62)
    print("  CROSS-YEAR SUMMARY (Alt2 ★ recommended config)")
    print("═" * 62)
    print(f"  {'Year':<6} {'Mid/Top-4':>10} {'Negatives':>11} {'Verdict':>14}  Draft fairness")
    print(f"  {'-'*70}")
    for label, result in [("2024", results_2024), ("2025", results_2025)]:
        _, _, df_alt2, draft = result
        st = balance_stats(df_alt2, "")
        print(f"  {label:<6} {st['mid_pct']:>9.0f}%  {st['negs']:>9}  {verdict(st):>20}  {draft_verdict(draft)}")
    print()
