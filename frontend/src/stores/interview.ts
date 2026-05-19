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
  // All audio drained — tell store we're done speaking
  useInterview.setState({ audioState: 'silence' })
}

function stopAudio(): void {
  _audioQueue = []
  _isPlaying = false
  try { _audioCtx?.close() } catch (_) {}
  _audioCtx = null
}

// ── Microphone capture ────────────────────────────────────────────────────────

let _micStream: MediaStream | null = null
let _micProcessor: ScriptProcessorNode | null = null
let _micAudioCtx: AudioContext | null = null

async function startMicCapture(ws: WebSocket): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) return
  try {
    _micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: 16000,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
      }
    })
    _micAudioCtx = new AudioContext({ sampleRate: 16000 })
    const source = _micAudioCtx.createMediaStreamSource(_micStream)
    _micProcessor = _micAudioCtx.createScriptProcessor(4096, 1, 1)
    _micProcessor.onaudioprocess = (e: AudioProcessingEvent) => {
      if (ws.readyState !== WebSocket.OPEN) return
      const f32 = e.inputBuffer.getChannelData(0)
      const pcm = new Int16Array(f32.length)
      for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]))
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      ws.send(pcm.buffer)
    }
    source.connect(_micProcessor)
    _micProcessor.connect(_micAudioCtx.destination)
  } catch (err) {
    console.error('[mic] failed to start capture:', err)
  }
}

function stopMicCapture(): void {
  try {
    _micProcessor?.disconnect()
    _micAudioCtx?.close()
    _micStream?.getTracks().forEach(t => t.stop())
  } catch (_) {}
  _micProcessor = null
  _micAudioCtx = null
  _micStream = null
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
      startMicCapture(socket)
    }

    socket.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame = TTS audio chunk
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
      reconnectAttempt: 0, reconnectTimer: null, ws: null,
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
      store.setAudioState('thinking')
      if (p.mode) store.setMode(p.mode as InterviewMode)
      // Clear previous question for new turn
      useInterview.setState({ currentQuestion: '', transcript: '' })
      break

    case 'TURN_COMPLETE':
      // question text = accumulated currentQuestion; answer = last STT_FINAL transcript
      store.finishTurn({
        turnNumber: p.turn_number ?? store.turnNumber + 1,
        question:   store.currentQuestion,
        answer:     store.transcript,
        mode:       store.mode,
      })
      store.setAudioState('silence')
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
      break

    case 'INTERVIEWER_DONE':
      // All audio sent. Don't clear question — let TURN_START of next turn do that.
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
      store.setAudioState('silence')
      break

    case 'ERROR':
      console.error('[WS] server error:', p.message || p.error)
      break

    default:
      break
  }
}
