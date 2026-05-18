import React, { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'

// ── Thin horizontal rule with dot ──────────────────────────────────────────────
function HR() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '0 auto', maxWidth: 1100 }}>
      <div style={{ flex: 1, height: 1, background: 'var(--border-0)' }} />
      <span style={{ width: 4, height: 4, borderRadius: '50%', background: 'var(--border-2)', flexShrink: 0 }} />
      <div style={{ flex: 1, height: 1, background: 'var(--border-0)' }} />
    </div>
  )
}

// ── Nav ────────────────────────────────────────────────────────────────────────
function Nav() {
  const navigate = useNavigate()
  return (
    <header style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 100,
      background: 'rgba(255,255,255,0.92)', backdropFilter: 'blur(12px)',
      borderBottom: '1px solid var(--border-0)',
    }}>
      <div style={{ maxWidth: 1100, margin: '0 auto', padding: '0 32px', height: 56, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            width: 28, height: 28, borderRadius: 7, background: 'var(--accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
              <path d="M2 6H10M6 2V10" stroke="white" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, letterSpacing: '0.05em', color: 'var(--text-0)' }}>
            VLSI<span style={{ color: 'var(--text-3)' }}> INTERVIEW</span>
          </span>
        </div>

        <nav style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <a href="#domains" style={{ fontSize: 13, color: 'var(--text-2)', textDecoration: 'none', padding: '6px 12px', borderRadius: 'var(--r-md)', transition: 'color var(--dur-fast)' }}>Domains</a>
          <a href="#workflow" style={{ fontSize: 13, color: 'var(--text-2)', textDecoration: 'none', padding: '6px 12px', borderRadius: 'var(--r-md)', transition: 'color var(--dur-fast)' }}>Workflow</a>
          <div style={{ width: 1, height: 16, background: 'var(--border-1)', margin: '0 4px' }} />
          <Link to="/login" style={{ fontSize: 13, color: 'var(--text-1)', textDecoration: 'none', padding: '6px 12px', borderRadius: 'var(--r-md)' }}>
            Sign in
          </Link>
          <button
            onClick={() => navigate('/register')}
            style={{
              background: 'var(--text-0)', color: '#fff', border: 'none',
              borderRadius: 'var(--r-md)', padding: '7px 16px', fontSize: 13,
              fontFamily: 'var(--font-body)', cursor: 'pointer', fontWeight: 500,
            }}
          >
            Get started
          </button>
        </nav>
      </div>
    </header>
  )
}

// ── Hero ───────────────────────────────────────────────────────────────────────
function Hero() {
  const navigate = useNavigate()
  return (
    <section style={{ paddingTop: 140, paddingBottom: 100, padding: '140px 32px 100px' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>

        {/* Label */}
        <div style={{ marginBottom: 24, display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.12em',
            textTransform: 'uppercase', color: 'var(--accent-dim)',
            background: 'var(--accent-8)', border: '1px solid var(--accent-15)',
            padding: '4px 10px', borderRadius: 'var(--r-full)',
          }}>
            Technical Evaluation Platform
          </span>
        </div>

        {/* Headline */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 64, alignItems: 'end' }}>
          <div>
            <h1 style={{
              fontFamily: 'var(--font-display)', fontSize: 'clamp(42px, 5.5vw, 68px)',
              color: 'var(--text-0)', lineHeight: 1.08, marginBottom: 24,
              letterSpacing: '-0.01em',
            }}>
              Interview VLSI engineers<br />
              <em style={{ fontStyle: 'italic', color: 'var(--text-2)' }}>
                the way they actually work.
              </em>
            </h1>
            <p style={{
              fontSize: 17, color: 'var(--text-2)', maxWidth: 560,
              lineHeight: 1.7, marginBottom: 36,
            }}>
              AI-powered technical interviews for Analog Layout, Physical Design, and Design Verification — evaluated with the precision of a senior staff engineer.
            </p>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
              <button
                onClick={() => navigate('/register')}
                style={{
                  background: 'var(--accent)', color: '#fff',
                  border: '1px solid var(--accent)', borderRadius: 'var(--r-lg)',
                  padding: '12px 28px', fontSize: 14, fontFamily: 'var(--font-body)',
                  fontWeight: 500, cursor: 'pointer',
                  boxShadow: '0 1px 2px rgba(0,0,0,0.1)',
                }}
              >
                Start interviewing →
              </button>
              <a href="#workflow" style={{
                fontSize: 13, color: 'var(--text-2)', textDecoration: 'none',
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
                See how it works
              </a>
            </div>
          </div>

          {/* Side stats block */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 200 }}>
            {[
              { n: '7',   label: 'Evaluation dimensions' },
              { n: '3',   label: 'VLSI domains' },
              { n: '< 1s', label: 'First question latency' },
              { n: '6',   label: 'Interview modes' },
            ].map(({ n, label }) => (
              <div key={label} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '12px 18px', background: 'var(--bg-0)',
                border: '1px solid var(--border-1)',
                borderRadius: 'var(--r-md)',
                marginBottom: 4,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: 'var(--text-0)', fontWeight: 300 }}>{n}</span>
                <span style={{ fontSize: 12, color: 'var(--text-3)', textAlign: 'right', maxWidth: 120 }}>{label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  )
}

// ── Domains ────────────────────────────────────────────────────────────────────
const DOMAINS = [
  {
    id: 'ANALOG_LAYOUT',
    name: 'Analog Layout',
    description: 'Device matching, LDO and OTA layout strategies, parasitic extraction, DRC/LVS, guard rings, shield routing.',
    topics: ['Device matching & symmetry', 'Parasitic-aware routing', 'Guard ring strategies', 'LDO / OTA / bandgap', 'DRC & LVS sign-off'],
    color: 'var(--accent)',
  },
  {
    id: 'PHYSICAL_DESIGN',
    name: 'Physical Design',
    description: 'Floorplanning, placement, CTS, routing, timing closure, IR drop, signal integrity — full backend PD flow evaluation.',
    topics: ['Floorplanning & partitioning', 'CTS & skew management', 'Timing closure strategies', 'IR drop & EM analysis', 'SI & crosstalk'],
    color: 'var(--blue)',
  },
  {
    id: 'DESIGN_VERIFICATION',
    name: 'Design Verification',
    description: 'UVM methodology, coverage closure, formal verification, assertion-based verification, debug and root-cause analysis.',
    topics: ['UVM testbench architecture', 'Functional coverage closure', 'Assertion-based verification', 'Formal / model checking', 'Debug methodology'],
    color: 'var(--green)',
  },
]

function Domains() {
  return (
    <section id="domains" style={{ padding: '80px 32px' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ marginBottom: 48 }}>
          <MonoLabel>Interview domains</MonoLabel>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 36, color: 'var(--text-0)', marginTop: 8 }}>
            Three disciplines. One platform.
          </h2>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          {DOMAINS.map(d => (
            <div key={d.id} style={{
              background: 'var(--bg-0)', border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-xl)', padding: '28px 28px 24px',
              boxShadow: 'var(--shadow-card)',
            }}>
              <div style={{
                width: 36, height: 36, borderRadius: 'var(--r-md)', background: d.color + '14',
                display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 16,
              }}>
                <div style={{ width: 10, height: 10, borderRadius: 2, background: d.color }} />
              </div>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 10 }}>
                {d.name}
              </h3>
              <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.65, marginBottom: 20 }}>
                {d.description}
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {d.topics.map(t => (
                  <div key={t} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-2)' }}>
                    <span style={{ width: 4, height: 4, borderRadius: '50%', background: d.color, flexShrink: 0 }} />
                    {t}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Workflow ───────────────────────────────────────────────────────────────────
const STEPS = [
  { n: '01', title: 'Domain selection', body: 'Candidate selects their area. The engine loads a domain-specific opening question drawn from a 76-entry corpus of real VLSI interview questions.' },
  { n: '02', title: 'Adaptive evaluation', body: 'Six interview modes adapt dynamically: probing → deepening on strong answers, escalating → pressure on weak ones. The strategy engine updates every turn.' },
  { n: '03', title: 'Real-time voice', body: 'Sub-second STT via Whisper, token-streamed LLM questions, sentence-level TTS. First audio under 900ms. Barge-in under 100ms.' },
  { n: '04', title: '7-dimension scoring', body: 'Accuracy, depth, completeness, clarity, engineering maturity, ownership, and correctness — evaluated asynchronously, never blocking the interview flow.' },
  { n: '05', title: 'Structured report', body: 'Hiring signal, per-dimension scores, strength and weakness summaries, full transcript. Available to reviewers for override and annotation.' },
]

function Workflow() {
  return (
    <section id="workflow" style={{ padding: '80px 32px', background: 'var(--bg-1)' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ marginBottom: 48 }}>
          <MonoLabel>How it works</MonoLabel>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 36, color: 'var(--text-0)', marginTop: 8 }}>
            Not a chatbot. A structured evaluation.
          </h2>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {STEPS.map((step, i) => (
            <div key={step.n} style={{
              background: 'var(--bg-0)', border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-lg)', padding: '24px',
              gridColumn: i === 4 ? '1 / -1' : undefined,
              boxShadow: 'var(--shadow-xs)',
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-4)',
                  paddingTop: 3, flexShrink: 0, letterSpacing: '0.04em',
                }}>
                  {step.n}
                </span>
                <div>
                  <h4 style={{ fontFamily: 'var(--font-display)', fontSize: 18, color: 'var(--text-0)', marginBottom: 8 }}>
                    {step.title}
                  </h4>
                  <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.65 }}>
                    {step.body}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Roles ──────────────────────────────────────────────────────────────────────
function Roles() {
  const navigate = useNavigate()
  return (
    <section style={{ padding: '80px 32px' }}>
      <div style={{ maxWidth: 1100, margin: '0 auto' }}>
        <div style={{ marginBottom: 40 }}>
          <MonoLabel>Who this is for</MonoLabel>
          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 36, color: 'var(--text-0)', marginTop: 8 }}>
            Three roles. One platform.
          </h2>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14 }}>
          {[
            {
              role: 'Candidates',
              description: 'Practice VLSI technical interviews with an AI that evaluates like a staff engineer — no softballing, no generic questions.',
              cta: 'Start practicing',
              path: '/register',
              points: ['Real domain-specific questions', 'Immediate structured feedback', 'Adaptive difficulty'],
            },
            {
              role: 'Interviewers & Reviewers',
              description: 'Review AI-conducted sessions, annotate transcripts, override scores, and make hiring decisions with full context.',
              cta: 'Access review queue',
              path: '/login',
              points: ['Full transcript review', 'Score override controls', 'Integrity signals'],
            },
            {
              role: 'Recruiting Teams',
              description: 'Monitor all sessions, manage users, track costs and latency, and control the evaluation prompts from a unified admin panel.',
              cta: 'View admin access',
              path: '/login',
              points: ['Live session monitoring', 'Cost and latency analytics', 'Prompt management'],
            },
          ].map(r => (
            <div key={r.role} style={{
              background: 'var(--bg-0)', border: '1px solid var(--border-1)',
              borderRadius: 'var(--r-xl)', padding: '28px 26px',
              display: 'flex', flexDirection: 'column',
              boxShadow: 'var(--shadow-card)',
            }}>
              <h3 style={{ fontFamily: 'var(--font-display)', fontSize: 20, color: 'var(--text-0)', marginBottom: 10 }}>
                {r.role}
              </h3>
              <p style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.65, marginBottom: 20 }}>
                {r.description}
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginBottom: 24, flex: 1 }}>
                {r.points.map(p => (
                  <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-2)' }}>
                    <span style={{ color: 'var(--green)', flexShrink: 0, fontSize: 11 }}>✓</span>
                    {p}
                  </div>
                ))}
              </div>
              <button
                onClick={() => navigate(r.path)}
                style={{
                  background: 'var(--bg-1)', color: 'var(--text-1)',
                  border: '1px solid var(--border-2)', borderRadius: 'var(--r-md)',
                  padding: '8px 16px', fontSize: 12, fontFamily: 'var(--font-body)',
                  cursor: 'pointer', fontWeight: 500, alignSelf: 'flex-start',
                }}
              >
                {r.cta} →
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

// ── Footer ─────────────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer style={{ borderTop: '1px solid var(--border-0)', padding: '32px', background: 'var(--bg-0)' }}>
      <div style={{
        maxWidth: 1100, margin: '0 auto',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-4)', letterSpacing: '0.06em' }}>
            VLSI INTERVIEW PLATFORM
          </span>
        </div>
        <div style={{ display: 'flex', gap: 24 }}>
          {['Analog Layout', 'Physical Design', 'Design Verification'].map(d => (
            <span key={d} style={{ fontSize: 11, color: 'var(--text-4)' }}>{d}</span>
          ))}
        </div>
      </div>
    </footer>
  )
}

// Small helper used in Domains
function MonoLabel({ children }: { children: string }) {
  return (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-3)' }}>
      {children}
    </span>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────
export default function LandingPage() {
  return (
    <div style={{ background: 'var(--bg-canvas)' }}>
      <Nav />
      <Hero />
      <HR />
      <Domains />
      <HR />
      <Workflow />
      <HR />
      <Roles />
      <Footer />
    </div>
  )
}
