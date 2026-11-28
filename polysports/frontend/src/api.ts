export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code = '',
    public retryAfter: number | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function responseData(response: Response): Promise<any> {
  if (response.status === 204) return null
  const contentType = response.headers?.get?.('content-type') || ''
  if (contentType && !contentType.toLowerCase().includes('application/json')) {
    throw new ApiError(
      response.status,
      response.ok ? '服务返回了无法解析的响应' : '服务暂不可用（HTTP ' + response.status + '）',
    )
  }
  let body: any
  try {
    body = await response.json()
  } catch {
    throw new ApiError(response.status, '服务返回了无效的响应（HTTP ' + response.status + '）')
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      body?.error?.message || '服务暂不可用',
      body?.error?.code || '',
      body?.error?.retry_after_seconds ?? null,
    )
  }
  return body?.data
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch('/api/v1' + path, { credentials: 'same-origin', cache: 'no-store', ...options })
  return (await responseData(response)) as T
}
