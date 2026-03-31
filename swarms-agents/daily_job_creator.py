#!/usr/bin/env python3
"""
Daily Job Creator
=================
Maakt automatisch nieuwe video_jobs aan op basis van een topic lijst.
Draait via cron: elke dag om 08:00.
Maakt alleen nieuwe jobs als er minder dan 5 IDEA/RESEARCHED jobs in de queue zitten.
"""
import os
import random
from dotenv import load_dotenv
from utils.supabase_client import get_client

load_dotenv()

CHANNEL_ID = "5400b43e-73ae-428b-b72d-a02e3d986cf1"
MAX_QUEUE_SIZE = 5
JOBS_PER_DAY = 3

TOPIC_POOL = [
    ("How to Invest €500/Month in ETFs (2026 Guide)", ["etf investing", "index funds", "passive investing"]),
    ("Why 90% of People Never Build Wealth (Fix This)", ["wealth building", "personal finance", "money mistakes"]),
    ("The Fastest Way to Pay Off Debt in 2026", ["debt payoff", "debt free", "financial freedom"]),
    ("How to Start Investing With €100", ["investing for beginners", "start investing", "small investments"]),
    ("5 Money Rules That Changed My Life", ["money rules", "personal finance tips", "financial habits"]),
    ("ETFs vs Stocks: What Actually Makes More Money?", ["etf vs stocks", "investing strategy", "stock market"]),
    ("How to Build Passive Income From Zero", ["passive income", "financial independence", "side income"]),
    ("The Truth About Index Funds Nobody Tells You", ["index funds", "passive investing", "vanguard"]),
    ("How Compound Interest Makes You Rich (Math Explained)", ["compound interest", "wealth building", "investing math"]),
    ("Why You Need an Emergency Fund Before Investing", ["emergency fund", "personal finance basics", "saving money"]),
    ("The €1000/Month Investment Plan (Step by Step)", ["investment plan", "monthly investing", "financial planning"]),
    ("How to Retire Early With ETFs (FIRE Strategy)", ["fire movement", "early retirement", "etf strategy"]),
    ("Dividend Investing: Is It Worth It in 2026?", ["dividend investing", "passive income stocks", "dividend stocks"]),
    ("How to Save €10,000 in 12 Months", ["saving money", "savings challenge", "financial goals"]),
    ("The Biggest Investing Mistakes Beginners Make", ["investing mistakes", "beginner investing", "stock market tips"]),
]

def main():
    db = get_client()

    # Check hoeveel jobs al in de queue zitten
    active = db.table("video_jobs").select("id").in_(
        "status", ["IDEA", "RESEARCHED", "SCRIPTED", "SCRIPT_APPROVED", "VOICE_GENERATED"]
    ).execute()

    queue_size = len(active.data)
    print(f"Huidige queue: {queue_size} actieve jobs")

    if queue_size >= MAX_QUEUE_SIZE:
        print(f"Queue vol ({queue_size} >= {MAX_QUEUE_SIZE}), geen nieuwe jobs aangemaakt")
        return

    # Haal bestaande titels op om duplicaten te voorkomen
    existing = db.table("video_jobs").select("title_concept").execute()
    existing_titles = {j["title_concept"].lower() for j in existing.data}

    # Filter al gebruikte topics
    available = [
        t for t in TOPIC_POOL
        if t[0].lower() not in existing_titles
    ]

    if not available:
        print("Alle topics al gebruikt — voeg nieuwe toe aan TOPIC_POOL")
        return

    # Maak nieuwe jobs aan
    to_create = min(JOBS_PER_DAY, MAX_QUEUE_SIZE - queue_size, len(available))
    selected = random.sample(available, to_create)

    for title, keywords in selected:
        result = db.table("video_jobs").insert({
            "channel_id": CHANNEL_ID,
            "status": "IDEA",
            "title_concept": title,
            "keyword_targets": keywords,
        }).execute()
        print(f"Job aangemaakt: {result.data[0]['id'][:8]} — {title}")

    print(f"Klaar: {to_create} nieuwe jobs aangemaakt")

if __name__ == "__main__":
    main()
