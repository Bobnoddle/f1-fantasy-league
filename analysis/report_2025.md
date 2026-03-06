# F1 Fantasy Scoring Analysis — 2025 Season

_Generated automatically by `analysis/scoring_analysis.py`_

## Scoring Configs Compared

| Config | Race pts top | Gain bonus | DNF penalty | Completion bonus |
|--------|-------------|------------|-------------|-----------------|
| **Current** | P1=25 (F1 standard) | +2/place | −10 | — |
| **Alt** | P1=20 (compressed) | +3/place | −5 | — |
| **Alt2 ★** | P1=20 (compressed) | +4/place | 0 | +3/race finish |

## Balance Summary

| Metric | Current | Alt | Alt2 ★ |
|--------|---------|-----|--------|
| P1 (leader) | 679 | 597 | 703 |
| P10 | 160 | 247 | 408 |
| Last place | -89 | 3 | 66 |
| Negatives | 4 | 0 | 0 |
| P1/P10 ratio | 4.24x | 2.42x | 1.72x |
| Mid/Top-4 | 28% | 48% | 63% |
| Back/Top-4 | -2% | 12% | 28% |
| Std dev | 227 | 168 | 168 |
| **Verdict** | ❌  POOR | ⚠️  MODERATE | ✅  GOOD |

## Tier Gaps

| Tier | Cur avg | Cur range | Alt avg | Alt range | Alt2 avg | Alt2 range |
|------|---------|-----------|---------|-----------|----------|------------|
| Top 4 | 618 | 526–679 | 536 | 461–597 | 642 | 585–703 |
| P5–10 | 258 | 160–395 | 322 | 247–425 | 468 | 408–556 |
| P11–16 | 92 | 68–118 | 192 | 146–237 | 342 | 275–400 |
| P17+ | -9 | -89–60 | 67 | 3–117 | 178 | 66–247 |

## Current Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **679** | 454 | 0 | 160 | 60 | 15 | -10 | 1 | 22.6 |
| 2 | Lando Norris | McLaren | **654** | 460 | 0 | 169 | 40 | 30 | -45 | 3 | 21.8 |
| 3 | Oscar Piastri | McLaren | **613** | 444 | 0 | 162 | 22 | 30 | -45 | 3 | 20.4 |
| 4 | George Russell | Mercedes | **526** | 356 | 0 | 123 | 42 | 15 | -10 | 1 | 17.5 |
| 5 | Charles Leclerc | Ferrari | **395** | 271 | 0 | 100 | 64 | 5 | -45 | 3 | 13.2 |
| 6 | Lewis Hamilton | Ferrari | **366** | 189 | 0 | 57 | 160 | 5 | -45 | 3 | 12.2 |
| 7 | Andrea Kimi Antonelli | Mercedes | **279** | 183 | 0 | 65 | 76 | 15 | -60 | 6 | 9.3 |
| 8 | Oliver Bearman | Haas F1 Team | **184** | 76 | 0 | 20 | 148 | 0 | -60 | 6 | 6.1 |
| 9 | Yuki Tsunoda | Red Bull | **162** | 68 | 0 | 24 | 150 | 0 | -80 | 8 | 5.4 |
| 10 | Alexander Albon | Williams | **160** | 105 | 0 | 24 | 106 | 5 | -80 | 8 | 5.3 |
| 11 | Esteban Ocon | Haas F1 Team | **118** | 67 | 0 | 13 | 128 | 0 | -90 | 9 | 3.9 |
| 12 | Nico Hülkenberg | Sauber | **113** | 77 | 0 | 11 | 140 | 0 | -115 | 10 | 3.8 |
| 13 | Carlos Sainz | Williams | **89** | 94 | 0 | 41 | 54 | 0 | -100 | 10 | 3.0 |
| 14 | Liam Lawson | RB F1 Team | **83** | 69 | 0 | 28 | 76 | 0 | -90 | 9 | 2.8 |
| 15 | Lance Stroll | Aston Martin | **81** | 60 | 0 | 13 | 128 | 0 | -120 | 12 | 2.8 |
| 16 | Isack Hadjar | RB F1 Team | **68** | 85 | 0 | 39 | 54 | 0 | -110 | 11 | 2.3 |
| 17 | Fernando Alonso | Aston Martin | **60** | 87 | 0 | 35 | 28 | 0 | -90 | 9 | 2.0 |
| 18 | Jack Doohan | Alpine F1 Team | **-1** | 6 | 0 | 3 | 20 | 0 | -30 | 3 | -0.1 |
| 19 | Gabriel Bortoleto | Sauber | **-2** | 49 | 0 | 17 | 62 | 0 | -130 | 13 | -0.1 |
| 20 | Pierre Gasly | Alpine F1 Team | **-15** | 50 | 0 | 18 | 62 | 0 | -145 | 13 | -0.5 |
| 21 | Franco Colapinto | Alpine F1 Team | **-89** | 19 | 0 | 6 | 26 | 0 | -140 | 14 | -4.0 |

## Alt Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **597** | 370 | 0 | 127 | 90 | 15 | -5 | 1 | 19.9 |
| 2 | Lando Norris | McLaren | **564** | 371 | 0 | 133 | 60 | 30 | -30 | 3 | 18.8 |
| 3 | Oscar Piastri | McLaren | **521** | 360 | 0 | 128 | 33 | 30 | -30 | 3 | 17.4 |
| 4 | George Russell | Mercedes | **461** | 292 | 0 | 96 | 63 | 15 | -5 | 1 | 15.4 |
| 5 | Lewis Hamilton | Ferrari | **425** | 165 | 0 | 45 | 240 | 5 | -30 | 3 | 14.2 |
| 6 | Charles Leclerc | Ferrari | **378** | 228 | 0 | 79 | 96 | 5 | -30 | 3 | 12.6 |
| 7 | Andrea Kimi Antonelli | Mercedes | **308** | 157 | 0 | 52 | 114 | 15 | -30 | 6 | 10.3 |
| 8 | Oliver Bearman | Haas F1 Team | **293** | 84 | 0 | 17 | 222 | 0 | -30 | 6 | 9.8 |
| 9 | Yuki Tsunoda | Red Bull | **280** | 74 | 0 | 21 | 225 | 0 | -40 | 8 | 9.3 |
| 10 | Alexander Albon | Williams | **247** | 101 | 0 | 22 | 159 | 5 | -40 | 8 | 8.2 |
| 11 | Nico Hülkenberg | Sauber | **237** | 81 | 0 | 11 | 210 | 0 | -65 | 10 | 7.9 |
| 12 | Esteban Ocon | Haas F1 Team | **234** | 76 | 0 | 11 | 192 | 0 | -45 | 9 | 7.8 |
| 13 | Lance Stroll | Aston Martin | **208** | 65 | 0 | 11 | 192 | 0 | -60 | 12 | 7.2 |
| 14 | Liam Lawson | RB F1 Team | **169** | 76 | 0 | 24 | 114 | 0 | -45 | 9 | 5.6 |
| 15 | Carlos Sainz | Williams | **161** | 96 | 0 | 34 | 81 | 0 | -50 | 10 | 5.4 |
| 16 | Isack Hadjar | RB F1 Team | **146** | 89 | 0 | 31 | 81 | 0 | -55 | 11 | 4.9 |
| 17 | Fernando Alonso | Aston Martin | **117** | 90 | 0 | 30 | 42 | 0 | -45 | 9 | 3.9 |
| 18 | Gabriel Bortoleto | Sauber | **101** | 58 | 0 | 15 | 93 | 0 | -65 | 13 | 3.4 |
| 19 | Pierre Gasly | Alpine F1 Team | **86** | 57 | 0 | 16 | 93 | 0 | -80 | 13 | 2.9 |
| 20 | Jack Doohan | Alpine F1 Team | **27** | 9 | 0 | 3 | 30 | 0 | -15 | 3 | 3.4 |
| 21 | Franco Colapinto | Alpine F1 Team | **3** | 28 | 0 | 6 | 39 | 0 | -70 | 14 | 0.1 |

## Alt2 ★ (Recommended) Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **703** | 372 | 69 | 127 | 120 | 15 | 0 | 1 | 23.4 |
| 2 | Lando Norris | McLaren | **665** | 374 | 63 | 133 | 80 | 30 | -15 | 3 | 22.2 |
| 3 | Oscar Piastri | McLaren | **616** | 363 | 66 | 128 | 44 | 30 | -15 | 3 | 20.5 |
| 4 | Lewis Hamilton | Ferrari | **585** | 170 | 60 | 45 | 320 | 5 | -15 | 3 | 19.5 |
| 5 | George Russell | Mercedes | **556** | 292 | 69 | 96 | 84 | 15 | 0 | 1 | 18.5 |
| 6 | Charles Leclerc | Ferrari | **491** | 231 | 63 | 79 | 128 | 5 | -15 | 3 | 16.4 |
| 7 | Oliver Bearman | Haas F1 Team | **460** | 93 | 54 | 17 | 296 | 0 | 0 | 6 | 15.3 |
| 8 | Yuki Tsunoda | Red Bull | **454** | 85 | 48 | 21 | 300 | 0 | 0 | 8 | 15.1 |
| 9 | Andrea Kimi Antonelli | Mercedes | **438** | 165 | 54 | 52 | 152 | 15 | 0 | 6 | 14.6 |
| 10 | Nico Hülkenberg | Sauber | **408** | 93 | 39 | 11 | 280 | 0 | -15 | 10 | 13.6 |
| 11 | Esteban Ocon | Haas F1 Team | **400** | 85 | 48 | 11 | 256 | 0 | 0 | 9 | 13.3 |
| 12 | Alexander Albon | Williams | **397** | 110 | 48 | 22 | 212 | 5 | 0 | 8 | 13.2 |
| 13 | Lance Stroll | Aston Martin | **381** | 78 | 36 | 11 | 256 | 0 | 0 | 12 | 13.1 |
| 14 | Liam Lawson | RB F1 Team | **308** | 87 | 45 | 24 | 152 | 0 | 0 | 9 | 10.3 |
| 15 | Carlos Sainz | Williams | **292** | 105 | 45 | 34 | 108 | 0 | 0 | 10 | 9.7 |
| 16 | Isack Hadjar | RB F1 Team | **275** | 97 | 39 | 31 | 108 | 0 | 0 | 11 | 9.2 |
| 17 | Gabriel Bortoleto | Sauber | **247** | 72 | 36 | 15 | 124 | 0 | 0 | 13 | 8.2 |
| 18 | Fernando Alonso | Aston Martin | **235** | 98 | 51 | 30 | 56 | 0 | 0 | 9 | 7.8 |
| 19 | Pierre Gasly | Alpine F1 Team | **227** | 69 | 33 | 16 | 124 | 0 | -15 | 13 | 7.6 |
| 20 | Franco Colapinto | Alpine F1 Team | **114** | 41 | 15 | 6 | 52 | 0 | 0 | 14 | 5.2 |
| 21 | Jack Doohan | Alpine F1 Team | **66** | 14 | 9 | 3 | 40 | 0 | 0 | 3 | 8.2 |

## Rank Shift: Current → Alt2

| Rank | Driver | Team | Current | Alt2 | Change |
|------|--------|------|---------|------|--------|
| 1 | Max Verstappen | Red Bull | 679 | 703 | ▲ 24 |
| 2 | Lando Norris | McLaren | 654 | 665 | ▲ 11 |
| 3 | Oscar Piastri | McLaren | 613 | 616 | ▲ 3 |
| 4 | George Russell | Mercedes | 526 | 556 | ▲ 30 |
| 5 | Charles Leclerc | Ferrari | 395 | 491 | ▲ 96 |
| 6 | Lewis Hamilton | Ferrari | 366 | 585 | ▲ 219 |
| 7 | Andrea Kimi Antonelli | Mercedes | 279 | 438 | ▲ 159 |
| 8 | Oliver Bearman | Haas F1 Team | 184 | 460 | ▲ 276 |
| 9 | Yuki Tsunoda | Red Bull | 162 | 454 | ▲ 292 |
| 10 | Alexander Albon | Williams | 160 | 397 | ▲ 237 |
| 11 | Esteban Ocon | Haas F1 Team | 118 | 400 | ▲ 282 |
| 12 | Nico Hülkenberg | Sauber | 113 | 408 | ▲ 295 |
| 13 | Carlos Sainz | Williams | 89 | 292 | ▲ 203 |
| 14 | Liam Lawson | RB F1 Team | 83 | 308 | ▲ 225 |
| 15 | Lance Stroll | Aston Martin | 81 | 381 | ▲ 300 |
| 16 | Isack Hadjar | RB F1 Team | 68 | 275 | ▲ 207 |
| 17 | Fernando Alonso | Aston Martin | 60 | 235 | ▲ 175 |
| 18 | Jack Doohan | Alpine F1 Team | -1 | 66 | ▲ 67 |
| 19 | Gabriel Bortoleto | Sauber | -2 | 247 | ▲ 249 |
| 20 | Pierre Gasly | Alpine F1 Team | -15 | 227 | ▲ 242 |
| 21 | Franco Colapinto | Alpine F1 Team | -89 | 114 | ▲ 203 |

## DNF Risk Analysis

> DNF penalty: Current=−10 · Alt=−5 · **Alt2=0** (implicit: DNF scores 0 finish + 0 gain)

| Driver | Team | DNFs | Penalties (Cur) | Total Cur | Alt | Alt2 |
|--------|------|------|-----------------|-----------|-----|------|
| Franco Colapinto | Alpine F1 Team | 14 | -140 | -89 | 3 | 114 |
| Pierre Gasly | Alpine F1 Team | 13 | -145 | -15 | 86 | 227 |
| Gabriel Bortoleto | Sauber | 13 | -130 | -2 | 101 | 247 |
| Lance Stroll | Aston Martin | 12 | -120 | 81 | 208 | 381 |
| Isack Hadjar | RB F1 Team | 11 | -110 | 68 | 146 | 275 |
| Carlos Sainz | Williams | 10 | -100 | 89 | 161 | 292 |
| Nico Hülkenberg | Sauber | 10 | -115 | 113 | 237 | 408 |
| Esteban Ocon | Haas F1 Team | 9 | -90 | 118 | 234 | 400 |
| Liam Lawson | RB F1 Team | 9 | -90 | 83 | 169 | 308 |
| Fernando Alonso | Aston Martin | 9 | -90 | 60 | 117 | 235 |
| Alexander Albon | Williams | 8 | -80 | 160 | 247 | 397 |
| Yuki Tsunoda | Red Bull | 8 | -80 | 162 | 280 | 454 |
| Oliver Bearman | Haas F1 Team | 6 | -60 | 184 | 293 | 460 |
| Andrea Kimi Antonelli | Mercedes | 6 | -60 | 279 | 308 | 438 |
| Oscar Piastri | McLaren | 3 | -45 | 613 | 521 | 616 |
| Jack Doohan | Alpine F1 Team | 3 | -30 | -1 | 27 | 66 |
| Lewis Hamilton | Ferrari | 3 | -45 | 366 | 425 | 585 |
| Charles Leclerc | Ferrari | 3 | -45 | 395 | 378 | 491 |
| Lando Norris | McLaren | 3 | -45 | 654 | 564 | 665 |

## Snake Draft Simulation (5 Players · Alt2 Scoring)

- Picks per player: **4** (`min(10, 21//5)`)
- Total drafted: **20** / 21 drivers
- Simulation: 1,000 drafts with σ=60pt knowledge noise

### Optimal Draft (perfect knowledge)

| Slot | Drivers | Team Total |
|------|---------|------------|
| Pick 1 | Max Verstappen (703), Nico Hülkenberg (408), Esteban Ocon (400), Franco Colapinto (114) | **1625** |
| Pick 2 | Lando Norris (665), Andrea Kimi Antonelli (438), Alexander Albon (397), Pierre Gasly (227) | **1727** |
| Pick 3 | Oscar Piastri (616), Yuki Tsunoda (454), Lance Stroll (381), Fernando Alonso (235) | **1686** |
| Pick 4 | Lewis Hamilton (585), Oliver Bearman (460), Liam Lawson (308), Gabriel Bortoleto (247) | **1600** |
| Pick 5 | George Russell (556), Charles Leclerc (491), Carlos Sainz (292), Isack Hadjar (275) | **1614** |

Optimal spread: **127 pts** (7.4% of top team)

### Monte Carlo Results

| Slot | Avg Total | Std Dev | vs Mean | % Diff |
|------|-----------|---------|---------|--------|
| Pick 1 | 1629 | ±103 | -18 | -1.1% |
| Pick 2 | 1694 | ±104 | +47 | +2.9% |
| Pick 3 | 1676 | ±106 | +29 | +1.8% |
| Pick 4 | 1630 | ±108 | -17 | -1.1% |
| Pick 5 | 1607 | ±107 | -40 | -2.5% |

**Fairness verdict: ✅  FAIR (2.9% max bias)**

## Charts Generated

- `analysis/charts/2025_01_leaderboard.png` — Full grid leaderboard (Alt2 scoring)
- `analysis/charts/2025_02_stacked_breakdown.png` — Points source breakdown per driver
- `analysis/charts/2025_03_distribution.png` — Points distribution histogram + rank curve
- `analysis/charts/2025_04_consistency.png` — Consistency vs season total scatter
- `analysis/charts/2025_05_comparison.png` — 3-way config comparison (Current / Alt / Alt2)
- `analysis/charts/2025_06_draft_fairness.png` — Snake draft fairness — optimal + Monte Carlo
