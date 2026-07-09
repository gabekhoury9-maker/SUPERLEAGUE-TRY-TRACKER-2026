from types import SimpleNamespace

import pandas as pd

from update_tracker import parse_fixture_page, parse_official_player_stats


class FakeSession:
    def __init__(self, html: str, url: str = "https://www.rugbyleagueproject.org/matches/121999"):
        self.response = SimpleNamespace(text=html, url=url)

    def get(self, *args, **kwargs):
        return self.response


def test_scheduled_fixture_and_teamlist_parser(monkeypatch):
    html = """
    <html><body>
      <h1>2026 Betfred Super League</h1><h2>Round 18</h2>
      <h3>York Knights vs. Hull FC</h3>
      <table><tbody id="match_info">
        <tr><th>Status</th><td>Scheduled to be played</td></tr>
        <tr><th>Date</th><td>Thursday, 9th July, 2026</td></tr>
        <tr><th>Kick Off</th><td>8:00pm (local time)</td></tr>
        <tr><th>Venue</th><td>LNER (York)</td></tr>
      </tbody></table>
      <a href="/matches/121999">Match URL</a>
      <table><tbody id="match_teams">
        <tr><th>Starting</th></tr>
        <tr><td><a href="/players/1/summary.html">Home Fullback</a></td><td>1</td><td>1</td><td><a href="/players/2/summary.html">Away Fullback</a></td></tr>
      </tbody></table>
    </body></html>
    """
    monkeypatch.setattr("update_data.fetch", lambda session, url: session.response)
    fixture, rows = parse_fixture_page(FakeSession(html), "https://example.test", 2026)
    assert fixture["match_id"] == 121999
    assert fixture["status"] == "Scheduled"
    assert fixture["home_team"] == "York"
    assert fixture["away_team"] == "Hull FC"
    assert fixture["date"] == "2026-07-09"
    assert len(rows) == 2
    assert {x["display_position"] for x in rows} == {"FB"}


def test_official_player_stats_parser_and_team_inference():
    html = """
    <table><thead><tr><th>#</th><th>Player</th><th>T</th><th>TA</th><th>TK</th><th>MT</th><th>MI</th><th>TB</th><th>AT</th><th>C</th><th>M</th><th>AG</th><th>CB</th><th>DR</th><th>DG</th><th>E</th><th>FT</th><th>G</th><th>MG</th><th>OF</th><th>P</th><th>RC</th><th>YC</th></tr></thead>
    <tbody><tr><td>1</td><td>M. Sivo</td><td>28</td><td>1</td><td>22</td><td>0</td><td>2</td><td>67</td><td>6</td><td>216</td><td>1738</td><td>8.05</td><td>29</td><td>3</td><td>0</td><td>17</td><td>0</td><td>0</td><td>0</td><td>8</td><td>5</td><td>0</td><td>1</td></tr></tbody></table>
    """
    lineups = pd.DataFrame([
        {"season": 2026, "match_id": 1, "date": "2026-02-13", "team": "Leeds", "player_key": "sivo", "full_name": "Maika Sivo"},
        {"season": 2026, "match_id": 2, "date": "2026-02-20", "team": "Leeds", "player_key": "sivo", "full_name": "Maika Sivo"},
    ])
    stats = parse_official_player_stats(html, 2026, lineups, pd.DataFrame())
    row = stats.iloc[0]
    assert row["player"] == "M. Sivo"
    assert row["tries"] == 28
    assert row["metres"] == 1738
    assert row["average_gain"] == 8.05
    assert row["team"] == "Leeds"
    assert row["games"] == 2
