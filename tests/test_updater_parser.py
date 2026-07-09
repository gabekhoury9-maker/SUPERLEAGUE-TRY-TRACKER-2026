from bs4 import BeautifulSoup

from update_data import parse_lineups, parse_scorers


def _team_rows(prefix, side):
    # side 0 is placed in cells 0/1; side 1 in cells 3/2.
    names = [f"{prefix} Player {i}" for i in range(1, 14)]
    rows = []
    for i, name in enumerate(names, 1):
        if side == 0:
            rows.append(f'<tr><td><a href="/players/{1000+i}/summary.html">{name}</a></td><td>{i}</td><td></td><td></td></tr>')
        else:
            rows.append(f'<tr><td></td><td></td><td>{i}</td><td><a href="/players/{2000+i}/summary.html">{name}</a></td></tr>')
    return rows


def test_parse_scorers_and_multiplicity():
    html = """
    <table><tbody id="match_scoresheet">
      <tr><th>T</th></tr>
      <tr><td class="name left"><a href="/players/10/summary.html">Home Wing</a></td><td>2</td><td></td><td class="name"><a href="/players/20/summary.html">Away Centre</a></td></tr>
      <tr><td class="name left"><a href="/players/11/summary.html">Home Hooker</a></td><td></td><td>1</td><td class="name"><a href="/players/21/summary.html">Away Fullback</a></td></tr>
    </tbody></table>
    """
    soup = BeautifulSoup(html, "lxml")
    rows = parse_scorers(soup, "Home", "Away")
    assert [(x["team"], x["full_name"], x["tries"]) for x in rows] == [
        ("Home", "Home Wing", 2),
        ("Away", "Away Centre", 1),
        ("Home", "Home Hooker", 1),
        ("Away", "Away Fullback", 1),
    ]


def test_parse_lineups_assigns_exact_starting_slots():
    left = _team_rows("Home", 0)
    right = _team_rows("Away", 1)
    merged = []
    for l, r in zip(left, right):
        # Merge the four cells from each synthetic row into one two-team row.
        ls = BeautifulSoup(l, "lxml").find("tr").find_all("td", recursive=False)
        rs = BeautifulSoup(r, "lxml").find("tr").find_all("td", recursive=False)
        merged.append(f"<tr>{str(ls[0])}{str(ls[1])}{str(rs[2])}{str(rs[3])}</tr>")
    html = '<table><tbody id="match_teams"><tr><th>Starting</th></tr>' + ''.join(merged) + '</tbody></table>'
    soup = BeautifulSoup(html, "lxml")
    rows = parse_lineups(soup, 99, 2026, "Round 1", 1, "2026-02-12", "Home", "Away", "https://example.test")
    home = [x for x in rows if x["team"] == "Home"]
    away = [x for x in rows if x["team"] == "Away"]
    assert len(home) == len(away) == 13
    assert [x["display_position"] for x in home] == ["FB", "RW", "RC", "LC", "LW", "FE", "HLF", "PR", "HK", "PR", "L2R", "R2R", "LK"]
    assert [x["display_position"] for x in away] == ["FB", "RW", "RC", "LC", "LW", "FE", "HLF", "PR", "HK", "PR", "L2R", "R2R", "LK"]
