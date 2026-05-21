"""
engines/resume_parser.py — Resume parsing using LLM.

Takes raw resume text, extracts structured data for interview personalization.
Uses sync OpenAI client in executor for reliability (same as monolith).
"""
from __future__ import annotations

import asyncio
import json
import re
import structlog

log = structlog.get_logger(__name__)

PARSE_PROMPT = """Extract from this resume. Return ONLY valid JSON:
{{"candidate_name":"","email":"","phone":"","level":"fresh_graduate|trained_fresher|experienced_junior|experienced_senior",
"years_experience":0,"skills":[],"tools":[],"key_projects":[],"domain":"","education":""}}

Rules:
- email: extract email if present, empty string if not
- phone: extract phone if present, empty string if not
- level: 0 years = fresh_graduate, 0-1 year = trained_fresher, 1-3 = experienced_junior, 3+ = experienced_senior
- skills: VLSI/EDA specific only
- tools: EDA tool names (ICC2, PrimeTime, Calibre, Virtuoso, VCS, etc.)
- key_projects: max 5
- domain: physical_design or analog_layout or design_verification

RESUME:
{resume_text}

JSON:"""


def _safe_json(text: str):
    text = re.sub(r"```json|```", "", text).strip()
    try: return json.loads(text)
    except: pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try: return json.loads(m.group())
        except: pass
    return None


async def parse_resume(resume_text: str, domain: str = "physical_design") -> dict:
    """Parse resume with retry. Uses sync OpenAI in executor for reliability."""
    if not resume_text or len(resume_text.strip()) < 20:
        return {"candidate_name": "Candidate", "domain": domain, "level": "trained_fresher"}

    from openai import OpenAI
    from app.config import get_settings
    settings = get_settings()
    client = OpenAI(api_key=settings.OPENAI_API_KEY)

    prompt = PARSE_PROMPT.format(resume_text=resume_text[:3000])

    def _sync_parse():
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a resume parser. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        return resp.choices[0].message.content.strip()

    loop = asyncio.get_event_loop()

    for attempt in range(3):
        try:
            raw = await asyncio.wait_for(loop.run_in_executor(None, _sync_parse), timeout=10.0)
            parsed = _safe_json(raw)
            if parsed and parsed.get("candidate_name"):
                parsed.setdefault("domain", domain)
                log.info("resume.parsed", name=parsed.get("candidate_name"),
                         skills=len(parsed.get("skills", [])), attempt=attempt+1)
                return parsed
            log.warning("resume.empty_result", attempt=attempt+1)
        except Exception as e:
            log.warning("resume.parse_failed", attempt=attempt+1, error=str(e))

    return {"candidate_name": "Candidate", "domain": domain, "level": "trained_fresher"}
