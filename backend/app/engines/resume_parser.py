"""
engines/resume_parser.py — Resume parsing using LLM.

Takes raw resume text, extracts structured data for interview personalization.
"""
from __future__ import annotations

import json
import structlog
from app.providers.llm import generate

log = structlog.get_logger(__name__)

PARSE_PROMPT = """Extract these fields from the resume below. Return ONLY valid JSON.

{{
  "candidate_name": "full name",
  "level": "fresh_graduate|trained_fresher|experienced_junior|experienced_senior",
  "years_experience": 0,
  "skills": ["skill1", "skill2"],
  "tools": ["tool1", "tool2"],
  "key_projects": ["project1", "project2"],
  "education": "degree, college"
}}

Rules:
- level: 0 years = fresh_graduate, 0-1 year training = trained_fresher, 1-3 years = experienced_junior, 3+ = experienced_senior
- skills: extract VLSI/EDA specific skills only
- tools: extract EDA tool names (ICC2, PrimeTime, Calibre, Virtuoso, VCS, etc.)
- key_projects: extract project names or descriptions, max 5
- If a field is not found, use empty string or empty list

RESUME:
{resume_text}

JSON:"""


async def parse_resume(resume_text: str, domain: str = "physical_design") -> dict:
    """Parse resume text and return structured data."""
    if not resume_text or len(resume_text.strip()) < 20:
        return {"candidate_name": "Candidate", "domain": domain, "level": "trained_fresher"}

    try:
        prompt = PARSE_PROMPT.format(resume_text=resume_text[:3000])
        raw = await generate(
            system="You are a resume parser. Return only valid JSON.",
            prompt=prompt,
            temperature=0.1,
            max_tokens=500,
        )

        # Clean and parse JSON
        import re
        raw = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(raw)
        parsed["domain"] = domain
        log.info("resume.parsed", name=parsed.get("candidate_name"), skills=len(parsed.get("skills", [])))
        return parsed

    except Exception as e:
        log.warning("resume.parse_failed", error=str(e))
        return {"candidate_name": "Candidate", "domain": domain, "level": "trained_fresher"}
