import { create } from 'zustand'

export type ToastType = 'info' | 'success' | 'error' | 'warning'

export interface Toast {
  id: string
  message: string
  type: ToastType
  duration?: number
}

interface ToastState {
  toasts: Toast[]
  push: (message: string, type?: ToastType, duration?: number) => void
  dismiss: (id: string) => void
}

let _counter = 0

export const useToast = create<ToastState>((set) => ({
  toasts: [],
  push: (message, type = 'info', duration = 4000) => {
    const id = `toast-${++_counter}`
    set(s => ({ toasts: [...s.toasts, { id, message, type, duration }] }))
    if (duration > 0) {
      setTimeout(() => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })), duration)
    }
  },
  dismiss: (id) => set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })),
}))

export const toast = {
  info:    (msg: string) => useToast.getState().push(msg, 'info'),
  success: (msg: string) => useToast.getState().push(msg, 'success'),
  error:   (msg: string) => useToast.getState().push(msg, 'error'),
  warning: (msg: string) => useToast.getState().push(msg, 'warning'),
}
