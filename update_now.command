#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 -m pip install -r requirements.txt
python3 update_tracker.py --season 2026 --force-player-stats
printf '\nFinished. Press Return to close.\n'
read -r
