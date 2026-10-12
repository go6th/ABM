# Account Intelligence & ABM Platform - Technical Documentation

## Executive Summary

The Account Intelligence & ABM Platform is an enterprise multi-tenant account-based intelligence and research platform. It combines a Next.js 16 frontend with a Python FastAPI backend, PostgreSQL database with pgvector, Redis, and comprehensive observability. The system supports multi-tenant authentication (local password + SSO), research runs, account management, alerts, chat interface, and operator console.

## 1. Frontend Architecture

### 1.1 Overview

The frontend is built with **Next.js 16.4.0** using the App Router, **React 19.3.0**, and **TypeScript 6.0.3**. It uses **Turbopack** for development and supports both client-side and server-side rendering.

### 1.2 Directory Structure

```
web/
├── app/                          # Next.js App Router
│   ├── (auth)/                   # Authentication routes (customer login, etc.)
│   │   ├── login/page.tsx        # Customer login page
│   │   ├── forgot/page.tsx       # Password reset request
│   │   ├── reset/page.tsx         # Password reset
│   │   └── verify-email/page.tsx # Email verification
│   ├── (client)/                 # Protected client routes
│   │   ├── layout.tsx             # Client layout with shell
│   │   ├── page.tsx               # Overview/Dashboard
│   │   ├── accounts/              # Account management
│   │   │   └── [domain]/
│   │   │       ├── page.tsx       # Account details
│   │   │       └── tables/[table]/page.tsx # Tabular data
│   │   ├── chat/page.tsx          # Claude chat interface
│   │   ├── runs/                  # Research runs
│   │   ├── alerts/page.tsx        # Alerts & signals
│   │   ├── audit/page.tsx         # Audit trail
│   │   └── settings/              # Settings (users, CRM, etc.)
│   ├── operator/                  # Operator console
│   │   ├── (auth)/                # Operator auth
│   │   │   └── login/page.tsx     # Operator login (enhanced)
│   │   └── (console)/             # Operator dashboard
│   ├── design/                    # Design system showcase
│   └── globals.css                # Global styles
├── components/                    # Reusable components
│   ├── ClientShell.tsx            # Main shell with sidebar/nav
│   └── ...
├── lib/                           # Utilities & API
└── design/                        # Design system tokens
```

### 1.3 Key Frontend Components

| Component | Description | Location |
|-----------|-------------|----------|
| `ClientShell.tsx` | Main client layout with sidebar navigation, breadcrumbs, topbar | `web/components/ClientShell.tsx` |
| `ClientLayout` | Server-side layout that fetches user auth & branding | `web/app/(client)/layout.tsx` |
| `RootLayout` | Root Next.js layout with CSP nonce support | `web/app/layout.tsx` |
| `OperatorLoginPage` | Operator login (split layout, Google SSO) | `web/app/operator/(auth)/login/page.tsx` |
| `TenantLoginPage` | Customer login (email/password + SSO) | `web/app/(auth)/login/page.tsx` |

### 1.4 Authentication (Frontend)

**Client Auth**: Email/password login at `/login` with tenant selection for multi-tenant users. Uses session cookies with CSRF protection.

**Operator Auth**: Google Workspace SSO at `/operator/login` via OAuth redirect flow.

**Client Shell**: Fetches `/v1/me` and `/v1/branding` server-side; redirects to `/login` if unauthorized.

### 1.5 Design System

- **Colors**: CSS variables with operator branding (`--operator: #4d69a8`, gradients)
- **Typography**: Inter, Fraunces, JetBrains Mono
- **Spacing**: Design tokens (8pt grid)
- **Components**: Reusable UI components in `design/components/`
- **Responsive**: Mobile-first with breakpoints at 900px, 520px

## 2. Backend Architecture

### 2.1 Overview

Python 3.11/3.12 FastAPI application with async SQLAlchemy, PostgreSQL, Redis, dependency injection via Container pattern.

### 2.2 Directory Structure

```
src/
├── account_intelligence/
│   ├── api/                       # FastAPI routes & middleware
│   │   ├── main.py                 # ASGI entry point, middleware, exception handlers
│   │   ├── auth.py                 # Identity verification (JWT + sessions)
│   │   ├── guard.py                # Route guards & authorization
│   │   ├── tenant_plane.py         # Identity broker wiring
│   │   └── routes/                 # API route handlers
│   ├── app/                        # Application container/wiring
│   │   └── container.py            # Dependency injection container
│   ├── workflow/                   # Research workflow engine
│   ├── runs/                       # Run lifecycle
│   └── platform/                   # Platform adapters
├── identity/                       # Identity & authentication
│   ├── local_password.py            # Argon2id password primitives
│   ├── membership.py                # Membership management
│   ├── sessions.py                 # Session management
│   └── roles.py                    # RBAC roles
├── tenancy/                        # Multi-tenancy
├── jobs/                           # Job processing
├── spend/                          # Billing/credits
└── admin/                          # Admin operations
```

### 2.2 Key Backend Files

| File | Description |
|------|-------------|
| `api/main.py` | FastAPI app with middleware, CSP headers, request ID, error handlers, lifespan management |
| `app/container.py` | DI container - selects adapters based on APP_ENV (hermetic vs GCP) |
| `api/auth.py` | Identity verification - accepts JWT bearer tokens or session cookies, checks membership cache |
| `api/guard.py` | Authorization - role-based access control, CSRF protection |
| `api/tenant_plane.py` | Tenant plane wiring (broker, secrets, mailer, etc.) |
| `identity/local_password.py` | Password hashing/verification with Argon2id |
| `workflow/engine.py` | Research workflow DAG execution |
| `workflow/spec.py` | Workflow specification loading & validation |

### 2.3 Authentication (Backend)

**Two credential types**:
1. **Session cookies** (`identity.sessions`) - Server-side sessions, token hash stored in `auth_sessions`, tenant resolved via `session_tenant()` lookup
2. **Bearer JWT** from configured issuer - Verified for signature, issuer, audience, expiry. `sub`, `tenant_id`, `role` claims

**Local Password Auth** (`/v1/auth/login`):
- Argon2id verification with constant-time work
- Rate limiting (IP + account based)
- Progressive lockout (5/1min, 8/5min, 12/1hr)
- MFA required for admin role (`admin_requires_mfa` in policy)
- Membership cache re-check every ~60s

**Session Management**:
- `auth_sessions` table with token hash, tenant_id, user_id, amr, step_up_at
- RLS enforced; tenant lookup uses SECURITY DEFINER function `session_tenant(token_hash)`
- CSRF protection for cookie-authenticated mutations

### 2.4 API Routes

| Route Group | Prefix | Description |
|-------------|--------|-------------|
| `health.router` | `/healthz`, `/readyz` | Health checks |
| `public_v1_router` | `/v1/auth/*` | Public auth endpoints |
| `v1_router` | `/v1/*` | Authenticated tenant routes |
| `operator_router` | `/operator/v1/*` | Operator console API |
| `tasks.router` | `/tasks/*` | Worker task endpoints |

### 2.5 Database Schema (Key Tables)

| Table | Purpose |
|-------|---------|
| `local_identities` | Global user credentials (email, password_hash, status) |
| `auth_sessions` | Server-side sessions (token_hash, tenant_id, user_id) |
| `memberships` | User-tenant-role mappings (RLS enabled) |
| `tenants` | Tenant organizations |
| `accounts` | Account intelligence data |
| `runs` | Research runs |
| `auth_events` | Audit log (insert-only) |

## 3. Infrastructure & Deployment

### 3.1 Local Development

**Docker Compose** stack:
- `db` (PostgreSQL 16 + pgvector) - `5432`
- `redis` - `6379`
- `api` (FastAPI) - `8080`
- `worker` (Worker service) - `8081`
- `web` (Next.js) - `3000`
- `fake-idp` (OIDC provider) - `9090`
- `gcs` (Cloud Storage emulator) - `4443`
- `tasks` (Cloud Tasks emulator) - `8123`

**Start:**
```bash
make up  # Full stack
make down # Stop
make seed # Seed pilot tenants
```

### 3.2 Configuration

- `.env` - Local env vars (gitignored)
- `config/` - YAML configs (auth.yaml, session.yaml, workflow_v1.yaml, etc.)
- `compose.yml` - Base compose config
- `compose.live.yml` - With real keys

## 4. Data Flow

### 4.1 Client Login Flow

1. User submits email/password at `/login`
2. Frontend calls `POST /v1/auth/login` (same-origin, CSRF)
3. API looks up credential via `local_auth_lookup()` (SECURITY DEFINER)
4. Argon2id verify → success/fail; failure increments counters
5. On success: select tenant (single/multi-tenant), check membership
6. Create session in tenant transaction → set cookie `__Host-ai_session`
7. Redirect to `/`

### 4.2 Operator Login Flow

1. User clicks "Continue with Google Workspace" at `/operator/login`
2. Redirect to `/api/operator/login?return_to=/operator`
3. Operator auth endpoint initiates OAuth with staff IdP (or fake IdP in dev)
4. Callback at `/api/operator/callback` verifies token, checks staff allowlist
5. Sets session cookie and redirects to operator console

### 4.3 Request Flow

1. Request → FastAPI middleware (request ID, headers, metrics, CSP)
2. Auth middleware (`identity()`) extracts JWT or session cookie
3. If cookie: `session_tenant(token_hash)` → load session → check membership
4. If JWT: verify signature, issuer, audience → check membership
5. Membership cache (`MembershipCache`) re-checks (60s TTL)
6. Guard checks roles (`require_role()`), CSRF (`csrf_refused` check)
7. Handler executes → response with security headers

## 5. Page Structure Documentation

### 5.1 Public/Auth Pages

| Page | Route | Description | Type |
|------|-------|-------------|------|
| Customer Login | `/login` | Email/password + SSO, tenant selection | (auth)/login/page.tsx |
| Forgot Password | `/forgot` | Request password reset | (auth)/forgot/page.tsx |
| Reset Password | `/reset` | Reset password with token | (auth)/reset/page.tsx |
| Verify Email | `/verify-email` | Email verification | (auth)/verify-email/page.tsx |
| Operator Login | `/operator/login` | Operator Google SSO (enhanced split layout) | operator/(auth)/login/page.tsx |

### 5.2 Client Pages

| Page | Route | Capabilities | Description |
|------|-------|--------------|-------------|
| Overview | `/` | `brief:public:read` | Dashboard/home |
| Accounts | `/accounts` | `brief:public:read` | Account list |
| Account Details | `/accounts/[domain]` | `brief:public:read` | Account view |
| Tables | `/accounts/[domain]/tables/[table]` | `brief:public:read` | Data tables |
| Chat | `/chat` | `brief:public:read` | Claude chat |
| Research Runs | `/runs` | `brief:public:read` | Run history/list |
| Run Detail | `/runs/[run_id]` | `brief:public:read` | Run details |
| Alerts | `/alerts` | `brief:public:read` | Signals & alerts |
| Audit | `/audit` | `audit:read` | Audit trail |
| Usage | `/usage` | `usage:read` | Usage & plan |
| Settings | `/settings` | `settings:manage` | Settings hub |
| Users | `/settings/users` | `users:manage` | Team management |
| CRM | `/settings/crm` | `settings:manage` | CRM write-back |
| Connectors | `/settings/connectors` | `settings:manage` | Integrations |
| Alerts Settings | `/settings/alerts` | `brief:public:read` | Alert preferences |
| Security | `/settings/security` | `sessions:revoke:self` | Security settings |
| SSO | `/settings/sso` | `settings:manage` | SSO config |
| Support | `/settings/support` | `settings:manage` | Support access |

### 5.3 Operator Pages

| Page | Route | Description |
|------|-------|-------------|
| Operator Home | `/operator` | Console overview |
| Tenants | `/operator/tenants` | Tenant management |
| Runs | `/operator/runs` | All runs across tenants |
| Monitors | `/operator/monitors` | System monitoring |
| Failures | `/operator/failures` | Failure analysis |
| Audit | `/operator/audit` | Operator audit trail |
| Configuration | `/operator/configuration` | System config |
| Spend | `/operator/spend` | Spend/cost analysis |
| Cost | `/operator/cost` | Cost reporting |
| Announcements | `/operator/announcements` | System announcements |
| Support | `/operator/support` | Support operations |
| Review | `/operator/review` | Review queue |

## 6. Security Features

- **RLS (Row-Level Security)** - Enforced per tenant on all tenant-scoped tables
- **Security DEFINER** functions for tenant/credential lookups
- **Argon2id** password hashing
- **CSRF protection** for mutations
- **Same-origin checks** for auth endpoints
- **CSP (Content Security Policy)** with nonce support
- **Security headers**: X-Content-Type-Options, X-Frame-Options, Referrer-Policy
- **Rate limiting** (IP + account)
- **Progressive lockout** after failed attempts
- **Admin MFA requirement** (configurable)
- **Session rotation** on step-up
- **Membership cache** for authorization (60s TTL)

## 7. Testing

| Test Suite | Description |
|------------|-------------|
| Unit tests | `tests/unit/` |
| API tests | `tests/api/` |
| Isolation tests | `tests/isolation/` (RLS, tenant isolation) |
| E2E tests | `web/e2e/` (Playwright) |
| Contract tests | OpenAPI contract verification |

## 8. Technical Specifications

### 8.1 Frontend Dependencies
- `next@16.4.0` (Turbopack, App Router)
- `react@19.3.0` / `react-dom@19.3.0`
- `typescript@6.0.3`
- `vitest@5.0.3` / `playwright@1.63.0`
- `tailwind-merge`, `clsx`, `lucide-react`
- `react-markdown`, `remark-gfm`

### 8.2 Backend Dependencies
- `fastapi@0.115` / `uvicorn[standard]@0.30`
- `sqlalchemy[asyncio]@2.0` / `asyncpg@0.29`
- `alembic@1.13` (migrations)
- `argon2-cffi@23.1.0` (passwords)
- `pyjwt[crypto]@2.9` (JWT)
- `redis@5.0` (caching, rate limiting)
- `pydantic@2.7` / `pydantic-settings@2.4`
- `langgraph@1.0` (workflow)
- `opentelemetry-api/sdk` (observability)

## 9. Recent Changes

- **Operator Login Page** - Redesigned with split layout, operator branding, improved spacing
- **Client Login Page** - Email/password + SSO with tenant selection
- **Local Password Auth** - Complete implementation with Argon2id, rate limiting, lockout

## 10. Conclusion

The platform is a well-architected multi-tenant SaaS application with strong security controls (RLS, Security DEFINER, constant-time auth), clean separation of concerns (Container DI pattern), and a polished UI design. It follows best practices with type safety (TypeScript + Pydantic), async I/O, comprehensive error handling, and extensive testing. The design system is consistent with CSS variables, responsive layouts, and accessibility considerations.
