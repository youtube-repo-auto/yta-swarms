"""
Thumbnail Generator v5 — Iteratie Pipeline
Genereert 3 varianten per job:
  variant 1: Pexels foto + rood/oranje urgentie schema
  variant 2: Pexels foto + marineblauw/goud autoriteit schema
  variant 3: DALL-E 3 (fallback: Pexels + voor/na schema)
Slaat op als variant_1.png, variant_2.png, variant_3.png
Logt in Supabase tabel thumbnail_variants
"""

import logging
import os
import re
import uuid
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from utils.retry import retry_call
from utils.supabase_client import get_client
from utils.supabase_upload import upload_to_bucket

load_dotenv()
logger = logging.getLogger(__name__)

W, H = 1280, 720
PANEL_W = 480
PHOTO_W = W - PANEL_W  # 800
TEXT_MAX_W = 440

PEXELS_KEY = os.getenv("PEXELS_API_KEY")
FONT_PATH = Path(__file__).parent.parent / "assets" / "fonts" / "BebasNeue-Regular.ttf"
_FALLBACK_FONTS = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

TOPIC_MAP = {
    "emergency fund": "glass jar coins cash savings",
    "6-month": "glass jar coins cash savings",
    "invest": "stock chart growth laptop money",
    "etf": "investment portfolio growth chart",
    "index fund": "stock market bull chart",
    "budget": "notebook calculator pen desk money",
    "50/30/20": "budget notebook calculator receipts",
    "passive income": "laptop coffee money desk earning",
    "saving": "piggy bank coins jar money",
    "sparen": "coins jar piggy bank euro",
    "debt": "credit card wallet bills money",
    "retire": "happy couple sunset freedom wealth",
    "crypto": "bitcoin phone cryptocurrency digital",
    "vastgoed": "modern house keys real estate",
    "beleggen": "investment chart euro growth",
    "snowball": "coins stacking growing wealth",
    "compound": "calculator growth chart wealth",
    "income": "laptop money desk earning work",
    "wealth": "luxury success wealth money",
    "money": "dollar bills coins wallet cash",
    "stock": "stock market chart trading",
    "financial": "financial planning calculator notebook",
    "freedom": "sunset freedom happy success",
}


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

def _font(size: int) -> ImageFont.FreeTypeFont:
    if FONT_PATH.exists():
        try:
            return ImageFont.truetype(str(FONT_PATH), size)
        except Exception:
            pass
    for p in _FALLBACK_FONTS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _auto_font(text: str, max_width: int, start_size: int = 96, min_size: int = 28) -> ImageFont.FreeTypeFont:
    """Bepaal de grootste fontgrootte zodat text op 1 regel past binnen max_width."""
    tmp = Image.new("RGB", (1, 1))
    draw = ImageDraw.Draw(tmp)
    for size in range(start_size, min_size - 1, -2):
        fnt = _font(size)
        bbox = draw.textbbox((0, 0), text, font=fnt)
        if (bbox[2] - bbox[0]) <= max_width:
            return fnt
    return _font(min_size)


# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------

def _pexels_search(queries: list[str]) -> Image.Image | None:
    if not PEXELS_KEY:
        logger.warning("PEXELS_API_KEY niet geconfigureerd")
        return None
    for q in queries:
        try:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_KEY},
                params={"query": q, "per_page": 10, "orientation": "landscape"},
                timeout=15,
            )
            photos = r.json().get("photos", [])
            if photos:
                best = max(photos[:5], key=lambda p: p["width"] / max(p["height"], 1))
                resp = requests.get(best["src"]["large2x"], timeout=30)
                logger.info("Pexels foto voor: %s", q)
                return Image.open(BytesIO(resp.content)).convert("RGB")
        except Exception as e:
            logger.warning("Pexels fout voor '%s': %s", q, e)
    return None


# ---------------------------------------------------------------------------
# DALL-E 3
# ---------------------------------------------------------------------------

def _dalle_generate(prompt: str) -> Image.Image | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY niet geconfigureerd")
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        logger.info("DALL-E 3 generatie...")
        response = retry_call(
            client.images.generate,
            model="dall-e-3", prompt=prompt,
            size="1792x1024", quality="hd", n=1,
            max_attempts=2, base_delay=5.0,
        )
        img_resp = requests.get(response.data[0].url, timeout=60)
        logger.info("DALL-E 3 OK")
        return Image.open(BytesIO(img_resp.content)).convert("RGB")
    except Exception as e:
        logger.error("DALL-E 3 mislukt: %s", e)
        return None


# ---------------------------------------------------------------------------
# Foto crop — exact target_w x target_h, geen zwarte balken
# ---------------------------------------------------------------------------

def _crop_photo(photo: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Resize + center-crop naar exact target_w x target_h. Nooit zwarte balken."""
    src_ratio = photo.width / photo.height
    tgt_ratio = target_w / target_h

    # Scale zodat de kleinste dimensie exact past (cover, niet fit)
    if src_ratio > tgt_ratio:
        # Foto is breder dan nodig → scale op hoogte, crop breedte
        new_h = target_h
        new_w = round(photo.width * (target_h / photo.height))
    else:
        # Foto is hoger dan nodig → scale op breedte, crop hoogte
        new_w = target_w
        new_h = round(photo.height * (target_w / photo.width))

    photo = photo.resize((new_w, new_h), Image.LANCZOS)

    # Center crop
    cx = (new_w - target_w) // 2
    cy = (new_h - target_h) // 2
    photo = photo.crop((cx, cy, cx + target_w, cy + target_h))

    # Enhance
    photo = ImageEnhance.Color(photo).enhance(1.4)
    photo = ImageEnhance.Contrast(photo).enhance(1.2)
    photo = ImageEnhance.Brightness(photo).enhance(1.05)
    return photo


# ---------------------------------------------------------------------------
# Helpers: tekst, gradient, blend
# ---------------------------------------------------------------------------

def _draw_text(draw: ImageDraw.Draw, xy: tuple, text: str,
               fnt: ImageFont.FreeTypeFont, fill: tuple,
               stroke_width: int = 3) -> int:
    """Teken tekst met stroke + drop shadow. Returns y na tekst."""
    x, y = xy
    draw.text((x + 4, y + 4), text, font=fnt, fill=(0, 0, 0, 153))
    draw.text((x, y), text, font=fnt, fill=fill,
              stroke_width=stroke_width, stroke_fill=(0, 0, 0))
    bbox = draw.textbbox((0, 0), text, font=fnt)
    return y + (bbox[3] - bbox[1])


def _draw_gradient(canvas: Image.Image, x1: int, y1: int, x2: int, y2: int,
                   color1: tuple, color2: tuple) -> None:
    draw = ImageDraw.Draw(canvas)
    for y in range(y1, y2):
        t = (y - y1) / max(y2 - y1, 1)
        c = tuple(int(color1[i] + (color2[i] - color1[i]) * t) for i in range(3))
        draw.line([(x1, y), (x2, y)], fill=c)


def _blend_edge(canvas: Image.Image, edge_x: int, panel_color: tuple,
                width: int = 100) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    for i in range(width):
        alpha = int(220 * (1 - i / width))
        x = edge_x - 10 + i
        if 0 <= x < W:
            draw.line([(x, 0), (x, H)], fill=(*panel_color, alpha))


# ---------------------------------------------------------------------------
# Variant composities
# ---------------------------------------------------------------------------

def _variant_urgency(photo: Image.Image, title_lines: dict) -> Image.Image:
    """Variant 1: Rood/oranje urgentie schema."""
    canvas = Image.new("RGB", (W, H), (26, 0, 0))
    _draw_gradient(canvas, 0, 0, PANEL_W, H, (26, 0, 0), (139, 0, 0))

    photo = _crop_photo(photo, PHOTO_W, H)
    canvas.paste(photo, (PANEL_W, 0))
    _blend_edge(canvas, PANEL_W, (139, 0, 0))

    # Badge cirkel
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    bcx, bcy, br = 1140, 80, 80
    od.ellipse([(bcx - br, bcy - br), (bcx + br, bcy + br)], fill=(255, 215, 0, 255))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(canvas, "RGBA")

    # Badge tekst
    f_b1 = _auto_font("6", 80, start_size=48)
    f_b2 = _auto_font("MAANDEN", 120, start_size=30)
    bb1 = draw.textbbox((0, 0), "6", font=f_b1)
    draw.text((bcx - (bb1[2] - bb1[0]) // 2, bcy - 32), "6", font=f_b1, fill=(0, 0, 0))
    bb2 = draw.textbbox((0, 0), "MAANDEN", font=f_b2)
    draw.text((bcx - (bb2[2] - bb2[0]) // 2, bcy + 10), "MAANDEN", font=f_b2, fill=(0, 0, 0))

    x = 30

    # Gouden separator
    draw.rectangle([(x, 160), (x + TEXT_MAX_W, 164)], fill=(255, 215, 0))

    # Hoofdtekst
    fnt_main = _auto_font(title_lines["main"], TEXT_MAX_W, start_size=96)
    y = _draw_text(draw, (x, 180), title_lines["main"], fnt_main, (255, 69, 0))

    # Subtekst
    fnt_sub = _auto_font(title_lines["sub"], TEXT_MAX_W, start_size=64)
    y = _draw_text(draw, (x, y + 16), title_lines["sub"], fnt_sub, (255, 255, 255))

    # Onderaan
    fnt_bottom = _auto_font(title_lines["bottom"], TEXT_MAX_W, start_size=44)
    _draw_text(draw, (x, 580), title_lines["bottom"], fnt_bottom, (255, 215, 0))

    # Accent lijn links
    draw.rectangle([(0, 0), (6, H)], fill=(255, 69, 0))

    return canvas


def _variant_authority(photo: Image.Image, title_lines: dict) -> Image.Image:
    """Variant 2: Marineblauw/goud autoriteit schema."""
    canvas = Image.new("RGB", (W, H), (10, 22, 40))
    _draw_gradient(canvas, 0, 0, PANEL_W, H, (10, 22, 40), (27, 58, 107))

    photo = _crop_photo(photo, PHOTO_W, H)
    canvas.paste(photo, (PANEL_W, 0))
    _blend_edge(canvas, PANEL_W, (27, 58, 107))

    draw = ImageDraw.Draw(canvas, "RGBA")
    x = 30

    # Gouden borderline links
    draw.rectangle([(0, 0), (8, H)], fill=(201, 168, 76))

    # Gouden badge
    fnt_badge = _auto_font(title_lines.get("badge", "€10.000"), 240, start_size=56)
    bb = draw.textbbox((0, 0), title_lines.get("badge", "€10.000"), font=fnt_badge)
    badge_w = bb[2] - bb[0] + 40
    draw.rectangle([(x, 60), (x + badge_w, 130)], fill=(201, 168, 76))
    _draw_text(draw, (x + 20, 65), title_lines.get("badge", "€10.000"), fnt_badge, (255, 255, 255), stroke_width=2)

    # Gouden separator
    draw.rectangle([(x, 195), (x + TEXT_MAX_W, 199)], fill=(201, 168, 76))

    # Hoofdtekst
    fnt_main = _auto_font(title_lines["main"], TEXT_MAX_W, start_size=96)
    y = _draw_text(draw, (x, 220), title_lines["main"], fnt_main, (201, 168, 76))

    # Subtekst
    fnt_sub = _auto_font(title_lines["sub"], TEXT_MAX_W, start_size=58)
    y = _draw_text(draw, (x, y + 16), title_lines["sub"], fnt_sub, (255, 255, 255))

    # Checkmarks
    checks = title_lines.get("checks", ["✓ Begin vandaag", "✓ €0 nodig", "✓ Stap voor stap"])
    for i, txt in enumerate(checks):
        fnt_c = _auto_font(txt, TEXT_MAX_W, start_size=32)
        cy = 430 + i * 42
        draw.text((x + 4, cy + 2), txt, font=fnt_c, fill=(0, 0, 0))
        draw.text((x + 2, cy), txt, font=fnt_c, fill=(255, 255, 255))

    return canvas


def _variant_transformation(photo: Image.Image, title_lines: dict) -> Image.Image:
    """Variant 3: Voor/na transformatie (DALL-E of Pexels fallback)."""
    # Als DALL-E beeld: cover crop naar exact 1280x720
    if photo.width > 1400:
        canvas = _crop_photo(photo, W, H)
        draw = ImageDraw.Draw(canvas, "RGBA")

        # "VOOR" linksboven
        fnt_voor = _font(72)
        draw.text((60, 50), "VOOR", font=fnt_voor, fill=(255, 255, 255),
                  stroke_width=5, stroke_fill=(0, 0, 0))

        # "NA" rechtsboven
        fnt_na = _font(72)
        draw.text((1150, 50), "NA", font=fnt_na, fill=(255, 255, 255),
                  stroke_width=5, stroke_fill=(0, 0, 0))

        # "NOODFONDS OPBOUWEN" centraal onderaan
        main_text = title_lines["main"] + " " + title_lines.get("sub2", "OPBOUWEN")
        fnt_main = _font(120)
        bb = draw.textbbox((0, 0), main_text, font=fnt_main)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        tx = (W - tw) // 2
        ty = 620 - th
        draw.text((tx, ty), main_text, font=fnt_main, fill=(255, 255, 255),
                  stroke_width=5, stroke_fill=(0, 0, 0))

        return canvas

    # Pexels fallback: paneel links + foto rechts
    canvas = Image.new("RGB", (W, H), (10, 22, 40))
    _draw_gradient(canvas, 0, 0, PANEL_W, H, (10, 22, 40), (27, 58, 107))

    photo = _crop_photo(photo, PHOTO_W, H)
    canvas.paste(photo, (PANEL_W, 0))
    _blend_edge(canvas, PANEL_W, (27, 58, 107))

    draw = ImageDraw.Draw(canvas, "RGBA")
    x = 30
    draw.rectangle([(0, 0), (6, H)], fill=(255, 215, 0))

    fnt_voor = _auto_font("VOOR", TEXT_MAX_W, start_size=52)
    _draw_text(draw, (x, 100), "VOOR", fnt_voor, (200, 200, 200))

    fnt_na = _auto_font("→ NA", TEXT_MAX_W, start_size=52)
    _draw_text(draw, (x, 170), "→ NA", fnt_na, (255, 215, 0))

    fnt_main = _auto_font(title_lines["main"], TEXT_MAX_W, start_size=96)
    y = _draw_text(draw, (x, 280), title_lines["main"], fnt_main, (255, 215, 0))

    fnt_sub = _auto_font(title_lines.get("sub2", "OPBOUWEN"), TEXT_MAX_W, start_size=80)
    _draw_text(draw, (x, y + 16), title_lines.get("sub2", "OPBOUWEN"), fnt_sub, (255, 255, 255))

    fnt_bot = _auto_font(title_lines["bottom"], TEXT_MAX_W, start_size=38)
    _draw_text(draw, (x, 580), title_lines["bottom"], fnt_bot, (200, 200, 200))

    return canvas


# ---------------------------------------------------------------------------
# Pexels queries per variant
# ---------------------------------------------------------------------------

VARIANT_QUERIES = {
    1: [
        "stressed person empty wallet money",
        "worried person bills financial stress",
        "person counting cash coins savings jar",
    ],
    2: [
        "professional person laptop financial planning",
        "person smiling money savings success",
        "piggy bank coins savings growth",
    ],
    3: [
        "person holding money cash success smile",
        "money success wealth growth",
    ],
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_thumbnail_for_job(job_id: str) -> str:
    """
    Genereer 3 thumbnail varianten voor een job.
    Returns de URL van variant 1 (primaire thumbnail).
    """
    supabase = get_client()
    res = supabase.table("video_jobs").select("title_concept").eq("id", job_id).single().execute()
    job = res.data
    if not job:
        raise ValueError(f"Job {job_id} niet gevonden")

    title = job.get("title_concept", "Finance Tips")
    logger.info("Thumbnail iteratie voor: %s", title[:60])

    # Title lines (customize per titel als nodig)
    title_lines = {
        "main": "NOODFONDS",
        "sub": "STAP VOOR STAP",
        "sub2": "OPBOUWEN",
        "bottom": "Begin met €0",
        "badge": "€10.000",
        "checks": ["✓ Begin vandaag", "✓ €0 nodig", "✓ Stap voor stap"],
    }

    # Parse titel voor dynamische content
    t = title.lower()
    if "emergency" in t or "noodfonds" in t:
        title_lines["main"] = "NOODFONDS"
    elif "etf" in t:
        title_lines["main"] = "ETF GIDS"
    elif "invest" in t or "beleg" in t:
        title_lines["main"] = "INVESTEREN"
    elif "budget" in t or "50/30/20" in t:
        title_lines["main"] = "BUDGET PLAN"
    elif "passive" in t or "passief" in t:
        title_lines["main"] = "PASSIEF INKOMEN"
    else:
        words = re.sub(r"[^\w\s]", "", title).split()
        title_lines["main"] = " ".join(words[:2]).upper()

    out_dir = Path(__file__).parent.parent / "assets" / "thumbnails" / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    urls = {}
    fallback_photo = Image.new("RGB", (PHOTO_W, H), (40, 40, 40))

    # --- Variant 1: Urgentie ---
    logger.info("Variant 1: Urgentie (rood/oranje)")
    photo1 = _pexels_search(VARIANT_QUERIES[1])
    if photo1 is None:
        photo1 = _pexels_search(["money coins cash savings jar"])
    if photo1 is None:
        photo1 = fallback_photo
    img1 = _variant_urgency(photo1, title_lines)
    urls[1] = _save_and_upload(supabase, job_id, img1, 1, "pexels", "red_urgency", out_dir)

    # --- Variant 2: Autoriteit ---
    logger.info("Variant 2: Autoriteit (blauw/goud)")
    photo2 = _pexels_search(VARIANT_QUERIES[2])
    if photo2 is None:
        photo2 = _pexels_search(["investment success growth money"])
    if photo2 is None:
        photo2 = fallback_photo
    img2 = _variant_authority(photo2, title_lines)
    urls[2] = _save_and_upload(supabase, job_id, img2, 2, "pexels", "blue_authority", out_dir)

    # --- Variant 3: DALL-E 3 / fallback ---
    logger.info("Variant 3: Transformatie (DALL-E 3)")
    dalle_prompt = (
        f"YouTube thumbnail, photorealistic, split-screen composition: "
        f"LEFT side shows stressed Dutch person (25-35 years, worried expression, "
        f"empty wallet, dim lighting, desaturated colors, slight blue tint) with "
        f"bold white text overlay VOOR at top left. RIGHT side shows same person "
        f"transformed (confident smile, holding cash/coins, bright warm golden "
        f"lighting, vibrant colors) with bold white text overlay NA at top right. "
        f"Center dividing line is a bright yellow lightning bolt. Bottom center: "
        f"large bold white text '{title_lines['main']}' with thick black outline. "
        f"Style: high contrast, cinematic lighting, ultra sharp, 16:9 ratio, "
        f"professional YouTube thumbnail aesthetic, no watermarks, no logos."
    )
    dalle_img = _dalle_generate(dalle_prompt)
    source3 = "dalle3"
    if dalle_img is not None:
        photo3 = dalle_img
    else:
        logger.warning("DALL-E fallback: Pexels")
        photo3 = _pexels_search(VARIANT_QUERIES[3])
        if photo3 is None:
            photo3 = fallback_photo
        source3 = "pexels_fallback"
    img3 = _variant_transformation(photo3, title_lines)
    urls[3] = _save_and_upload(supabase, job_id, img3, 3, source3, "transformation", out_dir)

    # Primaire thumbnail = variant 1
    supabase.table("video_jobs").update({"thumbnail_url": urls[1]}).eq("id", job_id).execute()
    logger.info("Primaire thumbnail gezet op variant 1")

    return urls[1]


def _save_and_upload(supabase, job_id: str, img: Image.Image, variant_nr: int,
                     source: str, scheme_name: str, out_dir: Path) -> str:
    """Sla lokaal op, upload naar Supabase, log in thumbnail_variants."""
    local_path = out_dir / f"variant_{variant_nr}.png"
    img.save(str(local_path), "PNG", optimize=True)
    size_kb = local_path.stat().st_size // 1024
    logger.info("Variant %d: %d KB → %s", variant_nr, size_kb, local_path)

    storage_path = f"{job_id}/variant_{variant_nr}.png"
    public_url = upload_to_bucket(
        supabase=supabase,
        bucket="thumbnails",
        local_path=str(local_path),
        storage_path=storage_path,
        content_type="image/png",
    )
    logger.info("Variant %d geüpload: %s", variant_nr, public_url)

    try:
        supabase.table("thumbnail_variants").upsert(
            {
                "job_id": job_id,
                "variant_nr": variant_nr,
                "url": public_url,
                "source": source,
                "scheme_name": scheme_name,
                "is_active": variant_nr == 1,
            },
            on_conflict="job_id,variant_nr",
        ).execute()
        logger.info("Variant %d gelogd in thumbnail_variants", variant_nr)
    except Exception as e:
        logger.warning("thumbnail_variants log mislukt: %s", e)

    return public_url
