"""
engines/resume_parser.py — Resume parsing using LLM.

Approach:
  1. Claude Haiku 4.5 (Bedrock) as primary parser — fast, accurate
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
- years_experience
  Convert ALL experience into decimal years
  Examples:
  - 6 months = 0.5
  - 10 months = 0.8
  - 1 year 6 months = 1.5
  - 2 years 3 months = 2.25
  NEVER confuse months with years
  Freshers = 0
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

_bedrock_client = None
_openai_client = None

HAIKU_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _get_bedrock():
    global _bedrock_client
    if _bedrock_client is None:
        import boto3, os
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        )
    return _bedrock_client


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from app.config import get_settings
        settings = get_settings()
        if settings.OPENAI_API_KEY:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def _call_haiku(prompt: str) -> str:
    """Resume parsing via Claude Haiku 4.5 on Bedrock."""
    client = _get_bedrock()
    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 500,
        "temperature": 0.1,
        "system": [{"type": "text", "text": "You are a resume parser. Return only valid JSON."}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]}],
    })
    try:
        resp = client.invoke_model(modelId=HAIKU_MODEL_ID, body=body)
        result = json.loads(resp["body"].read())
        return result["content"][0]["text"].strip()
    except Exception as exc:
        log.error("resume.haiku_error", error=str(exc), model=HAIKU_MODEL_ID)
        raise


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
    """Try Haiku 4.5 first (fast, accurate), fall back to OpenAI."""
    try:
        result = _call_haiku(prompt)
        log.info("resume.haiku_success", chars=len(result))
        return result
    except Exception as e:
        log.warning("resume.haiku_fallback", error=str(e))
    return _call_openai(prompt)


# ── Main parse function ─────────────────────────────────────────────────────

async def parse_resume(resume_text: str, domain: str = "physical_design") -> dict:
    """
    Parse resume text into structured JSON.
    Uses Claude Haiku 4.5 (Bedrock) with OpenAI fallback.
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

def _extract_pdf_pymupdf(file_bytes: bytes) -> str:
    """PyMuPDF (fitz) — strongest extractor. Handles embedded fonts, complex layouts."""
    import fitz
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text.strip()


def _extract_pdf_pypdf(file_bytes: bytes) -> str:
    """PyPDF — good fallback, handles most standard PDFs."""
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(file_bytes))
    text = ""
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"
    return text.strip()


def _extract_pdf_pdfplumber(file_bytes: bytes) -> str:
    """pdfplumber — last resort, good with tables."""
    import io
    import pdfplumber
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"
    return text.strip()


def _extract_pdf(file_bytes: bytes) -> str:
    """Chain: PyMuPDF → PyPDF → pdfplumber. Returns first non-empty result."""
    extractors = [
        ("pymupdf", _extract_pdf_pymupdf),
        ("pypdf", _extract_pdf_pypdf),
        ("pdfplumber", _extract_pdf_pdfplumber),
    ]
    for name, fn in extractors:
        try:
            text = fn(file_bytes)
            chars = len(text.strip()) if text else 0
            log.info("resume.pdf_extractor_result", method=name, chars=chars,
                     preview=text[:80].strip() if text else "(empty)")
            if chars > 20:
                log.info("resume.pdf_extracted", method=name, chars=chars)
                return text
        except Exception as e:
            log.warning("resume.pdf_extractor_failed", method=name, error=str(e))
    log.warning("resume.pdf_all_extractors_failed", file_size=len(file_bytes))
    return ""


async def extract_file_text(file_bytes: bytes, filename: str) -> str:
    """Extract text from uploaded file. Supports PDF, DOCX, TXT."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "pdf":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _extract_pdf, file_bytes)

    elif ext in ("docx", "doc"):
        import tempfile, os
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
