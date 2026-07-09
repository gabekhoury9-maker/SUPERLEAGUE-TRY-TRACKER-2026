from __future__ import annotations
from pathlib import Path
import pandas as pd

POSITION_ORDER = ["LW","LC","L2R","FE","HLF","R2R","RC","RW","FB","PR","HK","LK"]


def bool_series(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s
    return s.astype(str).str.strip().str.lower().isin({"true","1","yes","y"})


def load_data(root: Path | str | None = None):
    root = Path(root or Path(__file__).resolve().parents[1])
    matches = pd.read_csv(root / "data" / "matches.csv", parse_dates=["date"])
    events = pd.read_csv(root / "data" / "try_events.csv", parse_dates=["date"])
    matches["is_final"] = bool_series(matches["is_final"])
    events["is_final"] = bool_series(events["is_final"])
    return matches, events


def team_matches(matches, team, season, include_finals=False, mode="Season", opponent=None, round_number=None):
    m = matches[(matches["season"] == int(season)) & ((matches["home_team"] == team) | (matches["away_team"] == team))].copy()
    if not include_finals:
        m = m[~m["is_final"]]
    m = m.sort_values(["date", "match_id"])
    if mode == "L5":
        m = m.tail(5)
    elif mode == "L10":
        m = m.tail(10)
    elif mode == "H2H" and opponent:
        m = m[(m["home_team"] == opponent) | (m["away_team"] == opponent)]
    elif mode == "RD" and round_number is not None:
        m = m[pd.to_numeric(m["round_number"], errors="coerce") == int(round_number)]
    return m


def aggregate_positions(matches, events, team, season, include_finals=False, mode="Season", opponent=None, round_number=None):
    m = team_matches(matches, team, season, include_finals, mode, opponent, round_number)
    mids = set(m["match_id"].astype(int))
    scored = events[(events["match_id"].isin(mids)) & (events["team"] == team)].groupby("display_position")["tries"].sum()
    conceded = events[(events["match_id"].isin(mids)) & (events["opposition_team"] == team)].groupby("display_position")["tries"].sum()
    rows=[]
    for pos in POSITION_ORDER:
        sf, sa = int(scored.get(pos,0)), int(conceded.get(pos,0))
        rows.append({"position":pos,"tries_scored":sf,"tries_conceded":sa,"differential":sf-sa})
    return pd.DataFrame(rows), m


def match_log(matches, events, team):
    rows=[]
    for _,m in matches.sort_values(["date","match_id"],ascending=False).iterrows():
        opponent=m["away_team"] if m["home_team"]==team else m["home_team"]
        score_for=int(m["home_team_score"] if m["home_team"]==team else m["away_team_score"])
        score_against=int(m["away_team_score"] if m["home_team"]==team else m["home_team_score"])
        tries_for=int(events[(events.match_id==m.match_id)&(events.team==team)].tries.sum())
        tries_against=int(events[(events.match_id==m.match_id)&(events.team==opponent)].tries.sum())
        rows.append({"date":m.date.date(),"round":m.round_label,"opponent":opponent,
                     "score":f"{score_for}-{score_against}","tries_for":tries_for,"tries_against":tries_against,
                     "match_id":int(m.match_id)})
    return pd.DataFrame(rows)
