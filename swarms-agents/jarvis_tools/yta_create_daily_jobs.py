"""
Jarvis MCP Tool: yta_create_daily_jobs
=======================================
Creates new daily video jobs in the YTA pipeline queue.
"""

import subprocess
import sys
import os

TOOL_NAME = "yta_create_daily_jobs"
TOOL_DESCRIPTION = "Creates new daily YouTube video jobs in the pipeline queue"

PIPELINE_DIR = os.getenv(
    "YTA_PIPELINE_DIR",
    os.path.expanduser("~/Desktop/yta-system/yta-swarms/swarms-agents"),
)
VENV_PYTHON = os.path.join(PIPELINE_DIR, ".venv", "bin", "python")
SCRIPT = os.path.join(PIPELINE_DIR, "daily_job_creator.py")


def run() -> dict:
    """Execute daily_job_creator.py and return structured result."""
    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable

    try:
        result = subprocess.run(
            [python, SCRIPT],
            cwd=PIPELINE_DIR,
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": PIPELINE_DIR},
        )
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "errors": result.stderr.strip() if result.returncode != 0 else None,
        }
    except Exception as e:
        return {"success": False, "output": "", "errors": str(e)}


tool_config = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {},
    "handler": run,
}

if __name__ == "__main__":
    print(run())
