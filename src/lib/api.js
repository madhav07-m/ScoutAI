// Same-origin in production (the FastAPI backend serves the built frontend),
// proxied to localhost:8000 in dev via vite.config.js. Override with
// VITE_API_BASE if the backend is deployed separately (e.g. Render URL)
// and the frontend is hosted elsewhere (e.g. Vercel).
const API_BASE = import.meta.env.VITE_API_BASE || ''

export async function rankResumes({ jdFile, resumeFiles, geminiKey, accessToken }) {
  const formData = new FormData()
  formData.append('jd', jdFile)
  resumeFiles.forEach((f) => formData.append('resumes', f))
  if (geminiKey) formData.append('gemini_key', geminiKey)

  const res = await fetch(`${API_BASE}/api/rank`, {
    method: 'POST',
    body: formData,
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Ranking failed (${res.status})`)
  }
  return res.json()
}

// Gap analysis for a whole ranking batch runs as a backend background
// task (see backend/main.py's _run_gap_analysis_batch) rather than
// inline in the /api/rank response, because N resumes x up to 120s of
// Gemini retries each can exceed the hosting platform's request
// timeout. /api/rank now returns almost immediately with
// gap_status: "pending" | "skipped" (no key given) -- poll this
// endpoint until gap_status is "done" or "error", re-rendering the
// table on each poll since llm_score / gap_report fill in
// progressively per resume, not all at once.
export async function getRankGapStatus({ sessionId }) {
  const res = await fetch(`${API_BASE}/api/rank/${encodeURIComponent(sessionId)}/gap-status`)
  if (!res.ok) throw new Error(`Failed to check gap analysis status (${res.status})`)
  return res.json()
}

export async function regenerateGapAnalysis({ sessionId, docName, geminiKey }) {
  const params = new URLSearchParams()
  if (geminiKey) params.set('gemini_key', geminiKey)
  const qs = params.toString() ? `?${params.toString()}` : ''

  const res = await fetch(
    `${API_BASE}/api/rank/${encodeURIComponent(sessionId)}/gap-analysis/${encodeURIComponent(docName)}${qs}`,
    { method: 'POST' }
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Gap analysis failed (${res.status})`)
  }
  return res.json()
}

export function gapAnalysisPdfUrl({ sessionId, docName }) {
  return `${API_BASE}/api/rank/${encodeURIComponent(sessionId)}/gap-analysis/${encodeURIComponent(docName)}/pdf`
}

export async function getCompaniesOverview() {
  const res = await fetch(`${API_BASE}/api/companies/overview`)
  if (!res.ok) throw new Error(`Failed to load companies (${res.status})`)
  return res.json()
}

export async function refreshCompanies() {
  const res = await fetch(`${API_BASE}/api/companies/refresh`, { method: 'POST' })
  if (!res.ok) throw new Error(`Refresh failed (${res.status})`)
  return res.json()
}

export async function searchPostings({ role, location }) {
  const params = new URLSearchParams()
  if (role) params.set('role', role)
  if (location) params.set('location', location)
  const res = await fetch(`${API_BASE}/api/companies/search?${params.toString()}`)
  if (!res.ok) throw new Error(`Search failed (${res.status})`)
  return res.json()
}

export async function getCompanyPostings(companyName) {
  const res = await fetch(`${API_BASE}/api/companies/${encodeURIComponent(companyName)}/postings`)
  if (!res.ok) throw new Error(`Failed to load postings (${res.status})`)
  return res.json()
}