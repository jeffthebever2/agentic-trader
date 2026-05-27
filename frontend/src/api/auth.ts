import api from './client'
import type { User, AuthFeatures, TwoFAStatus } from '@/types'

export const getMe = () => api.get<User>('/auth/me').then(r => r.data)
export const getFeatures = () => api.get<AuthFeatures>('/auth/features').then(r => r.data)
export const get2FAStatus = () => api.get<TwoFAStatus>('/auth/2fa/status').then(r => r.data)
export const completeOnboarding = () => api.post('/auth/me/complete-onboarding')
export const acceptHilDisclosure = () => api.post('/auth/me/hil-disclosure')
export const updateName = (name: string) => api.post('/auth/me/name', { name })
export const updatePhone = (phone: string) => api.post('/auth/me/phone', { phone })
export const verifyPhone = (code: string) => api.post('/auth/me/phone/verify', { code })

export const getUsers = () => api.get('/auth/users').then(r => r.data)
export const getUserByEmail = (email: string) =>
  api.get(`/auth/users/${encodeURIComponent(email)}`).then(r => r.data)
export const updateUserRole = (email: string, role: string) =>
  api.patch(`/auth/users/${encodeURIComponent(email)}/role`, { role })

export const enrollTOTP  = () => api.post('/auth/2fa/totp/enroll').then(r => r.data)
export const activateTOTP = (code: string) => api.post('/auth/2fa/totp/activate', { code })
export const disableTOTP  = () => api.post('/auth/2fa/totp/disable')

export const beginPasskeyRegister   = () => api.post('/auth/2fa/passkey/register/begin').then(r => r.data)
export const completePasskeyRegister = (data: unknown) =>
  api.post('/auth/2fa/passkey/register/complete', data)

export const sendStepUpEmail   = () => api.post('/auth/2fa/step-up/email/send')
export const beginStepUpPasskey = () => api.post('/auth/2fa/step-up/passkey/begin').then(r => r.data)
export const completeStepUpPasskey = (data: unknown) =>
  api.post('/auth/2fa/step-up/passkey/complete', data)
export const submitStepUpCode  = (method: string, code: string) =>
  api.post(`/auth/2fa/step-up/${method}`, { code })
