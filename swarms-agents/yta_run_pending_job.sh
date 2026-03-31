#!/bin/zsh
cd /Users/mikedonker/Desktop/yta-system/yta-swarms/swarms-agents
source .venv/bin/activate
python run_pending_job.py >> ~/Library/Logs/yta_pipeline.log 2>&1
