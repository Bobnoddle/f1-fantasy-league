# F1 Fantasy Scoring Analysis — 2024 Season

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
| P1 (leader) | 718 | 627 | 735 |
| P10 | 52 | 152 | 271 |
| Last place | -125 | -6 | 2 |
| Negatives | 9 | 2 | 0 |
| P1/P10 ratio | 13.81x | 4.12x | 2.71x |
| Mid/Top-4 | 25% | 37% | 50% |
| Back/Top-4 | -9% | 5% | 16% |
| Std dev | 257 | 201 | 213 |
| **Verdict** | ❌  POOR | ❌  POOR | ⚠️  MODERATE |

## Tier Gaps

| Tier | Cur avg | Cur range | Alt avg | Alt range | Alt2 avg | Alt2 range |
|------|---------|-----------|---------|-----------|----------|------------|
| Top 4 | 611 | 519–718 | 550 | 478–627 | 670 | 617–735 |
| P5–10 | 286 | 52–442 | 314 | 152–468 | 437 | 271–589 |
| P11–16 | 22 | -9–42 | 96 | 63–136 | 231 | 193–270 |
| P17+ | -56 | -125–-9 | 29 | -6–54 | 107 | 2–185 |

## Current Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **718** | 471 | 0 | 176 | 66 | 15 | -10 | 1 | 23.9 |
| 2 | Lando Norris | McLaren | **615** | 406 | 0 | 155 | 44 | 30 | -20 | 2 | 20.5 |
| 3 | Charles Leclerc | Ferrari | **592** | 387 | 0 | 110 | 100 | 15 | -20 | 2 | 19.7 |
| 4 | Oscar Piastri | McLaren | **519** | 331 | 0 | 105 | 78 | 5 | 0 | 0 | 17.3 |
| 5 | Carlos Sainz | Ferrari | **442** | 320 | 0 | 99 | 48 | 5 | -30 | 3 | 15.2 |
| 6 | Lewis Hamilton | Mercedes | **440** | 252 | 0 | 60 | 138 | 10 | -20 | 2 | 14.7 |
| 7 | George Russell | Mercedes | **428** | 271 | 0 | 110 | 72 | 10 | -35 | 2 | 14.3 |
| 8 | Sergio Pérez | Red Bull | **253** | 186 | 0 | 64 | 78 | 5 | -80 | 8 | 8.4 |
| 9 | Fernando Alonso | Aston Martin | **98** | 99 | 0 | 45 | 34 | 10 | -90 | 9 | 3.3 |
| 10 | Pierre Gasly | Alpine F1 Team | **52** | 73 | 0 | 27 | 82 | 0 | -130 | 13 | 1.7 |
| 11 | Nico Hülkenberg | Haas F1 Team | **42** | 74 | 0 | 27 | 66 | 0 | -125 | 11 | 1.4 |
| 12 | Kevin Magnussen | Haas F1 Team | **30** | 49 | 0 | 16 | 60 | 5 | -100 | 10 | 1.1 |
| 13 | Oliver Bearman | Haas F1 Team | **27** | 11 | 0 | 2 | 14 | 0 | 0 | 0 | 6.8 |
| 14 | Liam Lawson | RB F1 Team | **25** | 11 | 0 | 8 | 26 | 0 | -20 | 2 | 2.8 |
| 15 | Franco Colapinto | Williams | **14** | 19 | 0 | 3 | 42 | 0 | -50 | 5 | 1.2 |
| 16 | Jack Doohan | Alpine F1 Team | **-9** | 1 | 0 | 0 | 0 | 0 | -10 | 1 | -9.0 |
| 17 | Yuki Tsunoda | RB F1 Team | **-9** | 58 | 0 | 31 | 52 | 0 | -150 | 15 | -0.3 |
| 18 | Lance Stroll | Aston Martin | **-30** | 52 | 0 | 22 | 46 | 0 | -150 | 15 | -1.0 |
| 19 | Daniel Ricciardo | RB F1 Team | **-30** | 36 | 0 | 17 | 32 | 5 | -120 | 12 | -1.4 |
| 20 | Esteban Ocon | Alpine F1 Team | **-33** | 50 | 0 | 18 | 54 | 5 | -160 | 16 | -1.1 |
| 21 | Logan Sargeant | Williams | **-60** | 16 | 0 | 4 | 30 | 0 | -110 | 11 | -3.5 |
| 22 | Alexander Albon | Williams | **-71** | 42 | 0 | 19 | 38 | 0 | -170 | 17 | -2.4 |
| 23 | Guanyu Zhou | Sauber | **-89** | 31 | 0 | 4 | 56 | 0 | -180 | 18 | -3.0 |
| 24 | Valtteri Bottas | Sauber | **-125** | 27 | 0 | 10 | 28 | 0 | -190 | 19 | -4.2 |

## Alt Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **627** | 380 | 0 | 138 | 99 | 15 | -5 | 1 | 20.9 |
| 2 | Charles Leclerc | Ferrari | **557** | 318 | 0 | 84 | 150 | 15 | -10 | 2 | 18.6 |
| 3 | Lando Norris | McLaren | **539** | 329 | 0 | 124 | 66 | 30 | -10 | 2 | 18.0 |
| 4 | Oscar Piastri | McLaren | **478** | 278 | 0 | 78 | 117 | 5 | 0 | 0 | 15.9 |
| 5 | Lewis Hamilton | Mercedes | **468** | 214 | 0 | 47 | 207 | 10 | -10 | 2 | 15.6 |
| 6 | George Russell | Mercedes | **405** | 224 | 0 | 88 | 108 | 10 | -25 | 2 | 13.5 |
| 7 | Carlos Sainz | Ferrari | **401** | 259 | 0 | 80 | 72 | 5 | -15 | 3 | 13.8 |
| 8 | Sergio Pérez | Red Bull | **293** | 161 | 0 | 50 | 117 | 5 | -40 | 8 | 9.8 |
| 9 | Pierre Gasly | Alpine F1 Team | **163** | 81 | 0 | 24 | 123 | 0 | -65 | 13 | 5.4 |
| 10 | Fernando Alonso | Aston Martin | **152** | 100 | 0 | 36 | 51 | 10 | -45 | 9 | 5.1 |
| 11 | Nico Hülkenberg | Haas F1 Team | **136** | 83 | 0 | 24 | 99 | 0 | -70 | 11 | 4.5 |
| 12 | Kevin Magnussen | Haas F1 Team | **117** | 56 | 0 | 16 | 90 | 5 | -50 | 10 | 4.3 |
| 13 | Yuki Tsunoda | RB F1 Team | **97** | 68 | 0 | 26 | 78 | 0 | -75 | 15 | 3.2 |
| 14 | Esteban Ocon | Alpine F1 Team | **87** | 64 | 0 | 17 | 81 | 5 | -80 | 16 | 3.0 |
| 15 | Lance Stroll | Aston Martin | **78** | 65 | 0 | 19 | 69 | 0 | -75 | 15 | 2.6 |
| 16 | Franco Colapinto | Williams | **63** | 22 | 0 | 3 | 63 | 0 | -25 | 5 | 5.2 |
| 17 | Daniel Ricciardo | RB F1 Team | **54** | 45 | 0 | 16 | 48 | 5 | -60 | 12 | 2.6 |
| 18 | Liam Lawson | RB F1 Team | **51** | 15 | 0 | 7 | 39 | 0 | -10 | 2 | 5.7 |
| 19 | Alexander Albon | Williams | **45** | 54 | 0 | 19 | 57 | 0 | -85 | 17 | 1.5 |
| 20 | Guanyu Zhou | Sauber | **43** | 45 | 0 | 4 | 84 | 0 | -90 | 18 | 1.4 |
| 21 | Oliver Bearman | Haas F1 Team | **35** | 12 | 0 | 2 | 21 | 0 | 0 | 0 | 8.8 |
| 22 | Logan Sargeant | Williams | **13** | 19 | 0 | 4 | 45 | 0 | -55 | 11 | 0.8 |
| 23 | Jack Doohan | Alpine F1 Team | **-3** | 2 | 0 | 0 | 0 | 0 | -5 | 1 | -3.0 |
| 24 | Valtteri Bottas | Sauber | **-6** | 37 | 0 | 10 | 42 | 0 | -95 | 19 | -0.2 |

## Alt2 ★ (Recommended) Leaderboard

| Rank | Driver | Team | Total | Finish | Comp | Quali | Delta | FL | Penalties | DNFs | Avg/Race |
|------|--------|------|-------|--------|------|-------|-------|----|-----------|------|----------|
| 1 | Max Verstappen | Red Bull | **735** | 381 | 69 | 138 | 132 | 15 | 0 | 1 | 24.5 |
| 2 | Charles Leclerc | Ferrari | **684** | 319 | 66 | 84 | 200 | 15 | 0 | 2 | 22.8 |
| 3 | Lando Norris | McLaren | **642** | 331 | 69 | 124 | 88 | 30 | 0 | 2 | 21.4 |
| 4 | Lewis Hamilton | Mercedes | **617** | 218 | 66 | 47 | 276 | 10 | 0 | 2 | 20.6 |
| 5 | Oscar Piastri | McLaren | **589** | 278 | 72 | 78 | 156 | 5 | 0 | 0 | 19.6 |
| 6 | George Russell | Mercedes | **516** | 226 | 63 | 88 | 144 | 10 | -15 | 2 | 17.2 |
| 7 | Carlos Sainz | Ferrari | **503** | 262 | 60 | 80 | 96 | 5 | 0 | 3 | 17.3 |
| 8 | Sergio Pérez | Red Bull | **428** | 169 | 48 | 50 | 156 | 5 | 0 | 8 | 14.3 |
| 9 | Pierre Gasly | Alpine F1 Team | **313** | 92 | 33 | 24 | 164 | 0 | 0 | 13 | 10.4 |
| 10 | Nico Hülkenberg | Haas F1 Team | **271** | 91 | 39 | 24 | 132 | 0 | -15 | 11 | 9.0 |
| 11 | Fernando Alonso | Aston Martin | **270** | 108 | 48 | 36 | 68 | 10 | 0 | 9 | 9.0 |
| 12 | Kevin Magnussen | Haas F1 Team | **247** | 70 | 36 | 16 | 120 | 5 | 0 | 10 | 9.1 |
| 13 | Yuki Tsunoda | RB F1 Team | **236** | 79 | 27 | 26 | 104 | 0 | 0 | 15 | 7.9 |
| 14 | Esteban Ocon | Alpine F1 Team | **224** | 73 | 21 | 17 | 108 | 5 | 0 | 16 | 7.7 |
| 15 | Lance Stroll | Aston Martin | **215** | 74 | 30 | 19 | 92 | 0 | 0 | 15 | 7.2 |
| 16 | Guanyu Zhou | Sauber | **193** | 59 | 18 | 4 | 112 | 0 | 0 | 18 | 6.4 |
| 17 | Alexander Albon | Williams | **185** | 69 | 21 | 19 | 76 | 0 | 0 | 17 | 6.2 |
| 18 | Daniel Ricciardo | RB F1 Team | **156** | 53 | 18 | 16 | 64 | 5 | 0 | 12 | 7.4 |
| 19 | Valtteri Bottas | Sauber | **136** | 55 | 15 | 10 | 56 | 0 | 0 | 19 | 4.5 |
| 20 | Franco Colapinto | Williams | **127** | 28 | 12 | 3 | 84 | 0 | 0 | 5 | 10.6 |
| 21 | Logan Sargeant | Williams | **105** | 32 | 9 | 4 | 60 | 0 | 0 | 11 | 6.2 |
| 22 | Liam Lawson | RB F1 Team | **91** | 20 | 12 | 7 | 52 | 0 | 0 | 2 | 10.1 |
| 23 | Oliver Bearman | Haas F1 Team | **52** | 13 | 9 | 2 | 28 | 0 | 0 | 0 | 13.0 |
| 24 | Jack Doohan | Alpine F1 Team | **2** | 2 | 0 | 0 | 0 | 0 | 0 | 1 | 2.0 |

## Rank Shift: Current → Alt2

| Rank | Driver | Team | Current | Alt2 | Change |
|------|--------|------|---------|------|--------|
| 1 | Max Verstappen | Red Bull | 718 | 735 | ▲ 17 |
| 2 | Lando Norris | McLaren | 615 | 642 | ▲ 27 |
| 3 | Charles Leclerc | Ferrari | 592 | 684 | ▲ 92 |
| 4 | Oscar Piastri | McLaren | 519 | 589 | ▲ 70 |
| 5 | Carlos Sainz | Ferrari | 442 | 503 | ▲ 61 |
| 6 | Lewis Hamilton | Mercedes | 440 | 617 | ▲ 177 |
| 7 | George Russell | Mercedes | 428 | 516 | ▲ 88 |
| 8 | Sergio Pérez | Red Bull | 253 | 428 | ▲ 175 |
| 9 | Fernando Alonso | Aston Martin | 98 | 270 | ▲ 172 |
| 10 | Pierre Gasly | Alpine F1 Team | 52 | 313 | ▲ 261 |
| 11 | Nico Hülkenberg | Haas F1 Team | 42 | 271 | ▲ 229 |
| 12 | Kevin Magnussen | Haas F1 Team | 30 | 247 | ▲ 217 |
| 13 | Oliver Bearman | Haas F1 Team | 27 | 52 | ▲ 25 |
| 14 | Liam Lawson | RB F1 Team | 25 | 91 | ▲ 66 |
| 15 | Franco Colapinto | Williams | 14 | 127 | ▲ 113 |
| 16 | Jack Doohan | Alpine F1 Team | -9 | 2 | ▲ 11 |
| 17 | Yuki Tsunoda | RB F1 Team | -9 | 236 | ▲ 245 |
| 18 | Lance Stroll | Aston Martin | -30 | 215 | ▲ 245 |
| 19 | Daniel Ricciardo | RB F1 Team | -30 | 156 | ▲ 186 |
| 20 | Esteban Ocon | Alpine F1 Team | -33 | 224 | ▲ 257 |
| 21 | Logan Sargeant | Williams | -60 | 105 | ▲ 165 |
| 22 | Alexander Albon | Williams | -71 | 185 | ▲ 256 |
| 23 | Guanyu Zhou | Sauber | -89 | 193 | ▲ 282 |
| 24 | Valtteri Bottas | Sauber | -125 | 136 | ▲ 261 |

## DNF Risk Analysis

> DNF penalty: Current=−10 · Alt=−5 · **Alt2=0** (implicit: DNF scores 0 finish + 0 gain)

| Driver | Team | DNFs | Penalties (Cur) | Total Cur | Alt | Alt2 |
|--------|------|------|-----------------|-----------|-----|------|
| Valtteri Bottas | Sauber | 19 | -190 | -125 | -6 | 136 |
| Guanyu Zhou | Sauber | 18 | -180 | -89 | 43 | 193 |
| Alexander Albon | Williams | 17 | -170 | -71 | 45 | 185 |
| Esteban Ocon | Alpine F1 Team | 16 | -160 | -33 | 87 | 224 |
| Yuki Tsunoda | RB F1 Team | 15 | -150 | -9 | 97 | 236 |
| Lance Stroll | Aston Martin | 15 | -150 | -30 | 78 | 215 |
| Pierre Gasly | Alpine F1 Team | 13 | -130 | 52 | 163 | 313 |
| Daniel Ricciardo | RB F1 Team | 12 | -120 | -30 | 54 | 156 |
| Nico Hülkenberg | Haas F1 Team | 11 | -125 | 42 | 136 | 271 |
| Logan Sargeant | Williams | 11 | -110 | -60 | 13 | 105 |
| Kevin Magnussen | Haas F1 Team | 10 | -100 | 30 | 117 | 247 |
| Fernando Alonso | Aston Martin | 9 | -90 | 98 | 152 | 270 |
| Sergio Pérez | Red Bull | 8 | -80 | 253 | 293 | 428 |
| Franco Colapinto | Williams | 5 | -50 | 14 | 63 | 127 |
| Carlos Sainz | Ferrari | 3 | -30 | 442 | 401 | 503 |

## Snake Draft Simulation (5 Players · Alt2 Scoring)

- Picks per player: **4** (`min(10, 24//5)`)
- Total drafted: **20** / 24 drivers
- Simulation: 1,000 drafts with σ=60pt knowledge noise

### Optimal Draft (perfect knowledge)

| Slot | Drivers | Team Total |
|------|---------|------------|
| Pick 1 | Max Verstappen (735), Nico Hülkenberg (271), Fernando Alonso (270), Franco Colapinto (127) | **1403** |
| Pick 2 | Charles Leclerc (684), Pierre Gasly (313), Kevin Magnussen (247), Valtteri Bottas (136) | **1380** |
| Pick 3 | Lando Norris (642), Sergio Pérez (428), Yuki Tsunoda (236), Daniel Ricciardo (156) | **1462** |
| Pick 4 | Lewis Hamilton (617), Carlos Sainz (503), Esteban Ocon (224), Alexander Albon (185) | **1529** |
| Pick 5 | Oscar Piastri (589), George Russell (516), Lance Stroll (215), Guanyu Zhou (193) | **1513** |

Optimal spread: **149 pts** (9.7% of top team)

### Monte Carlo Results

| Slot | Avg Total | Std Dev | vs Mean | % Diff |
|------|-----------|---------|---------|--------|
| Pick 1 | 1356 | ±87 | -88 | -6.1% |
| Pick 2 | 1359 | ±103 | -84 | -5.8% |
| Pick 3 | 1465 | ±112 | +22 | +1.5% |
| Pick 4 | 1517 | ±96 | +73 | +5.1% |
| Pick 5 | 1519 | ±93 | +76 | +5.2% |

**Fairness verdict: ⚠️  MINOR BIAS (6.1% max bias)**

## Charts Generated

- `analysis/charts/2024_01_leaderboard.png` — Full grid leaderboard (Alt2 scoring)
- `analysis/charts/2024_02_stacked_breakdown.png` — Points source breakdown per driver
- `analysis/charts/2024_03_distribution.png` — Points distribution histogram + rank curve
- `analysis/charts/2024_04_consistency.png` — Consistency vs season total scatter
- `analysis/charts/2024_05_comparison.png` — 3-way config comparison (Current / Alt / Alt2)
- `analysis/charts/2024_06_draft_fairness.png` — Snake draft fairness — optimal + Monte Carlo
