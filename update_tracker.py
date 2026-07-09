"""Refresh the Super League Pro Tracker.

The updater is deliberately split into independent source jobs so that a
temporary failure on one website does not erase or block the other datasets.

Jobs:
- completed matches, scores, try scorers and final 1-17s: Rugby League Project
- upcoming fixtures and announced team lists: Rugby League Project match pages
- season player statistics: official Super League player statistics page

The dashboard is regenerated after every run, even when one source is down.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import pandas as pd
from bs4 import BeautifulSoup

import update_data
from super_league_tracker.render import refresh_outputs, short

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASE = "https://www.rugbyleagueproject.org"
RLP_SEASON_RESULTS = "https://www.rugbyleagueproject.org/seasons/super-league-{season}/results.html"
OFFICIAL_PLAYER_STATS = "https://www.superleague.co.uk/stats/player-stats"
UTC_NOW = lambda: datetime.now(timezone.utc).replace(microsecond=0)

FIXTURE_COLUMNS = [
    "match_id", "season", "round_label", "round_number", "date", "kickoff",
    "status", "home_team", "away_team", "venue", "source_url", "last_checked_utc",
]
TEAMLIST_COLUMNS = [
    "match_id", "season", "round_label", "round_number", "date", "kickoff",
    "team", "opposition_team", "player_id", "full_name", "player_key",
    "jersey_number", "raw_position", "display_position", "is_starting",
    "source_url", "last_checked_utc",
]
PLAYER_COLUMNS = [
    "season", "rank", "player", "player_key", "team", "team_method", "games",
    "tries", "try_assists", "tackles", "missed_tackles", "minutes",
    "tackle_busts", "attacking_kicks", "carries", "metres", "average_gain",
    "clean_breaks", "drop_outs", "drop_goals", "errors", "forty_twenty",
    "goals", "missed_goals", "offloads", "penalties", "red_cards",
    "yellow_cards", "source_url", "updated_utc",
]

STAT_CODES = [
    "tries", "try_assists", "tackles", "missed_tackles", "minutes",
    "tackle_busts", "attacking_kicks", "carries", "metres", "average_gain",
    "clean_breaks", "drop_outs", "drop_goals", "errors", "forty_twenty",
    "goals", "missed_goals", "offloads", "penalties", "red_cards", "yellow_cards",
]


def _safe_read_csv(path: Path, columns: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=columns)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def _write_csv(df: pd.DataFrame, path: Path, columns: list[str]) -> None:
    out = df.copy()
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    out[columns].to_csv(path, index=False)


def _status() -> dict:
    path = DATA / "update_status.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_status(status: dict) -> None:
    (DATA / "update_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _as_int(value, default=0) -> int:
    match = re.search(r"-?\d+", str(value or ""))
    return int(match.group()) if match else default


def _clean_date(value: str) -> str:
    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value or "", flags=re.I)
    parsed = pd.to_datetime(value, dayfirst=True, errors="coerce")
    return parsed.date().isoformat() if pd.notna(parsed) else ""


def discover_match_urls(session, season: int) -> list[str]:
    url = RLP_SEASON_RESULTS.format(season=season)
    soup = BeautifulSoup(update_data.fetch(session, url).text, "lxml")
    found: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(url, anchor["href"])
        label = " ".join(anchor.stripped_strings).strip()
        if re.search(r"/matches/\d+/?$", href) and label in {">", "›", "Match URL"}:
            found.append(href)
        elif (f"/seasons/super-league-{season}/round-" in href
              and href.endswith("/summary.html") and label in {">", "›"}):
            found.append(href)
    return list(dict.fromkeys(found))


def _heading_teams(soup: BeautifulSoup) -> tuple[str, str]:
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = " ".join(tag.stripped_strings)
        match = re.match(r"(.+?)\s+(?:vs\.?|v\.?|\d+\s+(?:def\.?|defeated|drew with)\s+)\s*(.+?)(?:\s+\d+)?$", text, re.I)
        if match:
            return update_data.canon_team(match.group(1)), update_data.canon_team(match.group(2))
    text = soup.get_text(" ", strip=True)
    match = re.search(r"Round\s+\d+(?:\s+-\s+Magic WKND)?\s+(.+?)\s+(?:vs\.?|–)\s+(.+?)\s+Match Info", text, re.I)
    if match:
        return update_data.canon_team(match.group(1)), update_data.canon_team(match.group(2))
    raise ValueError("Could not determine fixture teams")




def parse_teamlists_lenient(soup: BeautifulSoup, match_id: int, season: int, round_label: str, round_number: int, date: str, home: str, away: str, url: str) -> list[dict]:
    """Parse any published players without requiring a complete final 17.

    Scheduled pages may expose partial or provisional team information. Completed
    match parsing remains strict in update_data.py; this lenient path is only for
    the weekly-team-list display.
    """
    body = soup.find("tbody", id="match_teams")
    if not body:
        return []
    rows: list[dict] = []
    current = ""
    starts = [0, 0]
    for tr in body.find_all("tr", recursive=False):
        th = tr.find("th")
        if th and th.get_text(" ", strip=True):
            current = th.get_text(" ", strip=True)
        if current == "HC":
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 4:
            continue
        entries = [(0, home, away, tds[0], tds[1]), (1, away, home, tds[3], tds[2])]
        for side, team, opp, name_td, num_td in entries:
            anchor = name_td.find("a", href=True)
            name = name_td.get_text(" ", strip=True)
            if not anchor or not name:
                continue
            raw = current or "B"
            starting = raw != "B" and starts[side] < 13
            pos = ""
            if starting:
                pos = update_data.START_POS[starts[side]]
                starts[side] += 1
            pid_match = re.search(r"/players/(\d+)", anchor.get("href", ""))
            rows.append({
                "match_id": match_id, "season": season, "round_label": round_label,
                "round_number": round_number, "date": date, "team": team,
                "opposition_team": opp, "player_id": pid_match.group(1) if pid_match else "",
                "full_name": name, "player_key": update_data.player_key(name),
                "jersey_number": update_data.as_int(num_td.get_text(), default=0) or "",
                "raw_position": raw, "display_position": pos,
                "is_starting": starting, "source_url": url,
            })
    return rows


def parse_fixture_page(session, url: str, season: int) -> tuple[dict, list[dict]]:
    response = update_data.fetch(session, url)
    final_url = response.url
    soup = BeautifulSoup(response.text, "lxml")
    page_text = soup.get_text(" ", strip=True)
    if str(season) not in page_text or "Super League" not in page_text:
        raise ValueError("Not a Super League season page")

    try:
        home, _, away, _ = update_data.parse_score_header(soup)
    except Exception:
        home, away = _heading_teams(soup)

    round_label, round_number, _ = update_data.parse_round(soup)
    match_id = update_data.parse_match_id(soup, final_url)
    raw_date = update_data.parse_info(soup, "Date")
    date = _clean_date(raw_date)
    kickoff = update_data.parse_info(soup, "Kick Off") or update_data.parse_info(soup, "Kickoff")
    venue = update_data.parse_info(soup, "Venue")
    status_text = update_data.parse_info(soup, "Status")
    if re.search(r"Completed", status_text or page_text, re.I):
        status = "Completed"
    elif re.search(r"Postponed", status_text or page_text, re.I):
        status = "Postponed"
    elif re.search(r"Cancelled", status_text or page_text, re.I):
        status = "Cancelled"
    else:
        status = "Scheduled"

    checked = UTC_NOW().isoformat()
    fixture = {
        "match_id": match_id,
        "season": season,
        "round_label": round_label,
        "round_number": round_number,
        "date": date,
        "kickoff": kickoff,
        "status": status,
        "home_team": home,
        "away_team": away,
        "venue": venue,
        "source_url": final_url,
        "last_checked_utc": checked,
    }

    teamlists: list[dict] = []
    try:
        rows = parse_teamlists_lenient(
            soup, match_id, season, round_label, round_number, date, home, away, final_url
        )
        for row in rows:
            row = {k: v for k, v in row.items() if k != "player_url"}
            row.update({"kickoff": kickoff, "last_checked_utc": checked})
            teamlists.append(row)
    except Exception:
        pass
    return fixture, teamlists


def _target_fixture_urls(urls: list[str], fixtures: pd.DataFrame, lookback_days: int, lookahead_days: int) -> list[str]:
    if fixtures.empty:
        return urls
    today = datetime.now(timezone.utc).date()
    low, high = today - timedelta(days=lookback_days), today + timedelta(days=lookahead_days)
    cached = fixtures.copy()
    cached["parsed_date"] = pd.to_datetime(cached["date"], errors="coerce").dt.date
    window = cached[
        cached["parsed_date"].apply(lambda x: bool(x and low <= x <= high))
        | cached["parsed_date"].isna()
    ]
    selected = set(window["source_url"].dropna().astype(str))
    known = set(cached["source_url"].dropna().astype(str))
    selected.update(u for u in urls if u not in known)
    return [u for u in urls if u in selected]


def sync_fixtures_teamlists(season: int, delay: float = 0.12) -> dict:
    session = update_data.get_session()
    fixtures = _safe_read_csv(DATA / "fixtures.csv", FIXTURE_COLUMNS)
    teamlists = _safe_read_csv(DATA / "teamlists.csv", TEAMLIST_COLUMNS)
    urls = discover_match_urls(session, season)
    targets = _target_fixture_urls(urls, fixtures[fixtures["season"].astype(str).eq(str(season))], 2, 12)
    parsed_fixtures: list[dict] = []
    parsed_teamlists: list[dict] = []
    failures: list[dict] = []

    for url in targets:
        try:
            fixture, rows = parse_fixture_page(session, url, season)
            parsed_fixtures.append(fixture)
            parsed_teamlists.extend(rows)
        except Exception as exc:
            failures.append({"source": "fixture/teamlist", "url": url, "error": str(exc), "checked_utc": UTC_NOW().isoformat()})
        time.sleep(delay)

    if parsed_fixtures:
        incoming = pd.DataFrame(parsed_fixtures)
        fixtures = pd.concat([fixtures, incoming], ignore_index=True)
        fixtures = fixtures.sort_values(["date", "round_number", "match_id"]).drop_duplicates("match_id", keep="last")
        _write_csv(fixtures, DATA / "fixtures.csv", FIXTURE_COLUMNS)

    if parsed_teamlists:
        incoming = pd.DataFrame(parsed_teamlists)
        # Remove the old sheet for any match/team successfully parsed, then insert the new sheet.
        keys = set(zip(incoming["match_id"].astype(str), incoming["team"].astype(str)))
        keep = ~teamlists.apply(lambda r: (str(r["match_id"]), str(r["team"])) in keys, axis=1)
        teamlists = pd.concat([teamlists[keep], incoming], ignore_index=True)
        teamlists = teamlists.sort_values(["date", "match_id", "team", "is_starting"], ascending=[True, True, True, False])
        teamlists = teamlists.drop_duplicates(["match_id", "team", "player_id", "full_name"], keep="last")
        _write_csv(teamlists, DATA / "teamlists.csv", TEAMLIST_COLUMNS)

    pd.DataFrame(failures).to_csv(DATA / "fixture_failures.csv", index=False)
    return {
        "ok": len(parsed_fixtures) > 0 or (len(targets) == 0 and len(urls) > 0),
        "message": f"Checked {len(targets)} of {len(urls)} fixture pages; found {len(parsed_teamlists)} team-list rows.",
        "fixture_pages_checked": len(targets),
        "teamlist_rows_found": len(parsed_teamlists),
        "failures": len(failures),
        "checked_utc": UTC_NOW().isoformat(),
    }


def _player_key(name: str) -> str:
    return update_data.player_key(name)


def _infer_player_team(name: str, season: int, lineups: pd.DataFrame, teamlists: pd.DataFrame) -> tuple[str, str]:
    key = _player_key(name)
    initial_match = re.match(r"\s*([A-Za-z])", str(name))
    initial = initial_match.group(1).lower() if initial_match else ""
    candidates = []
    for frame, priority in ((teamlists, 2), (lineups, 1)):
        if frame.empty or "player_key" not in frame:
            continue
        q = frame[frame["player_key"].astype(str).eq(key)].copy()
        if "season" in q.columns:
            q = q[q["season"].astype(str).eq(str(season))]
        if initial and "full_name" in q.columns:
            exact_initial = q["full_name"].astype(str).str.strip().str[:1].str.lower().eq(initial)
            if exact_initial.any():
                q = q[exact_initial]
        if q.empty:
            continue
        q["date_sort"] = pd.to_datetime(q.get("date"), errors="coerce")
        for _, row in q.iterrows():
            candidates.append((priority, row.get("date_sort"), str(row.get("team", ""))))
    if not candidates:
        return "", "unmatched"
    candidates.sort(key=lambda x: (x[0], pd.Timestamp.min if pd.isna(x[1]) else x[1]), reverse=True)
    teams = pd.Series([x[2] for x in candidates if x[2]])
    if teams.empty:
        return "", "unmatched"
    return teams.mode().iloc[0], "inferred from team sheets"


def _numeric(value):
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if text in {"", "-", "."}:
        return 0
    try:
        number = float(text)
        return int(number) if number.is_integer() else round(number, 2)
    except ValueError:
        return 0


def parse_official_player_stats(html: str, season: int, lineups: pd.DataFrame, teamlists: pd.DataFrame) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []

    # Primary strategy: inspect table rows, using the published stat order.
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"], recursive=False)]
        if len(cells) < 12:
            continue
        rank = _as_int(cells[0], -1)
        if rank < 1:
            continue
        # The player name is normally the second cell, but some page builds put
        # a badge/image cell before it. Find the first early non-numeric cell.
        player_index = next((i for i, value in enumerate(cells[1:5], start=1)
                             if re.search(r"[A-Za-z]", value) and value.upper() not in {"T", "TA", "TK"}), -1)
        if player_index < 0:
            continue
        player = cells[player_index].strip()
        values = cells[player_index + 1:player_index + 1 + len(STAT_CODES)]
        if len(values) < 8:
            continue
        item = {"season": season, "rank": rank, "player": player, "player_key": _player_key(player)}
        for code, value in zip(STAT_CODES, values):
            item[code] = _numeric(value)
        # Games is not displayed as an explicit column. Infer from tries / per-game card is unreliable,
        # so leave zero unless another parsed table includes it.
        item["games"] = 0
        rows.append(item)

    # Fallback: pandas can recover unusual rowspan/thead structures on some page versions.
    if not rows:
        try:
            tables = pd.read_html(StringIO(html))
        except ValueError:
            tables = []
        for table in sorted(tables, key=lambda x: x.shape[0] * x.shape[1], reverse=True):
            if table.shape[1] < 12:
                continue
            for _, raw in table.iterrows():
                vals = [str(x) for x in raw.tolist()]
                rank = _as_int(vals[0], -1)
                if rank < 1:
                    continue
                player = vals[1]
                item = {"season": season, "rank": rank, "player": player, "player_key": _player_key(player), "games": 0}
                for code, value in zip(STAT_CODES, vals[2:]):
                    item[code] = _numeric(value)
                rows.append(item)
            if rows:
                break

    # Final fallback: some builds render the Full Stats grid with nested divs
    # rather than conventional table rows. Parse the normalised text between
    # the Full Stats heading and the stat-key legend.
    if not rows:
        text = " ".join(soup.stripped_strings)
        section = text.split("Full Stats", 1)[-1].split("T: Tries", 1)[0]
        pattern = re.compile(
            r"(?:^|\s)(\d{1,3})\s+(?:Image\s+)?"
            r"([A-Z]\.\s+[A-Za-zÀ-ÿ'’\-]+(?:\s+[A-Za-zÀ-ÿ'’\-]+)?)\s+"
            r"((?:-?\d+(?:\.\d+)?\s+){20}-?\d+(?:\.\d+)?)"
        )
        for match in pattern.finditer(section):
            rank = int(match.group(1))
            player = match.group(2).strip()
            values = match.group(3).split()
            item = {"season": season, "rank": rank, "player": player, "player_key": _player_key(player), "games": 0}
            for code, value in zip(STAT_CODES, values):
                item[code] = _numeric(value)
            rows.append(item)

    if not rows:
        raise ValueError("Could not locate the official Full Stats table")

    out = pd.DataFrame(rows).drop_duplicates(["season", "player_key"], keep="first")
    inferred = out["player"].apply(lambda n: _infer_player_team(n, season, lineups, teamlists))
    out["team"] = [x[0] for x in inferred]
    out["team_method"] = [x[1] for x in inferred]
    appearance_frames = []
    for frame in (lineups, teamlists):
        if frame.empty or "player_key" not in frame or "match_id" not in frame:
            continue
        q = frame.copy()
        if "season" in q.columns:
            q = q[q["season"].astype(str).eq(str(season))]
        appearance_frames.append(q[["player_key", "match_id"]])
    if appearance_frames:
        appearances = pd.concat(appearance_frames, ignore_index=True).drop_duplicates(["player_key", "match_id"]).groupby("player_key").size()
        out["games"] = out["player_key"].map(appearances).fillna(0).astype(int)
    out["source_url"] = OFFICIAL_PLAYER_STATS
    out["updated_utc"] = UTC_NOW().isoformat()
    for col in PLAYER_COLUMNS:
        if col not in out:
            out[col] = 0 if col in STAT_CODES or col in {"rank", "games", "season"} else ""
    return out[PLAYER_COLUMNS].sort_values("rank")


def _stats_fresh(path: Path, max_age_hours: int) -> bool:
    if not path.exists() or path.stat().st_size < 100:
        return False
    age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
    return age < max_age_hours * 3600


def sync_player_stats(season: int, force: bool = False) -> dict:
    path = DATA / "player_stats.csv"
    if not force and _stats_fresh(path, 6):
        return {"ok": True, "message": "Player stats are under six hours old; retained cached data.", "checked_utc": UTC_NOW().isoformat(), "cached": True}
    session = update_data.get_session()
    response = update_data.fetch(session, OFFICIAL_PLAYER_STATS)
    lineups = _safe_read_csv(DATA / "lineups.csv", list(pd.read_csv(DATA / "lineups.csv", nrows=0).columns))
    teamlists = _safe_read_csv(DATA / "teamlists.csv", TEAMLIST_COLUMNS)
    parsed = parse_official_player_stats(response.text, season, lineups, teamlists)
    old = _safe_read_csv(path, PLAYER_COLUMNS)
    old = old[~old["season"].astype(str).eq(str(season))]
    combined = pd.concat([old, parsed], ignore_index=True)
    _write_csv(combined, path, PLAYER_COLUMNS)
    return {"ok": True, "message": f"Loaded {len(parsed)} official player-stat rows.", "rows": len(parsed), "checked_utc": UTC_NOW().isoformat(), "cached": False}


def run_completed_matches(season: int, delay: float, force_full_backfill: bool = False) -> dict:
    before = pd.read_csv(DATA / "matches.csv")
    before_count = int((before["season"].astype(str) == str(season)).sum())
    result = update_data.sync_rlp(season, delay, force_full_backfill) or {}
    after = pd.read_csv(DATA / "matches.csv")
    after_count = int((after["season"].astype(str) == str(season)).sum())
    return {
        "ok": True,
        "message": f"Completed database contains {after_count} matches for {season}; enriched {result.get('enriched', 0)} match pages with scorers and positions this run.",
        "matches": after_count,
        "added": max(0, after_count - before_count),
        "enriched": result.get("enriched", 0),
        "discovered_links": result.get("discovered", 0),
        "failures": result.get("failures", 0),
        "checked_utc": UTC_NOW().isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--delay", type=float, default=0.12)
    parser.add_argument("--force-player-stats", action="store_true")
    parser.add_argument("--force-full-backfill", action="store_true")
    parser.add_argument("--skip-completed", action="store_true")
    parser.add_argument("--skip-fixtures", action="store_true")
    parser.add_argument("--skip-player-stats", action="store_true")
    args = parser.parse_args()

    status = _status()
    status["last_run_utc"] = UTC_NOW().isoformat()
    jobs = [
        ("completed_matches", not args.skip_completed, lambda: run_completed_matches(args.season, args.delay, args.force_full_backfill)),
        ("fixtures_teamlists", not args.skip_fixtures, lambda: sync_fixtures_teamlists(args.season, args.delay)),
        ("player_stats", not args.skip_player_stats, lambda: sync_player_stats(args.season, args.force_player_stats)),
    ]
    for name, enabled, job in jobs:
        if not enabled:
            continue
        try:
            status[name] = job()
        except Exception as exc:
            status[name] = {
                "ok": False,
                "message": str(exc),
                "checked_utc": UTC_NOW().isoformat(),
            }
            print(f"WARNING: {name} failed: {exc}")
        _save_status(status)

    manifest = refresh_outputs(ROOT)
    status["last_render_utc"] = UTC_NOW().isoformat()
    status["manifest"] = manifest
    _save_status(status)
    # Re-render once more so the status embedded in index.html includes this run's final manifest.
    refresh_outputs(ROOT)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
