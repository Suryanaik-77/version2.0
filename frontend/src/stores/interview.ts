/**
 * stores/interview.ts — Interview session real-time state.
 *
 * Fixes applied vs previous version:
 *  1. Event type strings now match backend WSEventType enum exactly (uppercase)
 *  2. Binary WebSocket frames (audio) handled via socket.binaryType = 'arraybuffer'
 *  3. Web Audio API playback for MP3 audio from OpenAI TTS
 *  4. Microphone capture + PCM streaming to backend VAD
 *  5. STT_FINAL event correctly maps transcript
 *  6. TURN_COMPLETE maps to finishTurn with correct payload shape
 *  7. HEARTBEAT sends 'HEARTBEAT' not 'heartbeat'
 *  8. BARGE_IN sends 'BARGE_IN' not 'barge_in'
 */
import { create } from 'zustand'
import { useAuth } from './auth'

export type AudioState = 'silence' | 'listening' | 'thinking' | 'speaking'
export type WsStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'ended'
export type InterviewMode =
  | 'PROBING' | 'DEEPENING' | 'ESCALATING'
  | 'PRESSURE' | 'RECOVERING' | 'TRANSITIONING'

export interface TurnRecord {
  turnNumber: number
  question: string
  answer: string
  mode: InterviewMode
  evalScores?: Record<string, number>
  avgScore?: number
}

interface InterviewState {
  sessionId: string | null
  wsStatus: WsStatus
  audioState: AudioState
  mode: InterviewMode
  turnNumber: number
  currentQuestion: string
  transcript: string
  isStreaming: boolean
  turns: TurnRecord[]
  domain: string | null
  reconnectAttempt: number
  reconnectTimer: number | null
  reconnectMessage: string
  ws: WebSocket | null

  connect: (sessionId: string) => void
  disconnect: () => void
  bargeIn: () => void
  appendToken: (token: string) => void
  finishTurn: (turn: TurnRecord) => void
  setAudioState: (state: AudioState) => void
  setMode: (mode: InterviewMode) => void
  setTranscript: (t: string) => void
  reset: () => void
}

const MAX_RECONNECT_ATTEMPTS = 5
const RECONNECT_BASE_MS = 1000

const WS_BASE: string =
  typeof import.meta !== 'undefined'
    ? ((import.meta as any).env?.VITE_WS_URL ||
       (typeof window !== 'undefined'
         ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
         : 'ws://localhost:8000'))
    : 'ws://localhost:8000'

// ── Audio playback (Web Audio API) ────────────────────────────────────────────

let _audioCtx: AudioContext | null = null
let _audioQueue: ArrayBuffer[] = []
let _isPlaying = false

function getAudioCtx(): AudioContext {
  if (!_audioCtx || _audioCtx.state === 'closed') {
    _audioCtx = new AudioContext()
  }
  // Resume if suspended (browser autoplay policy)
  if (_audioCtx.state === 'suspended') {
    _audioCtx.resume()
  }
  return _audioCtx
}

async function enqueueAudio(data: ArrayBuffer): Promise<void> {
  _audioQueue.push(data.slice(0)) // copy — don't hold reference to original
  if (!_isPlaying) {
    _isPlaying = true
    _drainAudioQueue()
  }
}

async function _drainAudioQueue(): Promise<void> {
  const ctx = getAudioCtx()
  while (_audioQueue.length > 0) {
    const chunk = _audioQueue.shift()!
    try {
      const decoded = await ctx.decodeAudioData(chunk)
      await new Promise<void>((resolve) => {
        const src = ctx.createBufferSource()
        src.buffer = decoded
        src.connect(ctx.destination)
        src.onended = () => resolve()
        src.start(0)
      })
    } catch {
      // Skip malformed chunk — don't crash pipeline
    }
  }
  _isPlaying = false
  // All audio played — NOW start listening for candidate
  useInterview.setState({ audioState: 'silence' })
  startListening()
}

function stopAudio(): void {
  _audioQueue = []
  _isPlaying = false
  try { _audioCtx?.close() } catch (_) {}
  _audioCtx = null
}

// ── Browser-side VAD + Chunked Recording ─────────────────────────────────────

const SILENCE_THRESHOLD = 15
const SILENCE_TIMEOUT = 1200
const MAX_CHUNK_MS = 30000

let _micStream: MediaStream | null = null
let _recorder: MediaRecorder | null = null
let _chunks: Blob[] = []
let _micCtx: AudioContext | null = null
let _analyser: AnalyserNode | null = null
let _silenceStart: number | null = null
let _speechDetected = false
let _recStart = 0
let _checkTimer: number | null = null
let _capturing = false
let _recording = false
let _ws: WebSocket | null = null

async function _initMicStream(ws: WebSocket): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) return
  _ws = ws
  _capturing = true
  try {
    _micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true }
    })
    _micCtx = new AudioContext()
    const src = _micCtx.createMediaStreamSource(_micStream)
    _analyser = _micCtx.createAnalyser()
    _analyser.fftSize = 256
    src.connect(_analyser)
    // Don't start recording — wait for startListening()
  } catch (err) {
    console.error('[mic] init failed:', err)
    _capturing = false
  }
}

function startListening(): void {
  // Called AFTER AI finishes speaking — start recording candidate
  if (!_capturing || !_micStream || !_analyser || !_ws || _recording) return
  _recording = true
  useInterview.setState({ audioState: 'listening' })
  _beginRecording()
}

function stopListening(): void {
  // Called WHEN AI starts speaking — stop recording
  _recording = false
  if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
  if (_recorder && _recorder.state === 'recording') {
    // Discard current recording (it's AI voice or noise)
    _chunks = []
    _speechDetected = false
    try { _recorder.stop() } catch {}
  }
}

function _beginRecording(): void {
  if (!_micStream || !_analyser || !_ws) return

  _chunks = []
  _speechDetected = false
  _silenceStart = null
  _recStart = Date.now()

  // Use webm — same as monolith, OpenAI accepts it directly
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : 'audio/webm'
  try {
    _recorder = mimeType
      ? new MediaRecorder(_micStream, { mimeType })
      : new MediaRecorder(_micStream)
  } catch {
    _recorder = new MediaRecorder(_micStream)
  }

  _recorder.ondataavailable = (e) => {
    if (e.data.size > 0) _chunks.push(e.data)
  }

  _recorder.onstop = () => {
    if (_chunks.length === 0 || !_speechDetected || !_recording) {
      _chunks = []
      return
    }
    const blob = new Blob(_chunks, { type: 'audio/webm' })
    _chunks = []
    const dur = Date.now() - _recStart

    useInterview.setState({ audioState: 'thinking' })

    if (_ws && _ws.readyState === WebSocket.OPEN) {
      // Send metadata as text frame first, then audio as binary frame
      // Eliminates base64 encoding overhead (50-100ms) and 33% size bloat
      _ws.send(JSON.stringify({
        type: 'AUDIO_META',
        format: 'webm',
        duration_ms: dur,
      }))
      // Binary frame — zero encoding, zero size bloat
      blob.arrayBuffer().then(buf => {
        if (_ws && _ws.readyState === WebSocket.OPEN) {
          _ws.send(buf)
        }
      })
    }
    if (_recording) setTimeout(_beginRecording, 50)
  }

  _recorder.start(100)  // 100ms chunks — reduces hidden buffer delay at stop time

  // Silence detection
  const buf = new Uint8Array(_analyser.frequencyBinCount)
  if (_checkTimer) clearInterval(_checkTimer)
  _checkTimer = window.setInterval(() => {
    if (!_analyser || !_recorder || _recorder.state !== 'recording') return

    _analyser.getByteFrequencyData(buf)
    const avg = buf.reduce((a, b) => a + b, 0) / buf.length
    const elapsed = Date.now() - _recStart

    if (avg >= SILENCE_THRESHOLD) {
      _speechDetected = true
      _silenceStart = null
      useInterview.setState({ audioState: 'listening' })
    } else if (_speechDetected) {
      if (!_silenceStart) {
        _silenceStart = Date.now()
      } else if (Date.now() - _silenceStart >= SILENCE_TIMEOUT) {
        // 2s silence after speech → send
        useInterview.setState({ audioState: 'thinking' })
        try { _recorder.stop() } catch {}
        if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
        return
      }
    }

    // 30s auto-send
    if (elapsed >= MAX_CHUNK_MS && _speechDetected) {
      useInterview.setState({ audioState: 'thinking' })
      try { _recorder.stop() } catch {}
      if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
    }
  }, 100)
}

function stopMicCapture(): void {
  _capturing = false
  if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
  try { if (_recorder && _recorder.state !== 'inactive') _recorder.stop() } catch {}
  try { _micCtx?.close() } catch {}
  try { _micStream?.getTracks().forEach(t => t.stop()) } catch {}
  _recorder = null
  _micCtx = null
  _micStream = null
  _analyser = null
  _ws = null
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useInterview = create<InterviewState>((set, get) => ({
  sessionId: null,
  wsStatus: 'disconnected',
  audioState: 'silence',
  mode: 'PROBING',
  turnNumber: 0,
  currentQuestion: '',
  transcript: '',
  isStreaming: false,
  turns: [],
  domain: null,
  reconnectAttempt: 0,
  reconnectTimer: null,
  reconnectMessage: '',
  ws: null,

  connect: (sessionId: string) => {
    const { ws } = get()
    if (ws) ws.close(1000)

    const token = useAuth.getState().accessToken
    if (!token) return

    const url = `${WS_BASE}/ws/${sessionId}?token=${token}`
    set({ wsStatus: 'connecting', sessionId })

    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'  // receive audio as ArrayBuffer, not Blob

    socket.onopen = () => {
      set({ wsStatus: 'connected', reconnectAttempt: 0, ws: socket })
      socket.send(JSON.stringify({ type: 'HEARTBEAT' }))
      // Don't start recording here — wait for AI to finish speaking first
      // Just init the mic stream so it's ready
      _initMicStream(socket)
    }

    socket.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame = TTS audio chunk — stop mic immediately
        stopListening()
        enqueueAudio(event.data)
        set({ audioState: 'speaking' })
        return
      }
      try {
        handleMessage(JSON.parse(event.data as string))
      } catch (_) {}
    }

    socket.onclose = (event: CloseEvent) => {
      stopMicCapture()
      stopAudio()

      const { wsStatus, reconnectAttempt } = get()
      if (wsStatus === 'ended' || event.code === 1000) {
        set({ wsStatus: 'disconnected', ws: null })
        return
      }

      if (reconnectAttempt < MAX_RECONNECT_ATTEMPTS) {
        const delay = RECONNECT_BASE_MS * Math.pow(2, reconnectAttempt)
        set({ wsStatus: 'reconnecting', ws: null })
        const timer = window.setTimeout(() => {
          set(s => ({ reconnectAttempt: s.reconnectAttempt + 1 }))
          const { sessionId } = get()
          if (sessionId) get().connect(sessionId)
        }, delay)
        set({ reconnectTimer: timer })
      } else {
        set({ wsStatus: 'disconnected', ws: null })
      }
    }

    socket.onerror = () => { /* onclose handles reconnect */ }

    set({ ws: socket })
  },

  disconnect: () => {
    const { ws, reconnectTimer } = get()
    if (reconnectTimer) window.clearTimeout(reconnectTimer)
    stopMicCapture()
    stopAudio()
    if (ws) ws.close(1000)
    set({ wsStatus: 'disconnected', ws: null })
  },

  bargeIn: () => {
    const { ws } = get()
    if (ws?.readyState === WebSocket.OPEN) {
      stopAudio()  // stop playback immediately
      ws.send(JSON.stringify({ type: 'BARGE_IN' }))
      set({ audioState: 'listening', isStreaming: false, currentQuestion: '' })
    }
  },

  appendToken: (token: string) => {
    set(s => ({
      currentQuestion: s.currentQuestion + token,
      isStreaming: true,
    }))
  },

  finishTurn: (turn: TurnRecord) => {
    set(s => ({
      turns: [...s.turns, turn],
      turnNumber: turn.turnNumber,
      isStreaming: false,
    }))
  },

  setAudioState:  (state: AudioState)  => set({ audioState: state }),
  setMode:        (mode: InterviewMode) => set({ mode }),
  setTranscript:  (t: string)           => set({ transcript: t }),

  reset: () => {
    const { ws } = get()
    stopMicCapture()
    stopAudio()
    if (ws) ws.close(1000)
    set({
      sessionId: null, wsStatus: 'disconnected', audioState: 'silence',
      mode: 'PROBING', turnNumber: 0, currentQuestion: '', transcript: '',
      isStreaming: false, turns: [], domain: null,
      reconnectAttempt: 0, reconnectTimer: null, reconnectMessage: '', ws: null,
    })
  },
}))

// ── Message handler — event types match backend WSEventType enum (uppercase) ───

function handleMessage(msg: Record<string, unknown>) {
  const store = useInterview.getState()
  // Events carry data in a 'payload' wrapper from backend WSEvent model
  const p = (msg.payload || msg) as any

  switch (msg.type) {

    case 'TURN_START':
      stopListening()  // Stop mic immediately — AI is about to speak
      store.setAudioState('thinking')
      if (p.mode) store.setMode(p.mode as InterviewMode)
      useInterview.setState({ currentQuestion: '', transcript: '' })
      break

    case 'TURN_COMPLETE':
      // Save turn to history and clear current question
      store.finishTurn({
        turnNumber: p.turn_number ?? store.turnNumber + 1,
        question:   store.currentQuestion,
        answer:     store.transcript,
        mode:       store.mode,
      })
      useInterview.setState({ currentQuestion: '', audioState: 'silence' })
      break

    case 'STT_PARTIAL':
      store.setAudioState('thinking')
      store.setTranscript(p.fragment || '')
      break

    case 'STT_FINAL':
      store.setTranscript(p.transcript || '')
      store.setAudioState('thinking')
      break

    case 'INTERVIEWER_CHUNK':
      // Audio bytes arrive as binary frame (handled in onmessage above)
      // This JSON frame carries question text
      if (p.text && p.is_final) {
        useInterview.setState({ currentQuestion: p.text })
      }
      store.setAudioState('speaking')
      stopListening()  // Stop recording while AI speaks
      break

    case 'INTERVIEWER_DONE':
      // Audio sent to queue — don't start listening yet.
      // startListening() is called by _drainAudioQueue() when playback actually finishes.
      break

    case 'SESSION_START':
      store.setAudioState('silence')
      break

    case 'SESSION_END':
      useInterview.setState({ wsStatus: 'ended' })
      store.disconnect()
      break

    case 'HEARTBEAT_ACK':
      break

    case 'BARGE_IN':
      store.setAudioState('listening')
      break

    case 'STATE_CHANGE':
      if (p.new_mode) store.setMode(p.new_mode as InterviewMode)
      break

    case 'RECONNECTED':
      // Session restored after reconnect — update state from server
      if (p.turn_count) useInterview.setState({ turnNumber: p.turn_count })
      if (p.mode) store.setMode(p.mode as InterviewMode)
      useInterview.setState({
        audioState: 'silence',
        reconnectMessage: p.message || 'Session restored. Continuing interview.',
      })
      // Clear the reconnect message after 5 seconds
      setTimeout(() => useInterview.setState({ reconnectMessage: '' }), 5000)
      break

    case 'ERROR':
      console.error('[WS] server error:', p.message || p.error)
      break

    default:
      break
  }
}
