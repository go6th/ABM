# Automated Testing & Verification Guide

This document records the automated test suites, verification commands, and quality assurance gates for the Account Intelligence & ABM Platform.

---

## 1. Test Suites Overview

| Scope | Runner | Commands | Status |
| :--- | :--- | :--- | :--- |
| **Frontend Unit & Component** | Vitest 5.0 | `npm --prefix web run test` | ✅ 20/20 passed (7 test files) |
| **Frontend TypeScript Types** | TSC 6.0 | `npm --prefix web run typecheck` | ✅ 0 errors |
| **Frontend Production Build** | Next.js 16.4 | `npm --prefix web run build` | ✅ Complete (Optimized Turbopack) |
| **Backend Chat & LLM Registry**| Pytest 9.1 | `.venv/bin/pytest tests/unit/test_chat*` | ✅ 16/16 passed |
| **API Contract & OpenAPI Drift**| Pytest 9.1 | `.venv/bin/pytest tests/api/test_openapi_snapshot.py` | ✅ 4/4 passed |
| **Docker Compose Services** | Docker CLI | `docker compose ps` | ✅ 8/8 services healthy |

---

## 2. Running Frontend Tests

### Typecheck & Linter
```bash
# Typecheck TypeScript files
npm --prefix web run typecheck

# Lint client code
npm --prefix web run lint
```

### Unit Tests
```bash
npm --prefix web run test
```
**Test Files Verified:**
- `tests/unit/components.test.tsx`: Component mounting, state transitions, accessibility.
- `tests/unit/api-contract.test.ts`: Client contract validation against generated schemas.
- `tests/unit/operator-api.test.ts`: Operator console authentication, session state, and proxying.
- `tests/unit/operator-oauth-routes.test.ts`: Google OAuth & OIDC parameter verification.
- `tests/unit/operator-oidc.test.ts`: JWT token inspection and session minting.
- `tests/unit/query.test.ts`: Search, filtering, and query parameter serialization.
- `tests/unit/render.test.ts`: Data table rendering, cell sanitization, and date formatting.

---

## 3. Running Backend Tests

```bash
# Ensure virtual environment is activated
source .venv/bin/activate

# Execute Chat & Model Routing test suite
pytest tests/unit/test_chat_service.py tests/unit/test_chat_credentials.py tests/unit/test_chat_registry.py

# Execute OpenAPI contract & client sync tests
pytest tests/api/test_openapi_snapshot.py
```

### Regimen for OpenAPI Contract Generation
When backend routes or schemas change:
```bash
# 1. Update committed OpenAPI JSON specification
.venv/bin/python scripts/export_openapi.py

# 2. Regenerate TypeScript client types and labels
.venv/bin/python scripts/gen_web_client.py

# 3. Verify zero drift
pytest tests/api/test_openapi_snapshot.py
```

---

## 4. End-to-End Integration Scenarios Tested

### Scenario 1: Authentication & Session Verification
- **Endpoint**: `POST /api/auth/login` and `GET /v1/sso/session`
- **Behavior**: Minting authenticated session tokens via `fake-idp:9090` and validating multi-tenant membership. Verified that missing or invalid credentials return `422/401`.

### Scenario 2: Target Account Management & Persistence
- **Endpoint**: `POST /v1/accounts` and `GET /v1/accounts`
- **Behavior**: Adding company domain `salesforce.com` stores the account directly in PostgreSQL with isolated tenant ownership. Verified subsequent queries retrieve persisted accounts.

### Scenario 3: Research Run Execution
- **Endpoint**: `POST /v1/runs` and `GET /v1/runs/{id}`
- **Behavior**: Creating execution run reserves credits in tenant wallet and seeds 33-row intelligence matrix covering leadership, hiring, tech stack detections, and platform voice.

### Scenario 4: Claude-Style Interactive UI & Instant Downloads
- **Behavior**: Post-login interface on `/` features full-screen dark workspace, thread list matching production conversations, thinking trace accordion, data preview table, and zero-dependency client-side file generators:
  - `Salesforce_AI_Engineering_Roles_Market_Report.pdf`: Validated multi-page PDF 1.4 binary.
  - `salesforce_ai_engineering_roles_dataset.csv`: Validated RFC 4180 CSV export.
  - `salesforce_ai_intelligence.json`: Formatted JSON dataset.
