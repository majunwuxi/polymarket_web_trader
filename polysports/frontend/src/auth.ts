import { reactive } from 'vue'
import { ApiError, responseData } from './api'

export { ApiError } from './api'

export const session = reactive({ authenticated: false, username: '', csrf: '', expiresAt: '' })
const SESSION_RESTORE_TTL = 15_000
let restoreInFlight: Promise<boolean> | null = null
let restoredAt = 0

async function loadSession(): Promise<boolean> {
  try {
    const response = await fetch('/api/v1/auth/session', { credentials: 'same-origin', cache: 'no-store' })
    const data = await responseData(response)
    session.authenticated = true
    session.username = data.username
    session.csrf = data.csrf_token
    session.expiresAt = data.expires_at
    return true
  } catch (error) {
    clearSession()
    if (error instanceof ApiError && error.status === 401) return false
    throw error
  }
}

export function restoreSession(): Promise<boolean> {
  if (session.authenticated && Date.now() - restoredAt < SESSION_RESTORE_TTL) return Promise.resolve(true)
  if (restoreInFlight) return restoreInFlight
  restoreInFlight = loadSession()
    .then((authenticated) => { if (authenticated) restoredAt = Date.now(); return authenticated })
    .finally(() => { restoreInFlight = null })
  return restoreInFlight
}

export async function login(username: string, password: string): Promise<void> {
  const csrfResponse = await fetch('/api/v1/auth/csrf', { credentials: 'same-origin', cache: 'no-store' })
  const initial = await responseData(csrfResponse)
  const response = await fetch('/api/v1/auth/login', {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': initial.csrf_token },
    body: JSON.stringify({ username, password }),
  })
  const data = await responseData(response)
  session.authenticated = data.authenticated
  session.csrf = data.csrf_token
  restoredAt = Date.now()
}

export function clearSession(): void {
  session.authenticated = false; session.username = ''; session.csrf = ''; session.expiresAt = ''; restoredAt = 0
}

export async function logout(): Promise<void> {
  const response = await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': session.csrf } })
  await responseData(response)
  clearSession()
}
