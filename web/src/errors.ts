export class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail = '') {
    super(`API ${status}${detail ? `: ${detail}` : ''}`)
    this.status = status
    this.detail = detail
  }
}

export function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return 'Can’t reach Event Agent. Check your connection and try again.'
    return err.detail || `Something went wrong (${err.status}). Try again.`
  }
  return 'Something went wrong. Try again.'
}
