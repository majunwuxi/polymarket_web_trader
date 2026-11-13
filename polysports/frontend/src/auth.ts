import { reactive } from 'vue'

export const session = reactive({ authenticated: false, username: '', csrf: '', expiresAt: '' })
export class ApiError extends Error {
  constructor(public status: number, message: string, public retryAfter: number | null = null) { super(message) }
}

async function responseData(response: Response) {
  if (response.status === 204) return null
  const body = await response.json()
  if (!response.ok) throw new ApiError(response.status, body?.error?.message || '服务暂不可用', body?.error?.retry_after_seconds ?? null)
  return body.data
}

export async function restoreSession(): Promise<boolean> {
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
}

export function clearSession(): void {
  session.authenticated = false; session.username = ''; session.csrf = ''; session.expiresAt = ''
}

export async function logout(): Promise<void> {
  const response = await fetch('/api/v1/auth/logout', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': session.csrf } })
  await responseData(response)
  clearSession()
}
