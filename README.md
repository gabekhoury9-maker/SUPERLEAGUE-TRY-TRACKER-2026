# IMPORTANT POSITION-DATA FIX

This build fixes the all-zero **Position attack vs defence** panel. The previous updater was looking at RLP's `data.html` completeness page instead of the season `results.html` match list.

For the first run, choose **Run workflow** and leave **Re-read all completed 2026 match pages for scorers and positions** set to `true`. The workflow will replace score-only snapshot rows with full match scoresheets, team sheets, try scorers and assigned positions. Later scheduled runs only fetch unenriched/new matches.

# Super League Pro Tracker — populated 2026 fix

This build is designed for GitHub Pages and is already populated with a current 2026 snapshot so the dashboard is not blank before the first workflow run.

## Included snapshot

- 118 completed 2026 match scores through 5 July 2026
- current official 2026 Full Stats top 20 player rows
- complete included 2025 historical dataset

The automatic updater enriches the score snapshot with full match IDs, line-ups, try scorers and positional classifications. It replaces matching score-only rows instead of creating duplicates.

## Required GitHub structure

Upload the **contents** of this package to the repository root. The Code page must directly show `.github`, `data`, `assets`, `index.html`, `update_tracker.py` and the other project files.

If GitHub shows only one outer folder, Actions will not discover the workflow and the data will never refresh.

See `START_HERE_GITHUB_WEBSITE.txt` for click-by-click instructions.
