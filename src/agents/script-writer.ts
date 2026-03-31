// yta-swarms/src/agents/script-writer.ts
import { NimClient, getNimClient, NIM_MODELS } from '../../../shared/nim-client';
import { createClient, SupabaseClient } from '@supabase/supabase-js';

// ──────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────
interface ScriptSection {
  id: string;
  type: 'hook' | 'context' | 'main' | 'cta' | 'outro';
  title: string;
  content: string;
  durationSeconds: number;
  keywords: string[];
  visualCue: string;
}

interface ScriptResult {
  sections: ScriptSection[];
  fullScript: string;
  wordCount: number;
  estimatedDuration: number;
  hookVariants: string[];
  qualityScore: QualityScore;
  seoKeywordsUsed: string[];
}

interface QualityScore {
  overall: number;
  hookStrength: number;
  keywordDensity: number;
  emotionalRange: number;
  ctaPresent: boolean;
  retentionHooks: number;
  issues: string[];
}

interface VideoJob {
  id: string;
  channel_id: string;
  title_concept: string;
  niche: string;
  format: 'LONG' | 'SHORT';
  keyword_targets: string[];
  research_data: string;
  outline: string[];
}

// ──────────────────────────────────────────────
// Hook Engine
// ──────────────────────────────────────────────
const HOOK_SYSTEM_PROMPT = `Je bent een expert YouTube script hook schrijver. Je maakt hooks die kijkers binnen 5 seconden grijpen.

HOOK STRUCTUUR (eerste 30 seconden):
- 0-5s: Shock statement, contrarian claim, of onbeantwoorde vraag
- 5-15s: Concrete belofte — wat leert de kijker?
- 15-30s: Stakes uitleggen — waarom is dit NU belangrijk?

REGELS:
1. Begin NOOIT met "Hallo" of "Welkom bij mijn kanaal"
2. Eerste zin moet een emotionele trigger bevatten (verbazing, angst, curiosity)
3. Gebruik specifieke getallen ("97% van de mensen weet dit niet")
4. Eindig de hook met een open loop ("...en het derde punt verandert alles")
5. Max 80 woorden voor de hele hook

ENGAGEMENT TRIGGERS om te verwerken:
- "Maar hier is het ding..."
- "Wat de meeste mensen niet weten is..."
- "Dit verandert alles..."
- "En hier wordt het interessant..."
- "Wacht tot je hoort wat er dan gebeurde..."`;

const SCRIPT_LONG_PROMPT = `Je bent een professionele YouTube script schrijver voor video's van 8-15 minuten.

STRUCTUUR:
1. HOOK (0-30s, ~80 woorden) — zie hook instructies
2. CONTEXT (30s-1:30, ~150 woorden) — achtergrond, waarom dit onderwerp relevant is
3. MAIN CONTENT — 3-5 secties, elk 2-3 minuten (~300-450 woorden per sectie)
4. CTA (15s, ~40 woorden) — subscribe, like, comment vraag
5. OUTRO (15s, ~40 woorden) — teaser voor volgende video

PATTERN INTERRUPTS (elke 60-90 seconden):
- Toon shift (van serieus naar luchtig)
- Directe vraag aan kijker ("Heb je dit ooit meegemaakt?")
- "Maar wacht..." transitie
- Nieuwe sectie met eigen mini-hook

VISUELE CUES:
Bij elk segment, schrijf tussen [VISUAL: ...] wat er op het scherm moet verschijnen.
Voorbeeld: [VISUAL: B-roll van drukke stad, tekst overlay "97% WEET DIT NIET"]

OUTPUT FORMAT:
Lever het script als JSON met deze structuur:
{
  "sections": [
    {
      "id": "hook",
      "type": "hook",
      "title": "Hook",
      "content": "...",
      "durationSeconds": 30,
      "keywords": ["keyword1"],
      "visualCue": "..."
    }
  ]
}`;

const SCRIPT_SHORT_PROMPT = `Je bent een expert YouTube Shorts script schrijver voor video's van 45-60 seconden.

STRUCTUUR:
1. HOOK (0-5s, ~15 woorden) — pakkende eerste zin, meteen in de actie
2. PAYLOAD (5-40s, ~100 woorden) — kernboodschap, snel, to-the-point
3. CTA (40-50s, ~20 woorden) — follow, like, of comment

REGELS:
- Elk woord telt. Geen filler.
- Spreek direct tot de kijker ("jij", "je")
- Maximaal 150 woorden totaal
- Primair keyword in eerste zin

OUTPUT: JSON met sections array (zelfde format als LONG).`;

// ──────────────────────────────────────────────
// Script Writer Agent
// ──────────────────────────────────────────────
export class ScriptWriterAgent {
  private nim: NimClient;
  private supabase: SupabaseClient;

  constructor() {
    this.nim = getNimClient();
    this.supabase = createClient(
      process.env.SUPABASE_URL!,
      process.env.SUPABASE_SERVICE_KEY!
    );
  }

  async writeScript(job: VideoJob): Promise<ScriptResult> {
    // Step 1: Generate hook variants
    const hookVariants = await this.generateHookVariants(job);

    // Step 2: Write full script with best hook
    const script = await this.generateFullScript(job, hookVariants[0]);

    // Step 3: Validate quality
    const qualityScore = this.validateScript(script, job);

    // Step 4: If quality < 70, regenerate with feedback
    if (qualityScore.overall < 70) {
      const improvedScript = await this.regenerateWithFeedback(
        job, script, qualityScore
      );
      const improvedScore = this.validateScript(improvedScript, job);
      return {
        ...improvedScript,
        hookVariants,
        qualityScore: improvedScore,
      };
    }

    return {
      ...script,
      hookVariants,
      qualityScore,
    };
  }

  private async generateHookVariants(job: VideoJob): Promise<string[]> {
    const { content } = await this.nim.chat({
      task: 'script-writing',
      messages: [
        { role: 'system', content: HOOK_SYSTEM_PROMPT },
        {
          role: 'user',
          content: `Genereer 3 hook varianten voor deze video:
Titel concept: ${job.title_concept}
Niche: ${job.niche}
Keywords: ${job.keyword_targets.join(', ')}
Research: ${job.research_data?.substring(0, 2000) || 'Geen research data'}

Lever op als JSON array van 3 strings. Elke hook is ~80 woorden.
Varieer in aanpak: 1) shock/statistiek, 2) vraag/curiosity, 3) contrarian statement.`,
        },
      ],
      temperature: 0.9,
      maxTokens: 2048,
    });

    try {
      return JSON.parse(content);
    } catch {
      return content.split('\n\n').filter((h) => h.trim().length > 20);
    }
  }

  private async generateFullScript(
    job: VideoJob,
    hook: string
  ): Promise<Omit<ScriptResult, 'hookVariants' | 'qualityScore'>> {
    const systemPrompt =
      job.format === 'LONG' ? SCRIPT_LONG_PROMPT : SCRIPT_SHORT_PROMPT;

    const targetWords = job.format === 'LONG' ? '1200-2000' : '100-150';

    const { content } = await this.nim.chat({
      task: 'script-writing',
      messages: [
        { role: 'system', content: systemPrompt },
        {
          role: 'user',
          content: `Schrijf een volledig script voor deze video.

Titel: ${job.title_concept}
Niche: ${job.niche}
Format: ${job.format}
Target woordenaantal: ${targetWords}
Primair keyword: ${job.keyword_targets[0]}
Alle keywords: ${job.keyword_targets.join(', ')}
Hook (gebruik deze): ${hook}
Research data: ${job.research_data?.substring(0, 4000) || 'Gebruik je eigen kennis'}
Outline: ${job.outline?.join(' → ') || 'Vrij'}

BELANGRIJK:
- Primair keyword MOET in eerste 15 seconden voorkomen
- Voeg [VISUAL: ...] cues toe bij elk segment
- Voeg minstens 3 retention hooks in (aankondiging wat nog komt)
- Eindig met een sterke CTA

Lever op als JSON object met "sections" array.`,
        },
      ],
      temperature: 0.7,
      maxTokens: 8192,
    });

    try {
      const parsed = JSON.parse(content);
      const sections: ScriptSection[] = parsed.sections || [];
      const fullScript = sections.map((s) => s.content).join('\n\n');
      const wordCount = fullScript.split(/\s+/).length;
      const estimatedDuration = Math.round(wordCount / 2.5);

      return {
        sections,
        fullScript,
        wordCount,
        estimatedDuration,
        seoKeywordsUsed: this.extractUsedKeywords(fullScript, job.keyword_targets),
      };
    } catch {
      throw new Error(`Failed to parse script JSON from NIM response: ${content.substring(0, 200)}`);
    }
  }

  // ──────────────────────────────────────────
  // Quality Validation
  // ──────────────────────────────────────────
  private validateScript(
    script: Omit<ScriptResult, 'hookVariants' | 'qualityScore'>,
    job: VideoJob
  ): QualityScore {
    const issues: string[] = [];
    let overall = 100;

    const minWords = job.format === 'LONG' ? 1200 : 100;
    const maxWords = job.format === 'LONG' ? 2000 : 150;
    if (script.wordCount < minWords) {
      issues.push(`Te kort: ${script.wordCount} woorden (min ${minWords})`);
      overall -= 20;
    }
    if (script.wordCount > maxWords * 1.2) {
      issues.push(`Te lang: ${script.wordCount} woorden (max ${maxWords})`);
      overall -= 10;
    }

    const hookSection = script.sections.find((s) => s.type === 'hook');
    let hookStrength = 0;
    if (!hookSection) {
      issues.push('Geen hook sectie gevonden');
      overall -= 25;
    } else {
      hookStrength = this.scoreHook(hookSection.content);
      if (hookStrength < 50) {
        issues.push(`Zwakke hook (score: ${hookStrength}/100)`);
        overall -= 15;
      }
    }

    const primaryKeyword = job.keyword_targets[0]?.toLowerCase() || '';
    const fullTextLower = script.fullScript.toLowerCase();
    const keywordCount = (fullTextLower.match(new RegExp(primaryKeyword, 'gi')) || []).length;
    const keywordDensity = Math.min(100, keywordCount * 20);

    if (keywordCount === 0) {
      issues.push('Primair keyword niet gevonden in script');
      overall -= 20;
    }

    const first40Words = script.fullScript.split(/\s+/).slice(0, 40).join(' ').toLowerCase();
    if (!first40Words.includes(primaryKeyword)) {
      issues.push('Primair keyword niet in eerste 15 seconden');
      overall -= 10;
    }

    const ctaSection = script.sections.find((s) => s.type === 'cta');
    const ctaPresent = !!ctaSection || /subscribe|abonneer|like|comment/i.test(script.fullScript);
    if (!ctaPresent) {
      issues.push('Geen CTA gevonden');
      overall -= 10;
    }

    const retentionPhrases = [
      'maar wacht', 'hier is het ding', 'wat de meeste mensen niet weten',
      'dit verandert alles', 'en hier wordt het interessant', 'maar eerst',
      'stay tuned', 'verderop in deze video', 'straks laat ik zien',
    ];
    const retentionHooks = retentionPhrases.reduce(
      (count, phrase) => count + (fullTextLower.includes(phrase) ? 1 : 0),
      0
    );
    if (retentionHooks < 2 && job.format === 'LONG') {
      issues.push(`Te weinig retention hooks: ${retentionHooks} (min 2)`);
      overall -= 10;
    }

    const sentences = script.fullScript.split(/[.!?]+/).filter((s) => s.trim().length > 5);
    const questions = sentences.filter((s) => s.includes('?')).length;
    const exclamations = sentences.filter((s) => s.includes('!')).length;
    const emotionalRange = Math.min(100, (questions + exclamations) * 15);
    if (emotionalRange < 30) {
      issues.push('Script klinkt monotoon — meer vragen en uitroepen nodig');
      overall -= 5;
    }

    return {
      overall: Math.max(0, Math.min(100, overall)),
      hookStrength,
      keywordDensity,
      emotionalRange,
      ctaPresent,
      retentionHooks,
      issues,
    };
  }

  private scoreHook(hookText: string): number {
    let score = 50;
    const lower = hookText.toLowerCase();

    const triggers = ['nooit', 'altijd', 'geheim', 'waarheid', 'fout', 'gevaar',
      'vergeet', 'moet', 'stop', 'wist je', 'niemand', 'iedereen'];
    if (triggers.some((t) => lower.includes(t))) score += 15;

    if (/\d+%/.test(hookText) || /\d+ van de \d+/.test(hookText)) score += 15;

    if (/\bje\b|\bjij\b|\bjouw\b/i.test(hookText)) score += 10;

    if (/\.{3}|maar\b|wacht\b/i.test(hookText)) score += 10;

    return Math.min(100, score);
  }

  private extractUsedKeywords(text: string, targets: string[]): string[] {
    const lower = text.toLowerCase();
    return targets.filter((kw) => lower.includes(kw.toLowerCase()));
  }

  // ──────────────────────────────────────────
  // Regenerate with feedback
  // ──────────────────────────────────────────
  private async regenerateWithFeedback(
    job: VideoJob,
    previousScript: Omit<ScriptResult, 'hookVariants' | 'qualityScore'>,
    score: QualityScore
  ): Promise<Omit<ScriptResult, 'hookVariants' | 'qualityScore'>> {
    const { content } = await this.nim.chat({
      task: 'script-writing',
      messages: [
        {
          role: 'system',
          content: job.format === 'LONG' ? SCRIPT_LONG_PROMPT : SCRIPT_SHORT_PROMPT,
        },
        {
          role: 'user',
          content: `Het vorige script had kwaliteitsproblemen. Herschrijf het volledig.

PROBLEMEN:
${score.issues.map((i) => `- ${i}`).join('\n')}

VORIG SCRIPT (ter referentie):
${previousScript.fullScript.substring(0, 3000)}

VEREISTEN:
- Titel: ${job.title_concept}
- Keywords: ${job.keyword_targets.join(', ')}
- Format: ${job.format}
- Los ALLE bovengenoemde problemen op
- Lever op als JSON met "sections" array`,
        },
      ],
      temperature: 0.8,
      maxTokens: 8192,
    });

    const parsed = JSON.parse(content);
    const sections: ScriptSection[] = parsed.sections || [];
    const fullScript = sections.map((s) => s.content).join('\n\n');

    return {
      sections,
      fullScript,
      wordCount: fullScript.split(/\s+/).length,
      estimatedDuration: Math.round(fullScript.split(/\s+/).length / 2.5),
      seoKeywordsUsed: this.extractUsedKeywords(fullScript, job.keyword_targets),
    };
  }

  // ──────────────────────────────────────────
  // Supabase Integration
  // ──────────────────────────────────────────
  async processJob(jobId: string): Promise<void> {
    const { data: job, error } = await this.supabase
      .from('video_jobs')
      .select('*')
      .eq('id', jobId)
      .single();

    if (error || !job) {
      throw new Error(`Job ${jobId} not found: ${error?.message}`);
    }

    try {
      const result = await this.writeScript(job as VideoJob);

      await this.supabase
        .from('video_jobs')
        .update({
          status: 'SCRIPTED',
          script: result.fullScript,
          script_sections: result.sections,
          script_word_count: result.wordCount,
          script_quality_score: result.qualityScore.overall,
          script_quality_details: result.qualityScore,
          hook_variants: result.hookVariants,
          seo_keywords_used: result.seoKeywordsUsed,
          nim_model_used: NIM_MODELS.SCRIPT,
          nim_tokens_used: this.nim.getUsageStats().totalTokens,
          updated_at: new Date().toISOString(),
        })
        .eq('id', jobId);
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      await this.supabase
        .from('video_jobs')
        .update({
          status: 'ERROR',
          error_message: `Script generation failed: ${errorMsg}`,
          last_error_at: new Date().toISOString(),
          retry_count: (job.retry_count || 0) + 1,
          updated_at: new Date().toISOString(),
        })
        .eq('id', jobId);
      throw err;
    }
  }
}
