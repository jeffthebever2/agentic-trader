import axios, { type AxiosInstance, type AxiosError } from 'axios'
import { DEFAULT_API_BASE_URL, DEFAULT_API_TIMEOUT_MS } from '@shared/constants'

// Settings are persisted via Tauri plugin-store.
// The store is loaded async; until loaded, we fall back to env or default.
let _baseUrl = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? DEFAULT_API_BASE_URL
let _token: string | null = null

let _instance: AxiosInstance = buildInstance()

function buildInstance(): AxiosInstance {
  const inst = axios.create({
    baseURL: _baseUrl,
    timeout: DEFAULT_API_TIMEOUT_MS,
    withCredentials: true,
  })

  inst.interceptors.request.use(config => {
    if (_token) {
      // Backend reads X-Manager-Key and compares against MANAGER_API_KEY in .env
      config.headers['X-Manager-Key'] = _token
    }
    return config
  })

  inst.interceptors.response.use(
    res => res,
    (err: AxiosError<{ detail?: string; error?: string }>) => {
      const detail =
        err.response?.data?.detail ??
        err.response?.data?.error ??
        err.message ??
        'Unknown error'
      const enhanced = new Error(detail) as Error & { status?: number }
      enhanced.status = err.response?.status
      return Promise.reject(enhanced)
    },
  )

  return inst
}

/** Call after loading settings from Tauri store. Rebuilds the Axios instance. */
export function configureClient(baseUrl: string, token: string | null) {
  _baseUrl = baseUrl || DEFAULT_API_BASE_URL
  _token = token
  _instance = buildInstance()
}

export function getClient(): AxiosInstance {
  return _instance
}

/** GET /api/... shorthand */
export const api = new Proxy({} as AxiosInstance, {
  get(_target, prop) {
    return (...args: unknown[]) => (_instance as unknown as Record<string, (...a: unknown[]) => unknown>)[prop as string](...args)
  },
})
