"""
engines/resume_parser.py — Resume parsing using LLM.

Approach (from monolith):
  1. Cerebras (fast, free) as primary parser
  2. OpenAI gpt-4o-mini as fallback
  3. Separate parse endpoint — parse first, show preview, THEN create session
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import structlog

log = structlog.get_logger(__name__)

PARSE_PROMPT = """Extract from this resume. Return ONLY valid JSON:
{{"candidate_name":"","email":"","phone":"","level":"fresh_graduate|trained_fresher|experienced_junior|experienced_senior",
"years_experience":0,"skills":[],"tools":[],"key_projects":[],"domain":"","education":""}}

Rules:
- email: extract email address if present, empty string if not found
- phone: extract phone number if present, empty string if not found
- years_experience: convert to YEARS as a decimal. 10 months = 0.8, 6 months = 0.5, 1.5 years = 1.5, 2 years 3 months = 2.25. NEVER confuse months with years.
- level: based on years_experience: 0 = fresh_graduate, 0-1 year = trained_fresher, 1-3 years = experienced_junior, 3+ years = experienced_senior
- skills: VLSI/EDA specific only, return as flat list of strings
- tools: EDA tool names (ICC2, PrimeTime, Calibre, Virtuoso, VCS, etc.), return as flat list of strings
- key_projects: max 5, return as flat list of strings (project names only, not objects)
- domain: physical_design or analog_layout or design_verification

RESUME:
{resume_text}

JSON:"""


def _safe_json(text: str):
    """Extract JSON from LLM response."""
    text = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


# ── LLM clients (lazy init) ─────────────────────────────────────────────────

_cerebras_client = None
_openai_client = None


def _get_cerebras():
    global _cerebras_client
    if _cerebras_client is None:
        from app.config import get_settings
        settings = get_settings()
        key = settings.CEREBRAS_API_KEY
        if key:
            from openai import OpenAI
            _cerebras_client = OpenAI(
                api_key=key,
                base_url="https://api.cerebras.ai/v1",
            )
            log.info("resume_parser.cerebras_ready")
    return _cerebras_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from app.config import get_settings
        settings = get_settings()
        if settings.OPENAI_API_KEY:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def _call_cerebras(prompt: str) -> str:
    """Fast resume parsing via Cerebras. Returns raw LLM response."""
    client = _get_cerebras()
    if not client:
        raise RuntimeError("Cerebras not configured")
    resp = client.chat.completions.create(
        model="llama3.1-8b",
        messages=[
            {"role": "system", "content": "You are a resume parser. Return only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=500,
    )
    return resp.choices[0].message.content.strip()


def _call_openai(prompt: str) -> str:
    """Fallback resume parsing via OpenAI."""
    client = _get_openai()
    if not client:
        raise RuntimeError("OpenAI not configured")
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


def _call_llm_sync(prompt: str) -> str:
    """Try Cerebras first (fast, free), fall back to OpenAI."""
    try:
        return _call_cerebras(prompt)
    except Exception as e:
        log.info("resume.cerebras_fallback", error=str(e))
    return _call_openai(prompt)


# ── Main parse function ─────────────────────────────────────────────────────

async def parse_resume(resume_text: str, domain: str = "physical_design") -> dict:
    """
    Parse resume text into structured JSON.
    Uses Cerebras (fast/free) with OpenAI fallback.
    Retries 3 times. Returns fallback dict on total failure.
    """
    if not resume_text or len(resume_text.strip()) < 20:
        return {}

    prompt = PARSE_PROMPT.format(resume_text=resume_text[:3000])
    loop = asyncio.get_event_loop()

    for attempt in range(3):
        t0 = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, _call_llm_sync, prompt),
                timeout=15.0,
            )
            elapsed = int((time.monotonic() - t0) * 1000)
            log.info("resume.raw_response", attempt=attempt + 1,
                     raw_len=len(raw), latency_ms=elapsed,
                     raw_preview=raw[:200] if raw else "(empty)")

            parsed = _safe_json(raw)
            if parsed:
                # Fill defaults for any missing fields
                parsed.setdefault("candidate_name", "Candidate")
                parsed.setdefault("domain", domain)
                parsed.setdefault("level", "trained_fresher")
                parsed.setdefault("years_experience", 0)
                parsed.setdefault("skills", [])
                parsed.setdefault("tools", [])
                parsed.setdefault("key_projects", [])
                parsed.setdefault("education", "")
                parsed.setdefault("email", "")
                parsed.setdefault("phone", "")

                # Valid if we got any real content
                has_content = (
                    parsed.get("skills")
                    or parsed.get("tools")
                    or parsed.get("key_projects")
                    or (parsed.get("candidate_name", "") != "Candidate"
                        and parsed.get("candidate_name", "") != "")
                )
                if has_content:
                    from app.observability.call_tracker import track_resume_parse
                    track_resume_parse(session_id="", latency_ms=elapsed, status="success")
                    log.info("resume.parsed",
                             name=parsed.get("candidate_name"),
                             skills=len(parsed.get("skills", [])),
                             tools=len(parsed.get("tools", [])),
                             projects=len(parsed.get("key_projects", [])),
                             attempt=attempt + 1, latency_ms=elapsed)
                    return parsed

            log.warning("resume.empty_result", attempt=attempt + 1,
                        raw_preview=raw[:150] if raw else "(empty)")
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            log.warning("resume.parse_failed", attempt=attempt + 1,
                        error=str(e), latency_ms=elapsed)

    return {}


# ── PDF / DOCX text extraction ──────────────────────────────────────────────

async def extract_file_text(file_bytes: bytes, filename: str) -> str:
    """Extract text from uploaded file. Supports PDF, DOCX, TXT."""
    import tempfile
    import os

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "pdf":
        tmp_path = None
        try:
            import pdfplumber
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            text = ""
            with pdfplumber.open(tmp_path) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
            return text.strip()
        except Exception as e:
            log.warning("resume.pdf_extract_failed", error=str(e))
            return ""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    elif ext in ("docx", "doc"):
        tmp_path = None
        try:
            import docx2txt
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            return docx2txt.process(tmp_path) or ""
        except Exception as e:
            log.warning("resume.docx_extract_failed", error=str(e))
            return ""
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    else:
        return file_bytes.decode("utf-8", errors="ignore")
