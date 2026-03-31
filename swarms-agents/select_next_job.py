from utils.supabase_client import get_client

client = get_client()

res = (
    client.table("video_jobs")
    .select("id, title_concept, status")
    .in_("status", ["IDEA", "RESEARCHED"])
    .order("created_at", desc=False)
    .limit(1)
    .execute()
)

if res.data:
    job = res.data[0]
    print("Next job from IDEA/RESEARCHED:", job["id"], job["status"], job["title_concept"])
else:
    # 2) Fallback: pak oudste VOICE_GENERATED job
    res2 = (
        client.table("video_jobs")
        .select("id, title_concept, status")
        .eq("status", "VOICE_GENERATED")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if res2.data:
        job = res2.data[0]
        print("Fallback VOICE_GENERATED job:", job["id"], job["status"], job["title_concept"])
    else:
        print("Geen geschikte jobs gevonden.")
