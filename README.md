# chargeflow

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-6.0.9-092E20?logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.18-A30000?logo=django&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-compose-2496ED?logo=docker&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-5.5-37814A?logo=celery&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white)
![pytest](https://img.shields.io/badge/pytest-8.3-0A9EDC?logo=pytest&logoColor=white)

![CI](https://github.com/neklyudovv/chargeflow/actions/workflows/ci.yml/badge.svg)
![coverage](https://img.shields.io/badge/coverage-85%25-brightgreen)

Chargeflow is a modular Django billing engine that manages the full subscription lifecycle - plans, subscriptions, invoices, and payments - where state changes are direct service calls in one transaction, and Celery handles the payment call and the schedule.

## Table of Contents

* [Technologies](#technologies)
* [Solution Overview](#solution-overview)
* [Architecture Highlights](#architecture-highlights)
* [API Endpoints](#api-endpoints)
* [Installation and Launch](#installation-and-launch)
* [TODO](#todo)

## Technologies

* **Django** - web framework and ORM
* **Django REST Framework** - REST API layer (ViewSets, serializers)
* **PostgreSQL** - database
* **Docker** - containerized deployment via docker-compose
* **Celery + Redis** - background task processing and scheduled billing runs (dunning)
* **Github Actions** - CI
* **drf-spectacular** - OpenAPI 3 schema and interactive Swagger / Redoc docs
* **Pytest** (+ pytest-django, pytest-cov) - integration and unit testing
* **Token & API-key auth** - user sessions and service-to-service access

## Solution Overview

Chargeflow models a real billing engine as a **modular monolith** - no microservices; domain apps call each other's services directly, and Celery runs the charge and the scheduled work:

* Define **plans** with pricing and billing intervals (day / week / month / year)
* Manage the **subscription lifecycle** through an explicit state machine
* **Invoices are generated automatically** when a subscription is created, activated or renewed, not by an endpoint
* **Payments** are attempted through a provider gateway by background workers and reconciled through idempotent webhooks
* **Failed payments are retried automatically** by a scheduled Celery beat dunning job
* **Synchronous core** - a state change and its consequences are direct service calls in one transaction under row locks; only the charge is handed to a Celery worker, after the commit
* **Multi-tenant** by design - every request is scoped to an organization

## Architecture Highlights

The project follows a clean, layered structure with a strict separation of concerns. Each domain app owns its own vertical slice - domain, application, and API layers - which keeps business rules independent of the framework and easy to test. Services call each other in one direction (subscriptions -> invoices, payments -> invoices and subscriptions), so the dependencies stay visible.

### 1. Domain apps

The billing model is split across five Django apps.
* `accounts` holds users, organizations, customers, and API keys.
* `plans` defines the available plans and their pricing.
* `subscriptions` and `invoices` each drive their own state machine — one for the subscription lifecycle, one for invoice states.
* `payments` handles payment attempts and incoming provider webhooks.

### 2. Synchronous core

State changes are direct service calls inside one `@transaction.atomic` block, each row re-read under `select_for_update()`. Creating, activating or renewing a subscription generates its invoice in the same transaction; a charge result is applied by `PaymentService.settle` (attempt -> invoice -> subscription) and a cancellation closes the open invoices.

Every fact has one consumer. An in-process event bus gave the core extra hops and extra places to lose a change, not decoupling.

### 3. Background Jobs (Celery + Redis)

Celery is for external I/O and the schedule. `charge_invoice` runs on the `io` queue and is handed to the worker with `transaction.on_commit` when an invoice is issued, so it never sees a rolled-back state. The beat cycles run on the `batch` queue and call the same services:

* `run_dunning` retries on days 1, 3 and 7 after the first failure; a failed final retry cancels the subscription for `non_payment`
* `run_renewals` rolls expired subscriptions into the next period
* `run_trial_activations` activates trials that have ended
* `reconcile_stuck` resumes abandoned PENDING attempts and re-hands issued invoices with no attempt after ten minutes

Tasks retry transient database errors with backoff and are idempotent: a unique-per-period constraint on invoices, state guards under the lock, and separate client and provider idempotency keys on payment attempts.

### 4. Payment gateway

Chargeflow currently ships only a durable mock gateway; it does not connect to a real processor. Customers may hold optional provider customer and payment-method tokens, but Chargeflow accepts no raw card fields.

Charging has three phases: commit or recover a `PENDING` attempt and snapshot its amount, currency and provider tokens; call the gateway without an open database transaction or row lock; then settle in a new transaction under subscription, invoice and attempt locks. `PaymentService.attempt` and `PaymentService.charge_issued` reject callers already inside an atomic block. An ambiguous transport failure leaves the attempt pending, and `reconcile_stuck` retries the same immutable provider request and key unless it is already flagged for manual reconciliation. The mock stores a one-way request fingerprint and its result separately, so replay returns the first result even after a process restart or a failure-rate change without duplicating provider tokens in the receipt ledger.

Provider webhooks use the same settlement path. The request is JSON with this shape:

```json
{
  "provider": "mock",
  "provider_event_id": "evt_123",
  "event_type": "payment.updated",
  "payload": {
    "payment_attempt_id": 42,
    "status": "success",
    "provider_charge_id": "mock_123"
  }
}
```

Send `X-Webhook-Signature: t=<unix>,v1=<hex>` where the hex value is HMAC-SHA256 over `<unix>.<raw request bytes>` using `WEBHOOK_SECRET`. Timestamps more than 300 seconds in the past or future, malformed signatures, non-JSON requests, unknown providers, invalid attempt IDs and statuses are rejected. Events are deduplicated by provider and provider event ID; the first committed payload wins. A well-formed event for an unknown payment is acknowledged and retained without changing billing state. Retry deliveries with the same `provider_event_id` and a fresh signed timestamp; body-only signatures are no longer accepted.

Redis is the broker, `celery_worker` consumes the `io` and `batch` queues, and `celery_beat` schedules the cycles. Inline tasks are available only when explicitly enabled in DEBUG mode; normal deployments require a broker and worker.

### 5. Domain Layer (`domain/`)

Entities and state-machine transition tables. Illegal state changes are impossible by construction: every transition goes through `model.transition_to(new_state)`, which raises `InvalidStatusTransition` on an illegal move.

### 6. Application Layer (`application/services.py`)

Orchestration and use cases. Services wrap their work in `@transaction.atomic` and call the services of the apps they depend on - keeping the API layer thin and the business rules in one place.

### 7. API Layer (`api/`)

Built on **Django REST Framework**: ViewSets, serializers, and routing. This layer is a thin adapter between HTTP and the services, plus authentication and org resolution.

### 8. State Machines

* **Subscription:** `TRIAL -> ACTIVE -> OVERDUE -> CANCELED`
* **Invoice:** `DRAFT -> ISSUED -> PAID | FAILED | OVERDUE | CANCELED` (with `FAILED -> ISSUED` retry)

### 9. Multi-tenancy

Every authenticated request resolves an organization (API key -> `X-Organization-Id` header -> implicit single org), and **all querysets are scoped to it**, so tenants can never read each other's data.

### 10. Docker Environment

`docker-compose` runs the following services:

* `backend` - Django application (gunicorn)
* `db` - PostgreSQL
* `redis` - Celery broker
* `celery_worker` - background task worker
* `celery_beat` - scheduler

## API Endpoints

The public business API is versioned under `/api/v1/`.

- Swagger: `/api/docs/`
- ReDoc: `/api/redoc/`
- Downloadable schema: `/api/schema/` (JSON: `/api/schema/?format=json`)
- Committed OpenAPI 3.1 contract: [`openapi.yaml`](openapi.yaml)
- Public liveness probe: `GET /health/` → `{"status":"ok"}`

| Resource | Routes |
|---|---|
| Authentication | `POST /api/v1/auth/register/`, `POST /api/v1/auth/login/` |
| Current account | `GET /api/v1/me/` |
| Organizations | `GET`, `POST /api/v1/organizations/` |
| API keys | `GET`, `POST /api/v1/api-keys/`; `DELETE /api/v1/api-keys/{id}/` |
| Team | `GET /api/v1/members/`; `GET`, `POST /api/v1/invitations/`; `POST /api/v1/invitations/accept/` |
| Customers | `GET`, `POST /api/v1/customers/`; `GET`, `PATCH /api/v1/customers/{id}/` |
| Plans | `GET`, `POST /api/v1/plans/`; `GET`, `PATCH`, `DELETE /api/v1/plans/{id}/` |
| Subscriptions | `GET`, `POST /api/v1/subscriptions/`; `GET /api/v1/subscriptions/{id}/`; `POST .../{id}/activate/`, `.../{id}/renew/`, `.../{id}/cancel/` |
| Invoices | `GET /api/v1/invoices/`, `GET /api/v1/invoices/{id}/` |
| Payments | `GET`, `POST /api/v1/payment-attempts/`; `GET /api/v1/payment-attempts/{id}/` |
| Provider events | `POST /api/v1/webhooks/payments/` |

Requests and responses use JSON. Lists return `{next, previous, results}` with
opaque cursors; the default page size is 25, and `page_size` accepts 1–100.
Errors use `{code, message, fields}`. Money is a decimal string with its currency;
timestamps use ISO 8601 with a timezone. Use the documented trailing slashes.

User tokens use `Authorization: Token <token>`; service keys use
`Authorization: Bearer cf_<key>`. A user with several memberships selects a
project through `X-Organization-Id`. Service keys remain bound to their project.
Registration creates a user; create an organization separately to obtain its
initial API key.

See the [API v1 integration and upgrade notes](docs/api-v1.md) for filters,
replay behavior, error examples and the breaking changes from unversioned routes.

## Installation and Launch

Python 3.12, PostgreSQL 16 and Redis 7 are supported. PostgreSQL is required in every environment; there is no SQLite fallback.

```bash
git clone https://github.com/neklyudovv/chargeflow.git
cd chargeflow
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Generate a separate value for each of `DJANGO_SECRET_KEY`, `WEBHOOK_SECRET` and `POSTGRES_PASSWORD` and fill them in `.env`. Empty secrets and known placeholders are rejected. Set `DJANGO_ALLOWED_HOSTS` for the deployment. `PAYMENT_FAILURE_RATE` controls the mock provider; it defaults to zero and Compose does not override it.

```bash
docker compose up --build --wait
```

The API and Swagger documentation are at `http://localhost:8000/api/docs/`. The migration service must finish successfully before backend, worker or beat starts. Health checks exercise the database and broker, plus HTTP, worker ping or the beat scheduler heartbeat. Run one beat instance. The image runs as an unprivileged user and contains only runtime dependencies.

For later schema updates, stop the application processes and rerun the one-shot service before restarting them:

```bash
docker compose stop backend celery_worker celery_beat
docker compose build
docker compose run --rm migrate
docker compose up -d --build --wait
```

### Local Python development and tests

Install dependencies in a virtual environment with `pip install -r requirements-dev.txt`. Start infrastructure with `docker compose up -d db redis`. For commands outside Docker, override the container hostnames:

```bash
export POSTGRES_HOST=localhost
export CELERY_BROKER_URL=redis://localhost:6379/0
export REDIS_THROTTLE_URL=redis://localhost:6379/1
python manage.py migrate
python manage.py runserver
```

Secrets and other database values are read from `.env`. PostgreSQL must allow creating a test database. Inline Celery execution requires both `DJANGO_DEBUG=True` and the explicit `CELERY_TASK_ALWAYS_EAGER=True`; it is never selected just because a broker URL is missing.

```bash
DJANGO_DEBUG=True CELERY_TASK_ALWAYS_EAGER=True pytest
pip-audit -r requirements.txt
```

CI runs PostgreSQL tests with the coverage gate, the real-worker billing chain, dependency auditing and a complete Compose smoke test. Docker exposes PostgreSQL and Redis only on the loopback interface for local commands.

### Existing SQLite data

The old `db.sqlite3` file is not deleted or converted automatically. If it contains data you need, keep a backup and use the old revision and its environment to export application data with Django `dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission`. Store the export securely; it contains user information and authentication material. With the new PostgreSQL database migrated, import it using `loaddata` and verify counts and application access before retiring the old database. Do not run both databases as active billing systems.

## TODO

* [x] Background jobs (Celery / Redis) for async billing runs
* [x] Dunning / retry logic for failed payments
* [ ] Real payment provider integration (currently a mock provider)
* [ ] Invoice PDF generation
* [ ] Email notifications
* [ ] Reporting / analytics app

## Project access and payment request isolation

User tokens act through organization memberships. OWNER and ADMIN may change
project data and create/revoke API keys; MEMBER has read-only access. Users with
multiple memberships select a project with `X-Organization-Id`.

API keys represent their own project, never its owner. Key creation accepts
`scope: "read"` or `scope: "read_write"` (the default, including existing keys).
Read keys cannot mutate project data. All keys are denied access to user profile,
organization administration, members, invitations and API-key management; use a
user token for those endpoints. A key cannot select another project with a header.

`Idempotency-Key` on payment creation is unique within a project and bound to one
invoice. Replaying it for that invoice returns the original attempt; using it for
a different invoice returns HTTP 409. Different projects may use the same key.
An issued invoice that already has an attempt also rejects a new key with 409;
retry failed payments through the existing reissue flow.

The upgrade backfills organization IDs on subscriptions, invoices, invoice lines,
payment attempts and resolvable webhook events. A historical subscription whose
customer and plan belong to different projects stops migration with a clear error;
correct that inconsistent data before retrying. Unknown-payment webhook events
are preserved with a null organization, including newly acknowledged unknown
notifications; they cannot change billing state. Other billing records require an
organization matching their parent. Back up the database before migration.

### Billing periods and payment retries

Renewals start at the previous period end, preserving the original day of the
month across short months and leap years. An expired trial starts its paid
period at the trial end; an early manual activation starts it immediately. Each
renewal advances one period, so missed periods are billed in order.

The first declined payment makes the subscription `OVERDUE`. Automatic retries
are due 1, 3 and 7 days after that first failure and run on the next hourly
dunning cycle. The initial payment plus three retries gives four attempts. A
failed final retry leaves the invoice `OVERDUE` and cancels the subscription with
`non_payment`. A successful payment clears the retry schedule and restores an
overdue subscription once its other failed/overdue invoices are paid. Manual
payment requests remain available; they count toward the same retry limit.
Invoice responses expose `attempt_count` (charges prepared, including pending
and successful charges), `first_failed_at` and `next_retry_at`.

`POST /api/v1/subscriptions/` accepts an optional `Idempotency-Key` header (1–255
characters), scoped to the project. The first request returns 201; a replay
with the same customer and plan returns the original subscription with 200,
even after cancellation. Reusing the key with different ids returns 409. A
second live subscription (`TRIAL`, `ACTIVE` or `OVERDUE`) for the same customer
and plan also returns 409; a canceled subscription permits a new request with
a new key.

Plans require nonnegative prices and trial days, a known billing interval and
an uppercase ISO 4217 currency code. The currency snapshot is taken from
[SIX List One](https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml).

Before upgrading, resolve duplicate live customer/plan subscriptions and
negative prices or trial days. Migrations reject these rows instead of
canceling subscriptions or changing prices automatically. Existing invoices
receive attempt counts and retry dates from their recorded attempts; historical
first-failure times are approximated by the first declined attempt's timestamp.
Existing subscriptions retain their current period's calendar day as the anchor.
