/**
 * stores/interview.ts — Interview session real-time state.
 *
 * Audio architecture (v2 — AudioWorklet streaming):
 *   Mic → AudioContext → AudioWorkletNode (pcm-processor)
 *       → 20ms PCM frames → WebSocket binary → backend → Deepgram streaming STT
 *
 * Key changes from v1:
 *   - MediaRecorder + blob upload replaced with AudioWorklet PCM streaming.
 *   - SILENCE_TIMEOUT (1200ms) eliminated — Deepgram handles endpointing server-side.
 *   - Gapless TTS playback via pre-scheduled Web Audio API buffers.
 *   - AudioContext lifecycle fixed — single instance per connection, clean teardown.
 *   - Fallback to MediaRecorder blob if AudioWorklet unavailable (old browsers).
 *
 * Latency improvement:
 *   Before: 1200ms silence wait + 350–1150ms batch STT = 1550–2350ms before qgen.
 *   After:  200ms Deepgram endpointing + ~0ms streaming STT = 200ms before qgen.
 */

import { create } from 'zustand'
import { useAuth } from './auth'

export type AudioState  = 'silence' | 'listening' | 'thinking' | 'speaking'
export type WsStatus    = 'disconnected' | 'connecting' | 'connected' | 'reconnecting' | 'ended'
export type InterviewMode =
  | 'PROBING' | 'DEEPENING' | 'ESCALATING'
  | 'PRESSURE' | 'RECOVERING' | 'TRANSITIONING'

export interface TurnRecord {
  turnNumber:  number
  question:    string
  answer:      string
  mode:        InterviewMode
  evalScores?: Record<string, number>
  avgScore?:   number
}

interface InterviewState {
  sessionId:        string | null
  wsStatus:         WsStatus
  audioState:       AudioState
  mode:             InterviewMode
  turnNumber:       number
  currentQuestion:  string
  transcript:       string
  isStreaming:      boolean
  turns:            TurnRecord[]
  domain:           string | null
  reconnectAttempt: number
  reconnectTimer:   number | null
  reconnectMessage: string
  ws:               WebSocket | null

  connect:      (sessionId: string) => void
  disconnect:   () => void
  bargeIn:      () => void
  appendToken:  (token: string) => void
  finishTurn:   (turn: TurnRecord) => void
  setAudioState:(state: AudioState) => void
  setMode:      (mode: InterviewMode) => void
  setTranscript:(t: string) => void
  reset:        () => void
}

const MAX_RECONNECT_ATTEMPTS = 5
const RECONNECT_BASE_MS      = 1000

// VAD visual threshold — frequency-domain energy (0–255 scale).
// Only used for UI indicator, NOT for triggering send (Deepgram handles endpointing).
const VAD_VISUAL_THRESHOLD = 15

const WS_BASE: string =
  typeof import.meta !== 'undefined'
    ? ((import.meta as any).env?.VITE_WS_URL ||
       (typeof window !== 'undefined'
         ? `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
         : 'ws://localhost:8000'))
    : 'ws://localhost:8000'


// ── TTS Audio Playback (Web Audio API — gapless scheduling) ──────────────────
//
// Architecture: each decoded AudioBuffer is scheduled at an absolute AudioContext
// timestamp immediately after the previous chunk. This eliminates inter-sentence
// gaps caused by sequential decode→play loops.

let _playCtx:           AudioContext | null = null
let _nextPlayTime:      number = 0   // absolute AudioContext time when last chunk ends
let _isPlaying:         boolean = false
let _decodeQueue:       ArrayBuffer[] = []
let _processingQueue:   boolean = false

async function getPlayCtx(): Promise<AudioContext> {
  if (!_playCtx || _playCtx.state === 'closed') {
    _playCtx = new AudioContext()
  }
  if (_playCtx.state === 'suspended') {
    await _playCtx.resume()
  }
  return _playCtx
}

/**
 * Enqueue an audio chunk for gapless playback.
 * Called for every binary WebSocket frame (one TTS sentence = one frame).
 */
async function enqueueAudio(data: ArrayBuffer): Promise<void> {
  _decodeQueue.push(data.slice(0))
  if (!_processingQueue) {
    _processingQueue = true
    _processDecodeQueue()
  }
}

/**
 * Drain the decode queue, scheduling each buffer at an exact future time.
 * Serialised so chunks play in arrival order even if decode speeds differ.
 */
async function _processDecodeQueue(): Promise<void> {
  const ctx = await getPlayCtx()

  while (_decodeQueue.length > 0) {
    const chunk = _decodeQueue.shift()!
    try {
      const buffer = await ctx.decodeAudioData(chunk)

      // Schedule immediately after the last chunk (gapless)
      const startAt  = Math.max(ctx.currentTime + 0.02, _nextPlayTime)
      _nextPlayTime  = startAt + buffer.duration
      _isPlaying     = true

      const src = ctx.createBufferSource()
      src.buffer = buffer
      src.connect(ctx.destination)
      src.start(startAt)

      // When this is the last chunk, transition to listening
      src.onended = () => {
        // Only transition if no more audio is queued or playing
        if (ctx.currentTime >= _nextPlayTime - 0.05 && _decodeQueue.length === 0) {
          _isPlaying = false
          useInterview.setState({ audioState: 'silence' })
          startListening()
        }
      }
    } catch {
      // Skip malformed audio chunk — don't crash pipeline
    }
  }

  _processingQueue = false
}

function stopAudio(): void {
  _decodeQueue  = []
  _isPlaying    = false
  _nextPlayTime = 0
  _processingQueue = false
  try { _playCtx?.close() } catch (_) {}
  _playCtx = null
}


// ── Microphone Capture — AudioWorklet PCM Streaming ──────────────────────────
//
// Replaces MediaRecorder. Sends raw 16kHz int16 PCM frames to WebSocket.
// Deepgram streaming STT receives these frames and handles endpointing.
//
// Fallback: if AudioWorklet unavailable, falls back to MediaRecorder blob mode
// (the original implementation) so older browsers still function.

let _micStream:      MediaStream | null      = null
let _micCtx:         AudioContext | null     = null
let _analyser:       AnalyserNode | null     = null
let _workletNode:    AudioWorkletNode | null = null
let _capturing:      boolean = false
let _isSendingPCM:   boolean = false
let _pendingListen:  boolean = false    // startListening() called before worklet ready
let _checkTimer:     number | null = null
let _ws:             WebSocket | null = null
let _useWorklet:     boolean = true     // set false on init failure → blob fallback

// ── Blob fallback state (MediaRecorder) ──────────────────────────────────────
// Only used when AudioWorklet is unavailable
let _recorder:       MediaRecorder | null = null
let _chunks:         Blob[] = []
let _speechDetected: boolean = false
let _silenceStart:   number | null = null
let _recStart:       number = 0
const SILENCE_TIMEOUT_FALLBACK = 600  // ms — reduced from original 1200ms even in fallback

async function _initMicStream(ws: WebSocket): Promise<void> {
  if (!navigator.mediaDevices?.getUserMedia) return
  _ws = ws
  _capturing = true

  try {
    _micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        channelCount:     1,
        sampleRate:       { ideal: 16000 },
      },
    })

    _micCtx   = new AudioContext()
    const src = _micCtx.createMediaStreamSource(_micStream)

    // Pre-create playback AudioContext while we still have user-activation context
    // (getUserMedia counts as user gesture — AudioContext won't start suspended)
    if (!_playCtx || _playCtx.state === 'closed') {
      _playCtx = new AudioContext()
    }

    // Analyser for visual VAD indicator (not for send triggering)
    _analyser          = _micCtx.createAnalyser()
    _analyser.fftSize  = 256
    src.connect(_analyser)

    // Try AudioWorklet (requires HTTPS or localhost)
    if (typeof AudioWorkletNode !== 'undefined' && _micCtx.audioWorklet) {
      try {
        await _micCtx.audioWorklet.addModule('/audioWorkletProcessor.js')

        _workletNode = new AudioWorkletNode(_micCtx, 'pcm-processor')
        src.connect(_workletNode)

        _workletNode.port.onmessage = (e) => {
          if (
            e.data?.type === 'pcm' &&
            _isSendingPCM &&
            _ws?.readyState === WebSocket.OPEN
          ) {
            _ws.send(e.data.buffer)
          }
        }

        _useWorklet = true

        // startListening() may have been called before worklet was ready
        if (_pendingListen) {
          _pendingListen = false
          startListening()
        }

        return
      } catch (workletErr) {
        console.warn('[mic] AudioWorklet unavailable, falling back to MediaRecorder:', workletErr)
        _useWorklet = false
      }
    } else {
      _useWorklet = false
    }

    // Blob fallback path
    if (_pendingListen) {
      _pendingListen = false
      startListening()
    }

  } catch (err) {
    console.error('[mic] init failed:', err)
    _capturing = false
  }
}

function startListening(): void {
  if (!_capturing || !_micStream) return

  if (_useWorklet) {
    // AudioWorklet path
    if (!_workletNode) {
      // Worklet not ready yet — queue the request
      _pendingListen = true
      return
    }
    if (_isSendingPCM) return
    _isSendingPCM = true
    _workletNode.port.postMessage({ type: 'start' })
    useInterview.setState({ audioState: 'listening' })
    _startVADMonitor()
  } else {
    // MediaRecorder fallback path
    if (_recorder && _recorder.state === 'recording') return
    useInterview.setState({ audioState: 'listening' })
    _beginRecordingFallback()
  }
}

function stopListening(): void {
  if (_useWorklet) {
    if (!_isSendingPCM) return
    _isSendingPCM = false
    _workletNode?.port.postMessage({ type: 'stop' })
    if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
  } else {
    // Fallback: stop recorder but discard audio (AI is speaking)
    if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
    if (_recorder && _recorder.state === 'recording') {
      _chunks         = []
      _speechDetected = false
      try { _recorder.stop() } catch (_) {}
    }
  }
}

function stopMicCapture(): void {
  _capturing    = false
  _isSendingPCM = false
  _pendingListen = false

  if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }

  // Worklet teardown
  try { _workletNode?.port.postMessage({ type: 'stop' }) } catch (_) {}
  try { _workletNode?.disconnect() } catch (_) {}

  // Fallback recorder teardown
  try {
    if (_recorder && _recorder.state !== 'inactive') _recorder.stop()
  } catch (_) {}

  try { _micCtx?.close() } catch (_) {}
  try { _micStream?.getTracks().forEach(t => t.stop()) } catch (_) {}

  _workletNode = null
  _micCtx      = null
  _micStream   = null
  _analyser    = null
  _recorder    = null
  _ws          = null
}

/** Visual VAD monitor — updates listening indicator, does NOT trigger sends. */
function _startVADMonitor(): void {
  if (_checkTimer) clearInterval(_checkTimer)
  const buf = new Uint8Array(_analyser?.frequencyBinCount ?? 128)

  _checkTimer = window.setInterval(() => {
    if (!_analyser || !_isSendingPCM) return
    _analyser.getByteFrequencyData(buf)
    const avg = buf.reduce((a, b) => a + b, 0) / buf.length
    if (avg >= VAD_VISUAL_THRESHOLD) {
      useInterview.setState({ audioState: 'listening' })
    }
  }, 100)
}


// ── MediaRecorder fallback (used when AudioWorklet unavailable) ───────────────

function _beginRecordingFallback(): void {
  if (!_micStream || !_analyser || !_ws) return

  _chunks         = []
  _speechDetected = false
  _silenceStart   = null
  _recStart       = Date.now()

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : 'audio/webm'

  try {
    _recorder = new MediaRecorder(_micStream, { mimeType })
  } catch {
    _recorder = new MediaRecorder(_micStream)
  }

  _recorder.ondataavailable = (e) => {
    if (e.data.size > 0) _chunks.push(e.data)
  }

  _recorder.onstop = () => {
    if (_chunks.length === 0 || !_speechDetected || _isSendingPCM === false) {
      // _isSendingPCM is false in fallback mode; check _capturing instead
      if (_chunks.length === 0 || !_speechDetected || !_capturing) {
        _chunks = []
        return
      }
    }
    const blob = new Blob(_chunks, { type: 'audio/webm' })
    _chunks = []
    const dur = Date.now() - _recStart
    useInterview.setState({ audioState: 'thinking' })
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: 'AUDIO_META', format: 'webm', duration_ms: dur }))
      blob.arrayBuffer().then(buf => {
        if (_ws && _ws.readyState === WebSocket.OPEN) _ws.send(buf)
      })
    }
  }

  _recorder.start(100)

  const vbuf = new Uint8Array(_analyser.frequencyBinCount)
  if (_checkTimer) clearInterval(_checkTimer)
  _checkTimer = window.setInterval(() => {
    if (!_analyser || !_recorder || _recorder.state !== 'recording') return
    _analyser.getByteFrequencyData(vbuf)
    const avg     = vbuf.reduce((a, b) => a + b, 0) / vbuf.length
    const elapsed = Date.now() - _recStart

    if (avg >= VAD_VISUAL_THRESHOLD) {
      _speechDetected = true
      _silenceStart   = null
      useInterview.setState({ audioState: 'listening' })
    } else if (_speechDetected) {
      if (!_silenceStart) {
        _silenceStart = Date.now()
      } else if (Date.now() - _silenceStart >= SILENCE_TIMEOUT_FALLBACK) {
        useInterview.setState({ audioState: 'thinking' })
        try { _recorder.stop() } catch (_) {}
        if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
        return
      }
    }

    if (elapsed >= 30000 && _speechDetected) {
      useInterview.setState({ audioState: 'thinking' })
      try { _recorder.stop() } catch (_) {}
      if (_checkTimer) { clearInterval(_checkTimer); _checkTimer = null }
    }
  }, 100)
}


// ── Zustand Store ─────────────────────────────────────────────────────────────

export const useInterview = create<InterviewState>((set, get) => ({
  sessionId:        null,
  wsStatus:         'disconnected',
  audioState:       'silence',
  mode:             'PROBING',
  turnNumber:       0,
  currentQuestion:  '',
  transcript:       '',
  isStreaming:      false,
  turns:            [],
  domain:           null,
  reconnectAttempt: 0,
  reconnectTimer:   null,
  reconnectMessage: '',
  ws:               null,

  connect: (sessionId: string) => {
    const { ws } = get()
    if (ws) ws.close(1000)

    const token = useAuth.getState().accessToken
    if (!token) return

    const url = `${WS_BASE}/ws/${sessionId}?token=${token}`
    set({ wsStatus: 'connecting', sessionId })

    const socket = new WebSocket(url)
    socket.binaryType = 'arraybuffer'

    socket.onopen = () => {
      set({ wsStatus: 'connected', reconnectAttempt: 0, ws: socket })
      socket.send(JSON.stringify({ type: 'HEARTBEAT' }))
      // Init mic — async, non-blocking. startListening() called later by audio drain.
      _initMicStream(socket)

      // Heartbeat loop
      const hb = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'HEARTBEAT' }))
        } else {
          clearInterval(hb)
        }
      }, 15000)
    }

    socket.onmessage = (event: MessageEvent) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame = TTS audio — stop mic, play audio
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
      stopAudio()
      ws.send(JSON.stringify({ type: 'BARGE_IN' }))
      set({ audioState: 'listening', isStreaming: false, currentQuestion: '' })
      // Resume PCM streaming immediately for barge-in
      startListening()
    }
  },

  appendToken:  (token: string) => set(s => ({
    currentQuestion: s.currentQuestion + token,
    isStreaming:     true,
  })),

  finishTurn: (turn: TurnRecord) => set(s => ({
    turns:       [...s.turns, turn],
    turnNumber:  turn.turnNumber,
    isStreaming: false,
  })),

  setAudioState:  (state: AudioState)   => set({ audioState: state }),
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


// ── WebSocket message handler ─────────────────────────────────────────────────

function handleMessage(msg: Record<string, unknown>) {
  const store = useInterview.getState()
  const p     = (msg.payload || msg) as any

  switch (msg.type) {

    case 'TURN_START':
      // AI pipeline starting — stop PCM immediately to avoid echo/spurious transcription
      stopListening()
      store.setAudioState('thinking')
      if (p.mode) store.setMode(p.mode as InterviewMode)
      useInterview.setState({ currentQuestion: '', transcript: '' })
      break

    case 'TURN_COMPLETE':
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
      // JSON frame carries question text for transcript display
      if (p.text && p.is_final) {
        useInterview.setState({ currentQuestion: p.text })
      }
      store.setAudioState('speaking')
      stopListening()
      break

    case 'INTERVIEWER_DONE':
      // Audio frames are already queued in Web Audio. startListening() fires
      // in _processDecodeQueue.src.onended when playback is complete.
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
      if (p.turn_count) useInterview.setState({ turnNumber: p.turn_count })
      if (p.mode) store.setMode(p.mode as InterviewMode)
      useInterview.setState({
        audioState:       'silence',
        reconnectMessage: p.message || 'Session restored. Continuing interview.',
      })
      setTimeout(() => useInterview.setState({ reconnectMessage: '' }), 5000)
      break

    case 'ERROR':
      console.error('[WS] server error:', p.message || p.error)
      break

    default:
      break
  }
}
