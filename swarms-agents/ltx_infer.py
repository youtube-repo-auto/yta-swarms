"""
LTX-Video batch inference helper for yta-swarms pipeline.
Called as subprocess by agents/video_generation.py.

Usage:
    python ltx_infer.py --scenes_json /tmp/scenes.json

scenes_json format:
    [
        {"index": 1, "prompt": "...", "output_path": "/tmp/clip_001.mp4"},
        ...
    ]

Loads the model once and generates all clips sequentially.
Retries each clip up to MAX_RETRIES times on failure.
Exits with code 1 if any clip fails all retries.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import torch
from diffusers import LTXPipeline
from diffusers.utils import export_to_video

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Full diffusers pipeline on HuggingFace (text encoder + transformer + VAE).
# Downloaded and cached on first run; local after that.
HF_PIPELINE = "Lightricks/LTX-Video-0.9.1"

NEGATIVE_PROMPT = "worst quality, inconsistent motion, blurry, jittery, distorted"

# 97 frames @ 24 fps ≈ 4 seconds. LTX-Video requires num_frames = 8k + 1.
NUM_FRAMES = 97
WIDTH = 768
HEIGHT = 448   # must be divisible by 32 (768/32=24 ✓, 448/32=14 ✓)
FPS = 24
INFERENCE_STEPS = 20
MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_pipeline() -> LTXPipeline:
    # from_pretrained downloads all components (T5 text encoder, transformer,
    # VAE, tokenizer, scheduler) on first run and caches them in ~/.cache/huggingface.
    print(f"[ltx_infer] Pipeline laden van {HF_PIPELINE} …", flush=True)
    pipe = LTXPipeline.from_pretrained(
        HF_PIPELINE,
        torch_dtype=torch.bfloat16,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    pipe.to(device)
    print(f"[ltx_infer] Model klaar op {device}", flush=True)
    return pipe


# ---------------------------------------------------------------------------
# Per-clip generation
# ---------------------------------------------------------------------------

def generate_clip(pipe: LTXPipeline, prompt: str, output_path: str, seed: int) -> None:
    device = pipe.device
    generator = torch.Generator(device=device).manual_seed(seed)

    output = pipe(
        prompt=prompt,
        negative_prompt=NEGATIVE_PROMPT,
        width=WIDTH,
        height=HEIGHT,
        num_frames=NUM_FRAMES,
        num_inference_steps=INFERENCE_STEPS,
        generator=generator,
    )

    frames = output.frames[0]
    export_to_video(frames, output_path, fps=FPS)

    size_kb = Path(output_path).stat().st_size // 1024
    print(f"[ltx_infer] ✓ Clip opgeslagen → {output_path} ({size_kb} KB)", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="LTX-Video batch clip generator")
    parser.add_argument("--scenes_json", required=True, help="Path to scenes JSON file")
    args = parser.parse_args()

    with open(args.scenes_json, encoding="utf-8") as f:
        scenes = json.load(f)

    pipe = load_pipeline()

    failed: list[int] = []

    for scene in scenes:
        idx = scene["index"]
        prompt = scene["prompt"]
        output_path = scene["output_path"]
        # Deterministic but varied seed per scene
        seed = idx * 31 + 7

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print(
                    f"[ltx_infer] Scene {idx} (poging {attempt}/{MAX_RETRIES}): "
                    f"{prompt[:100]}…",
                    flush=True,
                )
                generate_clip(pipe, prompt, output_path, seed=seed)
                success = True
                break
            except Exception as exc:
                print(
                    f"[ltx_infer] Scene {idx} poging {attempt} mislukt: {exc}",
                    flush=True,
                )
                if attempt < MAX_RETRIES:
                    time.sleep(3)

        if not success:
            failed.append(idx)
            print(f"[ltx_infer] FOUT: Scene {idx} mislukt na {MAX_RETRIES} pogingen", flush=True)

    if failed:
        print(f"[ltx_infer] Mislukte scenes: {failed}", flush=True)
        sys.exit(1)

    print(f"[ltx_infer] ✓ Alle {len(scenes)} clips gegenereerd", flush=True)


if __name__ == "__main__":
    main()
