"""
Jarvis MCP Tool: yta_pipeline_status
======================================
Reads pipeline log and returns a summary of recent activity.
"""

import os
import subprocess

TOOL_NAME = "yta_pipeline_status"
TOOL_DESCRIPTION = "Shows recent YTA pipeline activity and job status summary"

# Log paths — Mac and VPS
LOG_PATHS = [
    os.path.expanduser("~/Library/Logs/yta_pipeline.log"),
    "/var/log/yta_pipeline.log",
]


def run() -> dict:
    """Read the last 50 lines of the pipeline log and summarize."""
    log_path = None
    for p in LOG_PATHS:
        if os.path.exists(p):
            log_path = p
            break

    if not log_path:
        return {
            "success": False,
            "output": "No pipeline log found",
            "log_paths_checked": LOG_PATHS,
        }

    try:
        with open(log_path) as f:
            lines = f.readlines()

        recent = lines[-50:] if len(lines) > 50 else lines
        log_text = "".join(recent)

        # Count key events
        published = sum(1 for l in recent if "PUBLISHED" in l)
        errors = sum(1 for l in recent if "ERROR" in l or "error" in l.lower())
        running = sum(1 for l in recent if "Running" in l or "Starting" in l)

        summary = (
            f"Last {len(recent)} log lines from {log_path}\n"
            f"Published: {published} | Errors: {errors} | Jobs started: {running}\n"
            f"---\n{log_text}"
        )

        return {"success": True, "output": summary}
    except Exception as e:
        return {"success": False, "output": str(e)}


tool_config = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "parameters": {},
    "handler": run,
}

if __name__ == "__main__":
    result = run()
    print(result["output"])
