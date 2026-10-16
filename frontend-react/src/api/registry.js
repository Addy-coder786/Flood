// Resource registry API (2026-10-11): every call goes through client.js request(), which adds the dispatcher token
// from this tab (X-FloodNet-Token). Responder and volunteer calls pass their own token instead and never fall back to
// the dispatcher's. Endpoints: backend/floodnet/api/registry_api.py and the registry part of ops_api.py.
import { request } from './client.js'

const enc = encodeURIComponent
const post = (path, body = {}, token) => request(path, { method: 'POST', body, token })
const R = '/api/ops/registry'

// ---------------------------------------------------------------- dispatcher
export const registryVocabulary = () => request(`${R}/vocabulary`)
export const registryResources = () => request('/api/ops/resources')
export const registerRecord = ({ kind, record, observedAt = null, clientRef = null }) =>
  post(`${R}/records`, { kind, record, observed_at: observedAt, client_ref: clientRef })
export const importRecords = ({ label, format, text, commit = false }) => post(`${R}/import`, { label, format, text, commit })
export const updateResource = (id, fields, observedAt = null) =>
  post(`/api/ops/resources/${enc(id)}/updates`, { fields, observed_at: observedAt })

export const listResponders = () => request('/api/ops/responders')
export const registerResponder = ({ name, role, scope }) => post('/api/ops/responders', { name, role, scope })
export const revokeResponder = (id) => post(`${R}/responders/${enc(id)}/revoke`)

export const listCandidates = () => request(`${R}/candidates`)
export const candidateSiteCheck = (id) => request(`${R}/candidates/${enc(id)}/site-check`)
export const candidatePermission = (id, body) => post(`${R}/candidates/${enc(id)}/permission`, body)
export const candidateSuitability = (id, body) => post(`${R}/candidates/${enc(id)}/suitability`, body)
export const candidateConfirm = (id, body = {}) => post(`${R}/candidates/${enc(id)}/confirm`, body)
export const candidateReject = (id, reason) => post(`${R}/candidates/${enc(id)}/reject`, { reason })

// Dispatcher on a volunteer's behalf (no token argument: the dispatcher's own), or a volunteer with their token.
export const submitCandidate = (body, token) => post(`${R}/candidates`, body, token)

// ---------------------------------------------------------------- responder / volunteer (own token)
export const responderMe = (token) => request(`${R}/responder/me`, { token })
export const responderUpdate = (token, id, fields, observedAt = null) =>
  post(`${R}/responder/resources/${enc(id)}/updates`, { fields, observed_at: observedAt }, token)
