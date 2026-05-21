/**
 * lib/api.ts — Axios instance with auth interceptors.
 *
 * URL contract matches backend gateway.py exactly:
 *   POST   /sessions                    create session
 *   GET    /sessions                    list sessions
 *   GET    /sessions/{id}               live session state (Redis)
 *   GET    /sessions/{id}/report        full report (Postgres)
 *   DELETE /sessions/{id}              end session
 *
 * Auth endpoints are at /auth/* (no prefix).
 * Admin endpoints are at /admin/*.
 * Reviewer endpoints are at /reviewer/*.
 */
import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios'
import { useAuth } from '@/stores/auth'

export const BASE_URL =
  typeof import.meta !== 'undefined'
    ? ((import.meta as any).env?.VITE_API_URL || '')
    : ''

// Empty base URL: calls go to same origin, proxied by vite in dev / nginx in prod
export const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 15000,
  withCredentials: true,  // required for httpOnly refresh token cookie
})

// ── 401 refresh + retry ───────────────────────────────────────────────────────

let isRefreshing = false
let failedQueue: Array<{ resolve: (v: unknown) => void; reject: (r?: unknown) => void }> = []

function processQueue(error: AxiosError | null, token: string | null = null) {
  failedQueue.forEach(({ resolve, reject }) => error ? reject(error) : resolve(token))
  failedQueue = []
}

api.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = useAuth.getState().accessToken
    if (token && !config.headers.Authorization) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as InternalAxiosRequestConfig & { _retry?: boolean }

    if (error.response?.status !== 401 || original._retry) {
      return Promise.reject(error)
    }
    if (original.url?.includes('/auth/refresh') || original.url?.includes('/auth/login')) {
      return Promise.reject(error)
    }

    if (isRefreshing) {
      return new Promise((resolve, reject) => { failedQueue.push({ resolve, reject }) })
        .then((token) => { original.headers.Authorization = `Bearer ${token}`; return api(original) })
        .catch((err) => Promise.reject(err))
    }

    original._retry = true
    isRefreshing = true

    try {
      const success = await useAuth.getState().refresh()
      if (!success) {
        processQueue(new AxiosError('Refresh failed'), null)
        useAuth.getState().logout()
        return Promise.reject(error)
      }
      const newToken = useAuth.getState().accessToken
      processQueue(null, newToken)
      original.headers.Authorization = `Bearer ${newToken}`
      return api(original)
    } catch (err) {
      processQueue(err as AxiosError, null)
      useAuth.getState().logout()
      return Promise.reject(err)
    } finally {
      isRefreshing = false
    }
  }
)

// ── Typed API helpers ─────────────────────────────────────────────────────────

export const authApi = {
  login:          (email: string, password: string) => api.post('/auth/login', { email, password }),
  register:       (email: string, password: string, fullName?: string) =>
                    api.post('/auth/register', { email, password, full_name: fullName }),
  logout:         () => api.post('/auth/logout'),
  me:             () => api.get('/auth/me'),
  forgotPassword: (email: string) => api.post('/auth/forgot-password', { email }),
  resetPassword:  (token: string, newPassword: string) =>
                    api.post('/auth/reset-password', { token, new_password: newPassword }),
}

export const sessionApi = {
  // Parse resume file — separate from session creation
  parseResume: (file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/sessions/parse-resume', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 30000,
    })
  },
  // candidate_id comes from JWT on backend — only domain needed in body
  create: (domain: string, resume_text?: string) => api.post('/sessions', { domain, resume_text: resume_text || '' }),
  createWithFile: (domain: string, file: File) => {
    const formData = new FormData()
    formData.append('domain', domain)
    formData.append('resume', file)
    return api.post('/sessions/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  // GET /sessions — list of completed sessions for the authenticated candidate
  list:   () => api.get('/sessions'),
  // GET /sessions/{id} — live Redis state (for InterviewPage to read domain)
  get:    (sessionId: string) => api.get(`/sessions/${sessionId}`),
  // GET /sessions/{id}/report — full Postgres report (for ReportPage)
  report: (sessionId: string) => api.get(`/sessions/${sessionId}/report`),
  end:    (sessionId: string) => api.delete(`/sessions/${sessionId}`),
}

export const adminApi = {
  dashboard:      ()              => api.get('/admin/dashboard'),
  activeSessions: ()              => api.get('/admin/sessions/active'),
  sessions:       (p?: object)   => api.get('/admin/sessions', { params: p }),
  sessionDetail:  (id: string)   => api.get(`/admin/sessions/${id}/detail`),
  latencyMetrics: (p?: object)   => api.get('/admin/metrics/latency', { params: p }),
  costMetrics:    (p?: object)   => api.get('/admin/metrics/cost', { params: p }),
  scoreMetrics:   (p?: object)   => api.get('/admin/metrics/scores', { params: p }),
  events:         (p?: object)   => api.get('/admin/events', { params: p }),
  users:          (p?: object)   => api.get('/admin/users', { params: p }),
  toggleUser:     (id: string)   => api.patch(`/admin/users/${id}/toggle-active`),
  prompts:        ()              => api.get('/admin/prompts'),
  createPrompt:   (d: object)    => api.post('/admin/prompts', d),
  activatePrompt: (id: string)   => api.post(`/admin/prompts/${id}/activate`),
  promptContent:  (id: string)   => api.get(`/admin/prompts/${id}/content`),
  integrity:      (p?: object)   => api.get('/admin/integrity', { params: p }),
  // LLM Config
  llmConfig:      ()              => api.get('/admin/llm-config'),
  setLlmConfig:   (d: object)    => api.post('/admin/llm-config', d),
  // Voice Config
  voiceConfig:    ()              => api.get('/admin/voice-config'),
  setVoiceConfig: (d: object)    => api.post('/admin/voice-config', d),
  // TTS Test
  testTts:        (d: object)    => api.post('/admin/test-tts', d),
  // Prompt Playground
  playground:     (d: object)    => api.post('/admin/playground', d),
}

export const reviewerApi = {
  queue:          (p?: object)   => api.get('/reviewer/queue', { params: p }),
  transcript:     (id: string)   => api.get(`/reviewer/sessions/${id}/transcript`),
  integrity:      (id: string)   => api.get(`/reviewer/sessions/${id}/integrity`),
  submitReview:   (d: object)    => api.post('/reviewer/review', d),
  sessionReviews: (id: string)   => api.get(`/reviewer/review/${id}`),
  allReviews:     (p?: object)   => api.get('/reviewer/reviews/all', { params: p }),
  addNote:        (id: string, d: object) => api.post(`/reviewer/sessions/${id}/notes`, d),
  override:       (id: string, d: object) => api.post(`/reviewer/sessions/${id}/override`, d),
  approve:        (id: string)   => api.post(`/reviewer/sessions/${id}/approve`),
  flag:           (id: string, d: object) => api.post(`/reviewer/sessions/${id}/flag`, d),
}

export const observabilityApi = {
  summary:  (window?: number) => api.get('/api/observability/summary', { params: window ? { window } : {} }),
  logs:     (p?: object)      => api.get('/api/observability/logs', { params: p }),
  session:  (id: string)      => api.get(`/api/observability/session/${id}`),
  deepHealth: ()              => api.get('/health/deep'),
}

export const integrityApi = {
  // Fire-and-forget — never throws, never blocks interview
  sendEvent: (data: { session_id: string; event_type: string; context?: object }) =>
    api.post('/integrity/event', data).catch(() => {}),
}
