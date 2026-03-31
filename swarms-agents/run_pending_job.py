from utils.supabase_client import get_client
from agents.pipeline import run_pipeline

def main():
    client = get_client()
    res = client.table("video_jobs") \
        .select("id, title_concept, status") \
        .in_("status", ["VOICE_GENERATED", "SCRIPT_APPROVED"]) \
        .order("created_at", desc=False) \
        .limit(1) \
        .execute()

    if not res.data:
        print("Geen pending jobs gevonden.")
        return

    job = res.data[0]
    print(f"[runner] Running: {job['id']} — {job['title_concept']} ({job['status']})")
    result = run_pipeline(job["id"])
    print(f"[runner] Result: {result}")

if __name__ == "__main__":
    main()
