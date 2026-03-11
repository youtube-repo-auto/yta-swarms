# CLAUDE.md — yta-swarms Code Refactor

## Project Overview
YouTube automation pipeline (Python 3.14) using AI agents (Swarms framework) for generating video ideas, research, scripts, voice, video, and publishing. Database: Supabase. Currently uses OpenAI GPT-4o/GPT-4o-mini; **migrating to Anthropic Claude** for cost reduction.

## IMPORTANT Refactoring Task

**YOU MUST follow these rules exactly:**

### Refactoring Goal
Replace ALL OpenAI SDK calls with Anthropic SDK calls across the entire `swarms-agents/` codebase.

### Files to Modify
1. `agents/research.py` — if using OpenAI
2. `agents/scene_generator.py` — currently GPT-4o
3. `agents/thumbnail.py` — if exists, currently GPT-4o
4. `agents/seo_optimization.py` — if exists, currently GPT-4o
5. `agents/voice_generation.py` — if exists, currently GPT-4o-mini
6. `agents/publishing.py` — if exists, currently GPT-4o-mini
7. `utils/llm_factory.py` — central LLM config
8. `requirements.txt` — dependencies

### Model Mapping Strategy

| Agent | Current OpenAI model | New Anthropic model | Reason |
|---|---|---|---|
| Research | GPT-4o | `claude-haiku-4-5-20241022` | Fact-finding, simple task |
| Scene Generator | GPT-4o | `claude-sonnet-4-5-20241022` | Creative visual prompts |
| Thumbnail | GPT-4o | `claude-haiku-4-5-20241022` | Prompt generation, simple |
| SEO Optimization | GPT-4o | `claude-haiku-4-5-20241022` | Metadata, simple task |
| Voice Generation | GPT-4o-mini | `claude-haiku-4-5-20241022` | Logic only |
| Publishing | GPT-4o-mini | `claude-haiku-4-5-20241022` | API calls, simple |

**IMPORTANT**: Content Planning and Script Writing already use Claude Sonnet — do NOT modify those.

### Code Transformation Pattern

**BEFORE (OpenAI):**
```python
from openai import OpenAI

client = OpenAI()  # reads OPENAI_API_KEY from env
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input}
    ],
    temperature=0.7,
)
# OpenAI voorbeeld
result = response.choices[0].message.content

**AFTER (Anthropic):**

```python
from anthropic import Anthropic

client = Anthropic()  # reads ANTHROPIC_API_KEY from env
response = client.messages.create(
    model="claude-sonnet-4-5-20241022",  # or claude-haiku-4-5-20241022
    max_tokens=4096,
    messages=[
        {"role": "user", "content": f"{system_prompt}\n\n{user_input}"}
    ],
    temperature=0.7,
)
# Anthropic voorbeeld
result = response.content[0].text

**CRITICAL DIFFERENCES:**

- Import: `openai.OpenAI` → `anthropic.Anthropic`
- Method: `chat.completions.create()` → `messages.create()`
- Messages: Anthropic has NO `system` role — prepend system prompt to first user message
- Response: `response.choices[^0].message.content` → `response.content[^0].text`
- Parameter: MUST include `max_tokens` (required by Anthropic)

### requirements.txt Changes

**REMOVE:**

openai>=1.0.0

**ADD:**

anthropic>=0.40.0

### Environment Variable

Ensure `.env` contains:

ANTHROPIC_API_KEY=your_key_here

Remove or comment out `OPENAI_API_KEY` (optional cleanup).

### Refactoring Process (MECHANICAL, NO LOGIC CHANGES)

**IMPORTANT**: This is a **refactor**, NOT a rewrite. Follow Martin Fowler's definition: "Restructuring code without changing external behavior."

**Step-by-step:**

1. **Identify** all files with `from openai import` or `OpenAI()` calls
2. **For each file:**
    - Replace import statement
    - Replace client instantiation
    - Replace `.chat.completions.create()` with `.messages.create()`
    - Merge system/user messages (Anthropic format)
    - Add `max_tokens=4096` parameter
    - Update response extraction path
    - Choose correct model based on agent complexity (see table above)
3. **Preserve** all existing logic:
    - Keep ALL error handling exactly as-is
    - Keep ALL retry logic exactly as-is
    - Keep ALL prompt text exactly as-is
    - Keep ALL variable names exactly as-is
    - Keep ALL function signatures exactly as-is
4. **Update** `requirements.txt` (remove openai, add anthropic)
5. **Test** after each agent conversion (optional per-file verification)

### NEVER Do These Things

**YOU MUST NOT:**

- ❌ Change prompt text or system instructions
- ❌ Add/remove function parameters
- ❌ Modify error handling logic
- ❌ Rewrite working code "to make it better"
- ❌ Change variable names or function names
- ❌ Add features not explicitly requested
- ❌ Skip the `max_tokens` parameter (Anthropic requirement)
- ❌ Use `role: "system"` (Anthropic doesn't support it)
- ❌ Mix OpenAI and Anthropic in one agent (complete per-file migration)


### Code Style (General Python)

- Python 3.14 syntax
- Type hints where helpful (not required everywhere)
- f-strings for string formatting
- Keep imports at top, sorted (stdlib → third-party → local)
- Error messages use logger, not print statements
- Use existing logging patterns in codebase


### Testing After Refactor

Run this to verify Scene Generator works:

```bash
cd swarms-agents
python -c "
from agents.scene_generator import generate_scenes_for_job
scenes = generate_scenes_for_job('507029f3-d296-4c7e-9320-8e363f85b0a0')
print(f'Generated {len(scenes)} scenes')
"

Expected: No errors, prints scene count. If errors, check Anthropic API key and model name.

### Git Workflow

After refactor complete:

```bash
git add .
git commit -m "refactor: migrate all agents from OpenAI to Anthropic Claude

- Replace GPT-4o with Claude Sonnet/Haiku across agents
- Update research, scene_generator, thumbnail, SEO, voice, publishing
- Update requirements.txt (openai → anthropic)
- Cost reduction: ~33% per video ($0.39 → $0.26)
- All agents tested and functional"

### Success Criteria

✅ All agents successfully make Anthropic API calls
✅ No OpenAI imports remain in codebase
✅ `requirements.txt` updated correctly
✅ Scene generator test passes
✅ No behavior changes (same prompts, same output quality)
✅ Cost per video reduced to ~\$0.26

## Additional Context

- **Database**: Supabase (oixvgvsytuqpihnjbmwq.supabase.co)
- **Python venv**: `C:\Users\MikeDonker\Projects\yta-swarms\venv`
- **Current working agents**: Content Planning (Claude Sonnet), Research, Script Writing (Claude Sonnet)
- **Agents needing migration**: Scene Generator, Thumbnail, SEO, Voice, Publishing
- **Current issue**: OpenAI quota exceeded — that's WHY we're migrating

**YOU MUST preserve exact functionality. This is a mechanical SDK swap, not a rewrite.**

Dit CLAUDE.md is:
- **Onder 200 regels** ✅ (149 regels)
- **Gebruikt IMPORTANT/YOU MUST** voor kritieke regels ✅[^2][^1]
- **Bevat negatieve instructies** (NEVER do) ✅[^3]
- **Geeft concrete voorbeelden** (BEFORE/AFTER code) ✅[^9]
- **Heeft duidelijke mechanische stappen** ✅[^6]
- **Specificeert success criteria** ✅[^8]
