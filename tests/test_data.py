from pathlib import Path
import pandas as pd

from super_league_tracker.core import POSITION_ORDER, aggregate_positions, bool_series, load_data

ROOT = Path(__file__).resolve().parents[1]


def test_2025_season_totals():
    matches = pd.read_csv(ROOT / "data" / "matches.csv")
    events = pd.read_csv(ROOT / "data" / "try_events.csv")
    matches["is_final"] = bool_series(matches["is_final"])
    m = matches[matches.season == 2025]
    e = events[events.season == 2025]
    assert len(m) == 167
    assert (~m.is_final).sum() == 162
    assert m.is_final.sum() == 5
    assert int(e.tries.sum()) == 1179
    assert len(e) == 978


def test_regular_rounds_are_complete():
    matches = pd.read_csv(ROOT / "data" / "matches.csv")
    matches["is_final"] = bool_series(matches["is_final"])
    counts = matches[(matches.season == 2025) & (~matches.is_final)].groupby("round_number").size()
    assert len(counts) == 27
    assert (counts == 6).all()


def test_positions_are_valid_and_complete():
    events = pd.read_csv(ROOT / "data" / "try_events.csv")
    e = events[events.season == 2025]
    assert not e.display_position.isna().any()
    assert set(e.display_position).issubset(set(POSITION_ORDER))


def test_scored_equals_conceded_leaguewide():
    matches, events = load_data(ROOT)
    teams = sorted(set(matches.loc[matches.season == 2025, "home_team"]) | set(matches.loc[matches.season == 2025, "away_team"]))
    scored = conceded = 0
    for team in teams:
        table, _ = aggregate_positions(matches, events, team, 2025, include_finals=True)
        scored += int(table.tries_scored.sum())
        conceded += int(table.tries_conceded.sum())
    assert scored == conceded == 1179


def test_starting_team_sheets_have_thirteen_players():
    lineups = pd.read_csv(ROOT / "data" / "lineups.csv")
    starts = lineups[(lineups.season == 2025) & bool_series(lineups.is_starting)]
    counts = starts.groupby(["match_id", "team"]).size()
    assert (counts == 13).all()


def test_no_unreviewed_2025_unknowns():
    review = pd.read_csv(ROOT / "data" / "review_queue.csv")
    assert review.empty
