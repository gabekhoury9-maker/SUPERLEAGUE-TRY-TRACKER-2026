# Sources and audit trail

## Completed matches, scorers and final team sheets

Rugby League Project season and match pages:

- https://www.rugbyleagueproject.org/seasons/super-league-2026/data.html
- individual match pages linked from the season page

The updater saves the source URL on every match, scorer and team-sheet row.

## Fixtures and weekly team lists

The updater checks scheduled Rugby League Project match pages. If a player table is published, the current table replaces the prior saved table for that match/team. A blank list is shown as awaiting publication, not guessed.

## Official player statistics

Super League player statistics:

- https://www.superleague.co.uk/stats/player-stats

Published totals are stored with their source URL and refresh timestamp. The official table uses abbreviated player names, so the team field is transparently inferred from the latest available team sheets. Unmatched players remain labelled as unmatched.

## Try-map limitation

The available sources identify the try scorer and team-sheet position, but do not provide exact x/y coordinates for every grounding. The dashboard therefore presents a positional/channel map. It must not be interpreted as an exact pitch-coordinate heat map.

## Update cadence

GitHub Actions is scheduled four times per hour. GitHub schedules are best-effort, and a source may publish a completed scoresheet or a team list after the match or announcement itself.
