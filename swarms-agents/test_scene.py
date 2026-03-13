from agents.scene_generator import generate_scenes_for_job

scenes = generate_scenes_for_job('507029f3-d296-4c7e-9320-8e363f85b0a0')
for s in scenes:
    print(f'Scene {s["index"]}: {s["image_prompt"][:80]}...')
