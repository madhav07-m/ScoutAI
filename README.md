# Scout AI — Frontend (React)

React 19 + Tailwind CSS v4 + Framer Motion rewrite of the original
vanilla HTML/CSS/JS frontend, using the same "confident dossier" visual
system, wired to the same FastAPI backend and Supabase project.

## Stack

- **React 19** + React Router 7 (client-side routing: `/`, `/login`, `/companies`)
- **Tailwind CSS v4** (via `@tailwindcss/vite`, config lives in `src/index.css`'s `@theme` block)
- **Framer Motion** — page transitions, the ranking progress ring, accordion gap-analysis cards, history list add/remove animations
- **lucide-react** — icon set
- **@supabase/supabase-js** — auth + history table, same project as before

## Getting started

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, proxies /api to localhost:8000
```

For the dev proxy to work, also run the FastAPI backend locally on port 8000
(`uvicorn backend.main:app --reload`, from the project root).

## Building for production

```bash
npm run build       # outputs to frontend/dist/
```

`backend/main.py` has been updated to serve `frontend/dist/` as a single-page
app — it serves built assets under `/assets`, and falls back to `dist/index.html`
for every other non-`/api/...` path, letting React Router take over client-side.
**You must run `npm run build` before deploying**, or the backend will 500 with
a message saying the build wasn't found.

## Structure

```
src/
  lib/
    supabase.js       Supabase client singleton
    api.js             fetch() wrappers for every FastAPI route
  hooks/
    useAuth.jsx        Auth context (Supabase session state)
  components/
    Nav.jsx            Shared top nav, route-aware (History/New Analysis only show on Dashboard)
    RequireAuth.jsx     Route guard, redirects to /login if signed out
    ProgressRing.jsx   Animated ranking progress ring
    Results.jsx        Metrics, rank table, notices
    GapCard.jsx        Accordion gap-analysis card, regenerate + PDF download
    Badge.jsx          Strong/Average/Weak pill
    HistoryPanel.jsx    History list, delete-one / clear-all (both confirm-gated)
  pages/
    Dashboard.jsx      Upload form -> progress ring -> results (the old index.html)
    Login.jsx          Sign in / sign up (the old login.html)
    Companies.jsx      Companies board + search (the old companies.html)
```

## Response shapes (verified against app/companies_store.py and app/postings_search.py)

- `GET /api/companies/overview` → `{ companies: [{ name, open_postings, hiring }] }`
- `GET /api/companies/{company}/postings` → `{ company, postings: [{ id, title, location, url, salary_text, skills }] }` — `{company}` in the URL is the plain company name (e.g. "Stripe"), there is no slug concept anywhere in this backend.
- `GET /api/companies/search?role=&location=` → `{ matches: [{ id, company, title, location, url, salary_text, similarity }], total, indexed, building }` — a flat list of postings (not grouped by company); `similarity` is `null` for a location-only search; `building: true` means the ephemeral search index is still warming up after a refresh, and `Companies.jsx` polls every 2s until it's ready.

`Companies.jsx` was rewritten against these exact shapes after reviewing both files directly — no guessed field names remain.

## What's genuinely new vs. the old vanilla frontend

- Real component reuse (`Badge`, `GapCard`, `Nav` are shared, not copy-pasted per page)
- Physics-based, interruptible animations via Framer Motion instead of CSS `transition`/`@keyframes`
- Client-side routing — no full page reload moving between Dashboard/Login/Companies
- Centralized auth state (`useAuth`) instead of each page re-checking `supabase.auth.getSession()` independently
- A single `api.js` instead of `fetch()` calls scattered inline in each page's script tag
