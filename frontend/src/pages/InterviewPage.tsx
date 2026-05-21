/**
 * InterviewPage.tsx — Live interview screen.
 *
 * Layout: Full-screen. Left: transcript. Right: status panel.
 * Audio states: silence → listening → thinking → speaking
 * All WS event types match backend WSEventType enum (uppercase).
 */
import React, { useEffect, useRef, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useInterview } from '@/stores/interview'
import { useAuth } from '@/stores/auth'
import { integrityApi, sessionApi } from '@/lib/api'
import ConnectionStatus from '@/components/interview/ConnectionStatus'
import { ModeTag } from '@/components/ui'

const DOMAIN_LABELS: Record<string, string> = {
  ANALOG_LAYOUT:       'Analog Layout',
  PHYSICAL_DESIGN:     'Physical Design',
  DESIGN_VERIFICATION: 'Design Verification',
}

// ── Main ───────────────────────────────────────────────────────────────────────
type Stage = 'setup' | 'permissions' | 'interview'
type DomainKey = 'ANALOG_LAYOUT' | 'PHYSICAL_DESIGN' | 'DESIGN_VERIFICATION'

export default function InterviewPage() {
  const { sessionId: urlSessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const {
    wsStatus, audioState, mode, turnNumber,
    currentQuestion, transcript, isStreaming, turns,
    reconnectMessage,
    connect, disconnect, bargeIn,
  } = useInterview()

  const transcriptRef = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)

  // Stage management
  const [stage, setStage] = useState<Stage>(urlSessionId ? 'permissions' : 'setup')
  const [sessionId, setSessionId] = useState<string>(urlSessionId || '')

  // Setup stage state
  const [resumeText, setResumeText] = useState('')
  const [resumeFile, setResumeFile] = useState<File | null>(null)
  const [resumeLoading, setResumeLoading] = useState(false)
  const [selectedDomain, setSelectedDomain] = useState<DomainKey>('PHYSICAL_DESIGN')
  const [creating, setCreating] = useState(false)

  // Permission stage state
  const [domain, setDomain] = useState<string>('Interview')
  const [permGranted, setPermGranted] = useState(false)
  const [permError, setPermError] = useState('')
  const [mediaStream, setMediaStream] = useState<MediaStream | null>(null)

  const handleResumeFile = async (file: File) => {
    setResumeFile(file)
    setResumeLoading(true)
    // For PDF, don't try to read as text — backend will extract
    if (file.name.toLowerCase().endsWith('.pdf')) {
      setResumeText('(PDF uploaded)')
    } else {
      try {
        const text = await file.text()
        setResumeText(text)
      } catch { }
    }
    setResumeLoading(false)
  }

  const handleCreateSession = async () => {
    if (!resumeFile && !resumeText) return
    setCreating(true)
    try {
      let res
      if (resumeFile) {
        // Upload file directly — backend extracts PDF text
        res = await sessionApi.createWithFile(selectedDomain, resumeFile)
      } else {
        res = await sessionApi.create(selectedDomain, resumeText)
      }
      const sid = res.data?.session_id || res.data?.id
      if (sid) {
        setSessionId(sid)
        setDomain(DOMAIN_LABELS[selectedDomain] || selectedDomain)
        setStage('permissions')
      }
    } catch {
      alert('Failed to create session. Please try again.')
    }
    setCreating(false)
  }

  const requestPermissions = async () => {
    try {
      setPermError('')
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true })
      setMediaStream(stream)
      if (videoRef.current) {
        videoRef.current.srcObject = stream
      }
      setPermGranted(true)
      setStage('interview')
    } catch (err: any) {
      if (err.name === 'NotAllowedError') {
        setPermError('Camera and microphone access denied. Please allow permissions and try again.')
      } else if (err.name === 'NotFoundError') {
        setPermError('No camera or microphone found. Please connect a device.')
      } else {
        setPermError(`Permission error: ${err.message}`)
      }
    }
  }

  // If landed with sessionId in URL, fetch domain
  useEffect(() => {
    if (urlSessionId) {
      sessionApi.get(urlSessionId)
        .then(res => {
          const d = res.data?.domain || 'ANALOG_LAYOUT'
          setDomain(DOMAIN_LABELS[d] || d)
        })
        .catch(() => {})
    }
  }, [urlSessionId])

  // Connect only after permissions granted + interview stage
  useEffect(() => {
    if (!sessionId || stage !== 'interview') return
    connect(sessionId)
    return () => {
      disconnect()
      if (mediaStream) {
        mediaStream.getTracks().forEach(t => t.stop())
      }
    }
  }, [sessionId, stage])

  // Auto-scroll
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [currentQuestion, turns.length])

  // Redirect after session end
  useEffect(() => {
    if (wsStatus === 'ended') {
      setTimeout(() => navigate(`/report/${sessionId}`), 1600)
    }
  }, [wsStatus])

  // Anti-cheat passive tracking
  const sendIntegrityEvent = useCallback((type: string) => {
    if (!sessionId) return
    integrityApi.sendEvent({ session_id: sessionId, event_type: type, context: { ts: Date.now() } })
  }, [sessionId])

  useEffect(() => {
    const onHide  = () => { if (document.hidden) sendIntegrityEvent('tab_hidden') }
    const onBlur  = () => sendIntegrityEvent('window_blur')
    const onPaste = () => sendIntegrityEvent('clipboard_paste')
    const onCopy  = () => sendIntegrityEvent('clipboard_copy')
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('blur', onBlur)
    document.addEventListener('paste', onPaste)
    document.addEventListener('copy', onCopy)

    // Split screen detection — window width < 80% of screen
    let splitWarnings = 0
    const checkSplit = () => {
      if (stage !== 'interview') return
      const ratio = window.innerWidth / screen.width
      if (ratio < 0.8) {
        splitWarnings++
        sendIntegrityEvent('split_screen')
        if (splitWarnings >= 3) {
          sendIntegrityEvent('split_screen_termination')
        }
      }
    }
    window.addEventListener('resize', checkSplit)

    // DOM overlay detection — AI answer overlays injected into page
    const scanOverlays = () => {
      document.querySelectorAll('div, iframe, section').forEach(el => {
        const style = getComputedStyle(el)
        const z = parseInt(style.zIndex || '0')
        const w = el.clientWidth
        const h = el.clientHeight
        if (z > 9000 && w > 200 && h > 100 && style.position === 'fixed') {
          sendIntegrityEvent('dom_overlay')
        }
      })
    }

    // AI extension detection — known AI assistant extensions
    const scanExtensions = () => {
      const selectors = [
        '[data-grammarly-part]', '#grammarly-mirror-div',
        '.cib-serp-main', '#copilot-sidebar',
        '[class*="chatgpt"]', '[class*="claude"]',
        '[id*="bard"]', '[class*="gemini"]',
        '.notion-ai-panel', '[data-testid="ai-assist"]',
      ]
      selectors.forEach(sel => {
        const found = document.querySelectorAll(sel)
        if (found.length > 0) {
          sendIntegrityEvent('ai_extension_detected')
        }
      })
    }

    // DevTools detection — window size difference
    const checkDevtools = () => {
      const threshold = 160
      if (window.outerWidth - window.innerWidth > threshold || window.outerHeight - window.innerHeight > threshold) {
        sendIntegrityEvent('devtools_opened')
      }
    }

    // Run scans periodically
    const scanInterval = setInterval(scanExtensions, 5000)
    const overlayInterval = setInterval(scanOverlays, 3000)
    const devtoolsInterval = setInterval(checkDevtools, 5000)

    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('blur', onBlur)
      document.removeEventListener('paste', onPaste)
      document.removeEventListener('copy', onCopy)
      window.removeEventListener('resize', checkSplit)
      clearInterval(scanInterval)
      clearInterval(overlayInterval)
      clearInterval(devtoolsInterval)
    }
  }, [sendIntegrityEvent, stage])

  const isEnded      = wsStatus === 'ended'
  const isConnecting = wsStatus === 'connecting' || wsStatus === 'reconnecting'
  const isLive       = wsStatus === 'connected'

  const DOMAIN_OPTIONS: { key: DomainKey; label: string; desc: string }[] = [
    { key: 'PHYSICAL_DESIGN', label: 'Physical Design', desc: 'Floorplan, CTS, timing closure, routing' },
    { key: 'ANALOG_LAYOUT', label: 'Analog Layout', desc: 'Device matching, parasitics, DRC/LVS' },
    { key: 'DESIGN_VERIFICATION', label: 'Design Verification', desc: 'UVM, coverage, formal, SVA' },
  ]

  // ── Stage 1: Setup (resume + domain) ──
  if (stage === 'setup') {
    return (
      <div style={{
        minHeight: '100dvh', background: 'var(--bg-canvas)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          background: 'var(--bg-0)', border: '1px solid var(--border-1)',
          borderRadius: 16, padding: '40px 48px', maxWidth: 520, width: '100%',
          boxShadow: 'var(--shadow-lg)',
        }}>
          {/* Step indicator */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 28 }}>
            <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--accent)' }} />
            <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--border-1)' }} />
          </div>

          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 24, color: 'var(--text-0)', marginBottom: 6 }}>
            Interview Setup
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 24 }}>
            Upload your resume and select a domain. The interviewer will personalize questions based on your experience.
          </p>

          {/* Resume upload */}
          <label style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '24px', border: '2px dashed var(--border-2)', borderRadius: 12,
            cursor: 'pointer', marginBottom: 20,
            background: resumeText ? 'rgba(34,197,94,0.05)' : 'var(--bg-1)',
            borderColor: resumeText ? 'rgba(34,197,94,0.3)' : 'var(--border-2)',
          }}>
            <input
              type="file"
              accept=".txt,.pdf,.doc,.docx"
              style={{ display: 'none' }}
              onChange={e => e.target.files?.[0] && handleResumeFile(e.target.files[0])}
            />
            {resumeLoading ? (
              <span style={{ fontSize: 13, color: 'var(--text-2)' }}>Reading file...</span>
            ) : resumeText ? (
              <span style={{ fontSize: 13, color: 'var(--green, #22c55e)' }}>
                {resumeFile?.name} ({Math.round(resumeText.length / 1024)}KB)
              </span>
            ) : (
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 28, marginBottom: 6 }}>📄</div>
                <span style={{ fontSize: 13, color: 'var(--text-3)' }}>Drop resume here or click to upload</span>
              </div>
            )}
          </label>

          {/* Domain selection */}
          <div style={{ marginBottom: 24 }}>
            <p style={{ fontSize: 12, color: 'var(--text-3)', marginBottom: 8, fontFamily: 'var(--font-mono)', letterSpacing: '0.05em', textTransform: 'uppercase' }}>Domain</p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {DOMAIN_OPTIONS.map(d => (
                <button
                  key={d.key}
                  onClick={() => setSelectedDomain(d.key)}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 12,
                    padding: '12px 16px', border: '1px solid',
                    borderColor: selectedDomain === d.key ? 'var(--accent)' : 'var(--border-1)',
                    background: selectedDomain === d.key ? 'rgba(99,102,241,0.05)' : 'var(--bg-1)',
                    borderRadius: 10, cursor: 'pointer', textAlign: 'left',
                    fontFamily: 'var(--font-body)',
                  }}
                >
                  <div style={{
                    width: 16, height: 16, borderRadius: '50%', flexShrink: 0,
                    border: `2px solid ${selectedDomain === d.key ? 'var(--accent)' : 'var(--border-2)'}`,
                    background: selectedDomain === d.key ? 'var(--accent)' : 'transparent',
                  }} />
                  <div>
                    <p style={{ fontSize: 13, color: 'var(--text-0)', fontWeight: 500 }}>{d.label}</p>
                    <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>{d.desc}</p>
                  </div>
                </button>
              ))}
            </div>
          </div>

          <button
            disabled={!resumeText || creating}
            onClick={handleCreateSession}
            style={{
              width: '100%', padding: '13px 20px',
              background: (!resumeText || creating) ? 'var(--text-3)' : 'var(--accent)',
              color: '#fff', border: 'none', borderRadius: 10,
              fontSize: 14, fontWeight: 500, cursor: (!resumeText || creating) ? 'not-allowed' : 'pointer',
              fontFamily: 'var(--font-body)',
            }}
          >
            {creating ? 'Creating session...' : !resumeText ? 'Upload resume to continue' : 'Next — Camera & Mic'}
          </button>
        </div>
      </div>
    )
  }

  // ── Stage 2: Permissions (camera + mic) ──
  if (stage === 'permissions') {
    return (
      <div style={{
        minHeight: '100dvh', background: 'var(--bg-canvas)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <div style={{
          background: 'var(--bg-0)', border: '1px solid var(--border-1)',
          borderRadius: 16, padding: '40px 48px', maxWidth: 520, width: '100%',
          textAlign: 'center', boxShadow: 'var(--shadow-lg)',
        }}>
          {/* Step indicator */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 28 }}>
            <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--accent)' }} />
            <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'var(--accent)' }} />
          </div>

          <div style={{
            width: 56, height: 56, borderRadius: 14, background: 'var(--accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            margin: '0 auto 20px',
          }}>
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2">
              <path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
            </svg>
          </div>

          <h2 style={{ fontFamily: 'var(--font-display)', fontSize: 22, color: 'var(--text-0)', marginBottom: 8 }}>
            Camera & Microphone
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-2)', marginBottom: 24, lineHeight: 1.6 }}>
            Your camera maintains interview integrity. Your microphone captures your answers. Both are required.
          </p>

          <div style={{
            width: '100%', aspectRatio: '16/9', background: 'var(--bg-2)',
            borderRadius: 10, overflow: 'hidden', marginBottom: 20,
            border: '1px solid var(--border-1)',
          }}>
            <video
              ref={videoRef}
              autoPlay
              muted
              playsInline
              style={{ width: '100%', height: '100%', objectFit: 'cover', transform: 'scaleX(-1)' }}
            />
          </div>

          {permError && (
            <p style={{ fontSize: 12, color: 'var(--red, #ef4444)', marginBottom: 16, padding: '10px 14px', background: 'rgba(239,68,68,0.05)', borderRadius: 8, border: '1px solid rgba(239,68,68,0.2)' }}>
              {permError}
            </p>
          )}

          <button
            onClick={requestPermissions}
            style={{
              width: '100%', padding: '13px 20px',
              background: 'var(--accent)', color: '#fff', border: 'none',
              borderRadius: 10, fontSize: 14, fontWeight: 500,
              cursor: 'pointer', fontFamily: 'var(--font-body)',
            }}
          >
            Allow & Start Interview
          </button>

          <p style={{ fontSize: 11, color: 'var(--text-4)', marginTop: 12 }}>
            {domain} Interview
          </p>
        </div>
      </div>
    )
  }

  // ── Stage 3: Interview ──

  return (
    <div style={{
      minHeight: '100dvh', background: 'var(--bg-canvas)',
      display: 'flex', flexDirection: 'column',
    }}>
      {/* ── Top bar ── */}
      <header style={{
        height: 52, flexShrink: 0,
        background: 'var(--bg-0)', borderBottom: '1px solid var(--border-0)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '0 24px',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          {/* Brand dot */}
          <span style={{
            width: 24, height: 24, borderRadius: 6, background: 'var(--accent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
          }}>
            <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
              <path d="M2 5H8M5 2V8" stroke="white" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          </span>

          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-3)', letterSpacing: '0.06em' }}>
            {domain.toUpperCase()}
          </span>

          {isLive && <ModeTag mode={mode} />}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {turnNumber > 0 && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-4)', letterSpacing: '0.06em' }}>
              TURN {turnNumber}
            </span>
          )}
          <ConnectionStatus status={wsStatus} />
          <button
            onClick={() => { disconnect(); navigate('/dashboard') }}
            style={{
              background: 'none', border: '1px solid var(--border-2)',
              color: 'var(--text-3)', borderRadius: 'var(--r-md)',
              padding: '5px 12px', fontSize: 11, cursor: 'pointer',
              fontFamily: 'var(--font-body)', letterSpacing: '0.02em',
            }}
          >
            End session
          </button>
        </div>
      </header>

      {/* ── Reconnecting banner ── */}
      {wsStatus === 'reconnecting' && (
        <div style={{
          background: 'var(--yellow-bg)', borderBottom: '1px solid var(--yellow-border)',
          padding: '8px 24px', fontSize: 12, color: 'var(--yellow)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--yellow)', animation: 'pulse 1.2s infinite', flexShrink: 0 }} />
          Connection became unstable. Reconnecting automatically — your session is preserved.
        </div>
      )}

      {/* ── Reconnect success banner ── */}
      {reconnectMessage && wsStatus === 'connected' && (
        <div style={{
          background: 'rgba(34,197,94,0.06)', borderBottom: '1px solid rgba(34,197,94,0.15)',
          padding: '8px 24px', fontSize: 12, color: 'var(--green, #22c55e)',
          display: 'flex', alignItems: 'center', gap: 8,
          animation: 'fade-in 0.3s',
        }}>
          <span style={{ color: 'var(--green, #22c55e)' }}>&#10003;</span>
          {reconnectMessage}
        </div>
      )}

      {/* ── Session ended banner ── */}
      {isEnded && (
        <div style={{
          background: 'var(--green-bg)', borderBottom: '1px solid var(--green-border)',
          padding: '8px 24px', fontSize: 12, color: 'var(--green)',
          display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ color: 'var(--green)' }}>✓</span>
          Session complete — generating your report…
        </div>
      )}

      {/* ── Camera PIP ── */}
      <CameraPIP stream={mediaStream} />

      {/* ── Body ── */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 320px', overflow: 'hidden' }}>

        {/* Transcript pane */}
        <div style={{
          display: 'flex', flexDirection: 'column',
          borderRight: '1px solid var(--border-0)', overflow: 'hidden',
        }}>

          {/* Connecting state */}
          {isConnecting && (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
              <div style={{
                width: 48, height: 48, borderRadius: 14, background: 'var(--accent-8)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{ width: 22, height: 22, border: '2px solid var(--accent-25)', borderTopColor: 'var(--accent)', borderRadius: '50%', display: 'inline-block', animation: 'spin 0.8s linear infinite' }} />
              </div>
              <p style={{ fontSize: 14, color: 'var(--text-2)' }}>Connecting to session…</p>
              <p style={{ fontSize: 12, color: 'var(--text-4)', fontFamily: 'var(--font-mono)', letterSpacing: '0.04em' }}>{sessionId?.slice(0, 8)}</p>
            </div>
          )}

          {/* Live transcript */}
          {(isLive || isEnded) && (
            <div ref={transcriptRef} style={{
              flex: 1, overflowY: 'auto', padding: '28px 32px',
              display: 'flex', flexDirection: 'column', gap: 20,
            }}>

              {/* Previous turns */}
              {turns.map((turn, i) => (
                <div key={turn.turnNumber} style={{ animation: 'slide-up 0.25s var(--ease-dec)' }}>
                  {/* Interviewer */}
                  <div style={{ marginBottom: 14 }}>
                    <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--accent-dim)', marginBottom: 7 }}>
                      Interviewer · Turn {turn.turnNumber}
                    </p>
                    <p style={{ fontSize: 16, color: 'var(--text-0)', lineHeight: 1.75 }}>
                      {turn.question}
                    </p>
                  </div>
                  {/* Answer */}
                  {turn.answer && (
                    <div style={{
                      background: 'var(--bg-1)', borderRadius: 'var(--r-lg)',
                      padding: '14px 18px', borderLeft: '2px solid var(--border-2)',
                    }}>
                      <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-4)', marginBottom: 6 }}>
                        Your response
                      </p>
                      <p style={{ fontSize: 14, color: 'var(--text-2)', lineHeight: 1.7 }}>{turn.answer}</p>
                    </div>
                  )}
                </div>
              ))}

              {/* Current streaming question */}
              {(isStreaming || currentQuestion) && (
                <div style={{ animation: 'slide-up 0.2s var(--ease-dec)' }}>
                  <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--accent-dim)', marginBottom: 7 }}>
                    Interviewer
                  </p>
                  <p style={{ fontSize: 16, color: 'var(--text-0)', lineHeight: 1.75 }}>
                    {currentQuestion}
                    {isStreaming && (
                      <span style={{
                        display: 'inline-block', width: 2, height: '1.1em',
                        background: 'var(--accent)', marginLeft: 2, verticalAlign: 'text-bottom',
                        animation: 'cursor-blink 1s ease-in-out infinite',
                      }} />
                    )}
                  </p>
                </div>
              )}

              {/* STT feedback */}
              {audioState === 'thinking' && transcript && (
                <div style={{ animation: 'fade-in 0.15s' }}>
                  <p style={{ fontSize: 10, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-4)', marginBottom: 6 }}>
                    Transcribing
                  </p>
                  <p style={{ fontSize: 13, color: 'var(--text-3)', fontStyle: 'italic', lineHeight: 1.6 }}>
                    "{transcript}"
                  </p>
                </div>
              )}

              {/* Empty state */}
              {!turns.length && !currentQuestion && !isStreaming && audioState === 'silence' && (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8, padding: '40px 0' }}>
                  <p style={{ fontSize: 14, color: 'var(--text-2)' }}>Session ready</p>
                  <p style={{ fontSize: 12, color: 'var(--text-4)' }}>The interviewer will open with a question shortly.</p>
                </div>
              )}
            </div>
          )}

          {/* ── Audio controls bar ── */}
          {isLive && (
            <div style={{
              height: 80, flexShrink: 0,
              background: 'var(--bg-0)', borderTop: '1px solid var(--border-0)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16,
              padding: '0 32px',
            }}>
              <MicButton audioState={audioState} />
              {audioState === 'speaking' && (
                <button
                  onClick={bargeIn}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 7,
                    background: 'var(--accent-8)', color: 'var(--accent-dim)',
                    border: '1px solid var(--accent-25)', borderRadius: 'var(--r-full)',
                    padding: '8px 18px', fontSize: 12, cursor: 'pointer',
                    fontFamily: 'var(--font-body)', fontWeight: 500,
                    animation: 'fade-in 0.2s',
                  }}
                >
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--accent)', animation: 'pulse 1s infinite' }} />
                  Interrupt
                </button>
              )}
            </div>
          )}
        </div>

        {/* ── Right status panel ── */}
        <div style={{
          background: 'var(--bg-0)', display: 'flex', flexDirection: 'column', overflow: 'hidden',
        }}>
          {/* Status */}
          <div style={{ padding: '24px 20px', borderBottom: '1px solid var(--border-0)' }}>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-4)', marginBottom: 16 }}>
              Live status
            </p>
            <AudioStateDisplay state={audioState} />
          </div>

          {/* Mode info */}
          {isLive && (
            <div style={{ padding: '20px', borderBottom: '1px solid var(--border-0)' }}>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-4)', marginBottom: 12 }}>
                Interview mode
              </p>
              <ModeTag mode={mode} />
              <p style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 10, lineHeight: 1.6 }}>
                {MODE_DESCRIPTIONS[mode]}
              </p>
            </div>
          )}

          {/* Turn history mini-list */}
          {turns.length > 0 && (
            <div style={{ padding: '20px', flex: 1, overflowY: 'auto' }}>
              <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-4)', marginBottom: 14 }}>
                Turns
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {turns.map(t => (
                  <div key={t.turnNumber} style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '8px 12px', background: 'var(--bg-1)',
                    borderRadius: 'var(--r-md)',
                  }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-4)', width: 20 }}>
                      {String(t.turnNumber).padStart(2,'0')}
                    </span>
                    <ModeTag mode={t.mode} />
                    {t.avgScore != null && (
                      <span style={{ marginLeft: 'auto', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-3)' }}>
                        {Number(t.avgScore).toFixed(1)}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Mode descriptions ──────────────────────────────────────────────────────────
const MODE_DESCRIPTIONS: Record<string, string> = {
  PROBING:       'Establishing baseline. Broad, exploratory questions.',
  DEEPENING:     'Strong performance detected. Pursuing depth and nuance.',
  ESCALATING:    'Gaps identified. Moving to harder territory.',
  PRESSURE:      'Testing under difficulty. High-stakes follow-ups.',
  RECOVERING:    'Giving space to recover with structured guidance.',
  TRANSITIONING: 'Moving to a new topic area.',
}

// ── Audio state display ────────────────────────────────────────────────────────
function AudioStateDisplay({ state }: { state: string }) {
  const configs: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
    silence: {
      label: 'Waiting',
      icon: <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--text-4)', display: 'block' }} />,
      color: 'var(--text-3)',
    },
    listening: {
      label: 'Listening',
      icon: <span style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--green)', display: 'block', animation: 'pulse-scale 1.2s ease-in-out infinite' }} />,
      color: 'var(--green)',
    },
    thinking: {
      label: 'Processing',
      icon: <ThinkingDots />,
      color: 'var(--accent-dim)',
    },
    speaking: {
      label: 'Interviewer speaking',
      icon: <Waveform />,
      color: 'var(--accent-dim)',
    },
  }
  const c = configs[state] || configs.silence
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      {c.icon}
      <span style={{ fontSize: 13, color: c.color, fontWeight: 500 }}>{c.label}</span>
    </div>
  )
}

function Waveform() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 2, height: 16 }}>
      {[0.25, 0.6, 1, 0.75, 0.45, 0.9, 0.55].map((h, i) => (
        <span key={i} style={{
          width: 2, height: 16,
          background: 'var(--accent)',
          borderRadius: 1, transformOrigin: 'bottom',
          animation: `waveform 0.8s ${i * 0.1}s ease-in-out infinite`,
          transform: `scaleY(${h})`,
        }} />
      ))}
    </div>
  )
}

function ThinkingDots() {
  return (
    <div style={{ display: 'flex', gap: 3, alignItems: 'center' }}>
      {[0, 0.15, 0.3].map(delay => (
        <span key={delay} style={{
          width: 5, height: 5, borderRadius: '50%', background: 'var(--accent)',
          animation: `pulse 1.2s ${delay}s ease-in-out infinite`,
        }} />
      ))}
    </div>
  )
}

// ── Camera PIP (picture-in-picture) ──────────────────────────────────────────
function CameraPIP({ stream }: { stream: MediaStream | null }) {
  const pipRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    if (pipRef.current && stream) {
      pipRef.current.srcObject = stream
    }
  }, [stream])

  if (!stream) return null

  return (
    <div style={{
      position: 'fixed', bottom: 80, right: 24,
      width: 180, height: 135, borderRadius: 10,
      overflow: 'hidden', border: '1.5px solid var(--border-1, #e0e0e0)',
      background: '#1a1a1a',
      boxShadow: '0 4px 20px rgba(0,0,0,0.12)',
      zIndex: 50,
    }}>
      {/* Header overlay */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0,
        padding: '5px 8px',
        background: 'linear-gradient(180deg, rgba(0,0,0,0.5) 0%, transparent 100%)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        zIndex: 1,
      }}>
        <span style={{
          fontFamily: 'var(--font-mono, monospace)', fontSize: 8,
          letterSpacing: '0.1em', textTransform: 'uppercase' as const,
          color: 'rgba(255,255,255,0.8)',
        }}>
          monitoring
        </span>
        <span style={{
          width: 6, height: 6, borderRadius: '50%',
          background: '#e53e3e',
          animation: 'pulse 1.5s infinite',
        }} />
      </div>

      <video
        ref={pipRef}
        autoPlay
        muted
        playsInline
        style={{
          width: '100%', height: '100%',
          objectFit: 'cover', transform: 'scaleX(-1)',
        }}
      />
    </div>
  )
}

// ── Mic button ─────────────────────────────────────────────────────────────────
function MicButton({ audioState }: { audioState: string }) {
  const isListening = audioState === 'listening'
  return (
    <div style={{ position: 'relative', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      {isListening && (
        <>
          <span style={{
            position: 'absolute', width: 52, height: 52, borderRadius: '50%',
            border: '1.5px solid var(--accent)',
            animation: 'ring-out 1.4s ease-out infinite',
          }} />
          <span style={{
            position: 'absolute', width: 52, height: 52, borderRadius: '50%',
            border: '1.5px solid var(--accent)',
            animation: 'ring-out 1.4s 0.5s ease-out infinite',
          }} />
        </>
      )}
      <div style={{
        width: 44, height: 44, borderRadius: '50%',
        background: isListening ? 'var(--accent)' : 'var(--bg-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background var(--dur-std)',
        boxShadow: isListening ? '0 0 0 3px var(--accent-15)' : undefined,
        zIndex: 1,
      }}>
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <rect x="5.5" y="1" width="5" height="8" rx="2.5" fill={isListening ? '#fff' : 'var(--text-3)'} />
          <path d="M2.5 8C2.5 11.038 4.962 13.5 8 13.5C11.038 13.5 13.5 11.038 13.5 8"
            stroke={isListening ? '#fff' : 'var(--text-3)'} strokeWidth="1.5" strokeLinecap="round" fill="none" />
          <path d="M8 13.5V15.5" stroke={isListening ? '#fff' : 'var(--text-3)'} strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </div>
    </div>
  )
}
