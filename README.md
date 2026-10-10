# Tiradentes V1

Tiradentes is an early MVP for looking up Brazilian politically exposed persons (PEPs) by CPF. The backend validates the CPF, gathers public records from several sources, and returns a consolidated response. The frontend is a React page scaffold for submitting a CPF and displaying the result.

## Current state

- `POST /api/v1/check-cpf` is implemented in FastAPI and calls a LangGraph pipeline.
- The graph checks the CPF, reads a 24-hour local JSON cache, runs source tools in parallel on a cache miss, aggregates the results, and writes the response to the cache.
- Source tools cover CGU PEP CSV, TSE candidaturas and declared assets, and CGU CEIS/CNEP sanctions. The DOU tool is a stub. Downloads use `httpx`, retries, and pandas CSV parsing. Availability and formats of external datasets have not been verified end to end.
- The API response contains `nome`, `cargos`, `fontes`, `irregularidades`, `sancoes`, `bens`, and `is_pep`. There is no LLM verification yet; classification currently comes from the aggregation rules.
- The frontend has a form, API client, result view, and mock fallback when the request fails. It is **not yet a runnable Vite app**: `index.html`, `src/main.tsx`, Vite/TypeScript/PostCSS setup, and a stylesheet are still missing. The backend also does not currently configure CORS for a separate Vite origin.

## Repository layout

```text
backend/
  app/main.py                 FastAPI application
  app/api/v1/                 Request/response schemas and CPF route
  app/agents/graph.py         LangGraph orchestration and local response cache
  app/agents/tools/crawler.py Source fetchers and CSV parsers
  tests/test_tools.py         Source tool tests with mocked downloads
  requirements.txt            Python dependencies
frontend/
  src/App.tsx                 React Query provider
  src/pages/Home.tsx          CPF form and results
  src/api/client.ts           Typed API request and mock fallback
  src/types/index.ts          Frontend response types
  package.json                Frontend dependencies and scripts
```

## Backend development

Use Python 3.12. From the repository root:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
cd backend
.venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

API documentation is served at `http://localhost:8000/docs`. From another terminal, submit a checksum-valid CPF:

```bash
curl -X POST http://localhost:8000/api/v1/check-cpf \
  -H 'Content-Type: application/json' \
  -d '{"cpf":"52998224725"}'
```

The example CPF is for exercising validation; no PEP match is implied. A cache miss may trigger large public downloads and can take time. Source failures are logged and may leave results incomplete. The frontend API client currently returns a local mock on fetch failures, so a frontend result alone does not prove the backend was reached.

Run the existing backend tests after installing dependencies:

```bash
cd backend
.venv/bin/python -m pytest tests -q
```

## Frontend status

The intended stack is React 18, Vite, TypeScript, TanStack Query, Tailwind, Radix primitives, and `react-input-mask`. `frontend/src/api/client.ts` targets `http://localhost:8000` by default and accepts `VITE_API_BASE_URL` to change that address. The frontend cannot be built or started from the checked-in files until the missing Vite entry/configuration files are added. CORS must also be configured on the API before browser requests from a separate development origin will succeed.

## Planned work

Complete the Vite bootstrap and backend CORS configuration; verify source URLs and CSV schemas with live datasets; add meaningful integration tests; then replace the local cache and rule-based classification with the intended persistence and verification services.
