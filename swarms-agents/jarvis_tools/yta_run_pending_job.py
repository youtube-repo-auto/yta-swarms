"""
Jarvis MCP Tool: yta_run_pending_job
=====================================
Runs the next pending YouTube video job through the full pipeline.
Returns job status and YouTube URL if available.

Register this tool in your Jarvis config (tools directory or config file).
"""

import subprocess
import sys
import os

TOOL_NAME = "yta_run_pending_job"
TOOL_DESCRIPTION = "Runs the next pending YouTube video job through the full pipeline"

# Path to the pipeline runner — adjust if needed
PIPELINE_DIR = os.getenv(
    "YTA_PIPELINE_DIR",
    os.path.expanduser("~/Desktop/yta-system/yta-swarms/swarms-agents"),
)
VENV_PYTHON = os.path.join(PIPELINE_DIR, ".venv", "bin", "python")
SCRIPT = os.path.join(PIPELINE_DIR, "run_pending_job.py")


def run() -> dict:
    """Execute run_pending_job.py and return structured result."""
    python = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable

    try:
        result = subprocess.run(
            [python, SCRIPT],
            cwd=PIPELINE_DIR,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max
            env={**os.environ, "PYTHONPATH": PIPELINE_DIR},
        )
        output = result.stdout.strip()
        errors = result.stderr.strip()

        return {
            "success": result.returncode == 0,
            "output": output,
            "errors": errors if result.returncode != 0 else None,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "errors": "Pipeline timed out after 30 minutes"}
    except Exception as e:
        return {"success": False, "output": "", "errors": str(e)}


# Jarvis tool interface
tool_config = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {},
    "handler": run,
}

if __name__ == "__main__":
    print(run())
