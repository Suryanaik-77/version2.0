/**
 * stores/auth.ts — Authentication state management.
 *
 * Responsibilities:
 * - Store access token in memory (never localStorage for security)
 * - Store refresh token in httpOnly cookie (handled by browser/server)
 * - Provide reactive user state to all components
 * - Handle token refresh on 401 responses
 * - Drive role-based route access
 */
import { create } from 'zustand'
import { api } from '../lib/api'

export type UserRole = 'candidate' | 'reviewer' | 'admin'

export interface AuthUser {
  id: string
  email: string
  full_name: string | null
  role: UserRole
}

interface AuthState {
  user: AuthUser | null
  accessToken: string | null
  isLoading: boolean
  isInitialized: boolean

  // Actions
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, fullName?: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<boolean>
  initialize: () => Promise<void>
  setUser: (user: AuthUser | null) => void
}

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  isLoading: false,
  isInitialized: false,

  initialize: async () => {
    // On app load, try to restore session via refresh token (httpOnly cookie)
    const refreshed = await get().refresh()
    if (!refreshed) {
      set({ isInitialized: true })
    }
  },

  login: async (email, password) => {
    set({ isLoading: true })
    try {
      const res = await api.post('/auth/login', { email, password })
      const { access_token, user_id, role, full_name } = res.data

      // Fetch full user details
      api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
      const meRes = await api.get('/auth/me')

      set({
        accessToken: access_token,
        user: meRes.data,
        isLoading: false,
        isInitialized: true,
      })
    } catch (err) {
      set({ isLoading: false })
      throw err
    }
  },

  register: async (email, password, fullName) => {
    set({ isLoading: true })
    try {
      const res = await api.post('/auth/register', {
        email,
        password,
        full_name: fullName,
      })
      const { access_token } = res.data
      api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
      const meRes = await api.get('/auth/me')

      set({
        accessToken: access_token,
        user: meRes.data,
        isLoading: false,
        isInitialized: true,
      })
    } catch (err) {
      set({ isLoading: false })
      throw err
    }
  },

  logout: async () => {
    try {
      await api.post('/auth/logout')
    } catch (_) {}
    delete api.defaults.headers.common['Authorization']
    set({ user: null, accessToken: null })
    window.location.href = '/login'
  },

  refresh: async () => {
    try {
      const res = await api.post('/auth/refresh')
      const { access_token } = res.data
      api.defaults.headers.common['Authorization'] = `Bearer ${access_token}`

      const meRes = await api.get('/auth/me')
      set({
        accessToken: access_token,
        user: meRes.data,
        isInitialized: true,
      })
      return true
    } catch (_) {
      set({ user: null, accessToken: null, isInitialized: true })
      return false
    }
  },

  setUser: (user) => set({ user }),
}))
