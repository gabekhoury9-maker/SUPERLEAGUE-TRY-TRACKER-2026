from pathlib import Path
import sys
import pandas as pd

from super_league_tracker.core import POSITION_ORDER, bool_series

ROOT = Path(__file__).resolve().parent
matches = pd.read_csv(ROOT / "data" / "matches.csv")
events = pd.read_csv(ROOT / "data" / "try_events.csv")
lineups = pd.read_csv(ROOT / "data" / "lineups.csv")
matches["is_final"] = bool_series(matches["is_final"])
events["is_final"] = bool_series(events["is_final"])

errors = []
m25 = matches[matches.season == 2025]
e25 = events[events.season == 2025]
if len(m25) != 167:
    errors.append(f"Expected 167 2025 matches, found {len(m25)}")
if int((~m25.is_final).sum()) != 162:
    errors.append(f"Expected 162 regular matches, found {int((~m25.is_final).sum())}")
if int(m25.is_final.sum()) != 5:
    errors.append(f"Expected 5 finals, found {int(m25.is_final.sum())}")
if int(e25.tries.sum()) != 1179:
    errors.append(f"Expected 1,179 tries, found {int(e25.tries.sum())}")
invalid = sorted(set(e25.display_position.dropna()) - set(POSITION_ORDER))
if invalid:
    errors.append(f"Invalid positions: {invalid}")
round_counts = m25[~m25.is_final].groupby("round_number").size()
if len(round_counts) != 27 or not (round_counts == 6).all():
    errors.append(f"Regular round counts are not exactly six each: {round_counts.to_dict()}")
if e25.display_position.isna().any() or (e25.display_position.astype(str).str.strip() == "").any():
    errors.append("Blank display position in 2025 try events")


# A populated 2026 match table must also contain scorer-position rows after the
# backfill.  Failing loudly is safer than deploying a misleading all-zero chart.
m26 = matches[matches.season == 2026]
e26 = events[events.season == 2026]
l26 = lineups[lineups.season == 2026]
if len(m26) and e26.empty:
    errors.append(f"2026 has {len(m26)} matches but zero try-event rows; full scorer/position backfill did not run")
if len(m26) and l26.empty:
    errors.append(f"2026 has {len(m26)} matches but zero lineup rows; team-sheet backfill did not run")
if not e26.empty:
    invalid26 = sorted(set(e26.display_position.dropna()) - set(POSITION_ORDER))
    if invalid26:
        errors.append(f"Invalid 2026 positions: {invalid26}")
    if pd.to_numeric(e26.tries, errors="coerce").fillna(0).sum() <= 0:
        errors.append("2026 try-event rows exist but total tries is zero")

# For every try event, exactly one opposition receives that try as conceded.
team_scored = int(e25.tries.sum())
team_conceded = int(e25.groupby("opposition_team").tries.sum().sum())
if team_scored != team_conceded:
    errors.append(f"Scored/conceded imbalance: {team_scored} vs {team_conceded}")

# 13 starters per available team sheet. The known forfeit has no full sheet.
starts = lineups[(lineups.season == 2025) & bool_series(lineups.is_starting)].groupby(["match_id", "team"]).size()
bad_starts = starts[starts != 13]
if len(bad_starts):
    errors.append(f"Team sheets with a non-13 starter count: {bad_starts.to_dict()}")

if errors:
    print("DATA VALIDATION FAILED")
    for error in errors:
        print("-", error)
    sys.exit(1)

print("DATA VALIDATION PASSED")
print(f"2025 matches: {len(m25)} (regular {int((~m25.is_final).sum())}, finals {int(m25.is_final.sum())})")
print(f"2025 tries: {int(e25.tries.sum())}")
print(f"2025 scorer rows: {len(e25)}")
print(f"Position categories: {', '.join(POSITION_ORDER)}")
