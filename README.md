# chargeflow

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django](https://img.shields.io/badge/Django-6.0-092E20?logo=django&logoColor=white)
![DRF](https://img.shields.io/badge/DRF-3.16-A30000?logo=django&logoColor=white)
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
* **SQLite** - zero-config database for local development
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
* **Payments** are attempted by background workers and reconciled through idempotent webhooks
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

* `run_dunning` re-issues failed invoices up to a retry cap, then marks them overdue and cancels the subscription
* `run_renewals` rolls expired subscriptions into the next period
* `run_trial_activations` activates trials that have ended
* `reconcile_stuck` re-hands the charge of an issued invoice that has no attempt after ten minutes, in case the hand-off was lost

Tasks retry transient database errors with backoff and are idempotent: a unique-per-period constraint on invoices, state guards under the lock, and a no-duplicate guard on payment attempts.

Redis is the broker, `celery_worker` consumes the `io` and `batch` queues, and `celery_beat` schedules the cycles. With no broker configured (local dev, CI), tasks run in-process, so neither Redis nor a worker is needed to run or test the project.

### 4. Domain Layer (`domain/`)

Entities and state-machine transition tables. Illegal state changes are impossible by construction: every transition goes through `model.transition_to(new_state)`, which raises `InvalidStatusTransition` on an illegal move.

### 5. Application Layer (`application/services.py`)

Orchestration and use cases. Services wrap their work in `@transaction.atomic` and call the services of the apps they depend on - keeping the API layer thin and the business rules in one place.

### 6. API Layer (`api/`)

Built on **Django REST Framework**: ViewSets, serializers, and routing. This layer is a thin adapter between HTTP and the services, plus authentication and org resolution.

### 7. State Machines

* **Subscription:** `TRIAL -> ACTIVE -> OVERDUE -> CANCELED`
* **Invoice:** `DRAFT -> ISSUED -> PAID | FAILED | OVERDUE | CANCELED` (with `FAILED -> ISSUED` retry)

### 8. Multi-tenancy

Every authenticated request resolves an organization (API key -> `X-Organization-Id` header -> implicit single org), and **all querysets are scoped to it**, so tenants can never read each other's data.

### 9. Docker Environment

`docker-compose` runs the following services:

* `backend` - Django application (gunicorn)
* `db` - PostgreSQL
* `redis` - Celery broker
* `celery_worker` - background task worker
* `celery_beat` - scheduler

## API Endpoints

Interactive documentation is generated from the code with **drf-spectacular** and served at:

* **Swagger UI** — `/api/docs/`
* **Redoc** — `/api/redoc/`
* **OpenAPI schema** — `/api/schema/`

The endpoints below are grouped by domain.

### Authentication (`/api/auth`)
* `POST /api/auth/register/` - Register a user (and create their organization)
* `POST /api/auth/login/` - Log in and obtain an auth token

### Accounts (`/api/accounts`)
* `GET /api/accounts/me/` - Current authenticated user
* `GET|POST /api/accounts/organizations/` - List / create organizations
* `GET|POST /api/accounts/customers/` - List / create customers
* `GET|PATCH /api/accounts/customers/{id}/` - Retrieve / update a customer
* `GET /api/accounts/members/` - List organization members and roles
* `GET|POST /api/accounts/invitations/` - List / send invitations
* `POST /api/accounts/invitations/accept/` - Accept an invitation
* `GET|POST /api/accounts/keys/` - List / create API keys (service-to-service)
* `DELETE /api/accounts/keys/{id}/` - Revoke an API key

### Plans (`/api/plans`)
* `GET|POST /api/plans/` - List / create plans
* `GET|PATCH|DELETE /api/plans/{id}/` - Retrieve / update / delete a plan

### Subscriptions (`/api/subscriptions`)
* `GET|POST /api/subscriptions/` - List / create subscriptions
* `GET /api/subscriptions/{id}/` - Retrieve a subscription
* `POST /api/subscriptions/{id}/activate/` - Activate (trial -> active)
* `POST /api/subscriptions/{id}/renew/` - Renew the billing period
* `POST /api/subscriptions/{id}/cancel/` - Cancel

### Invoices (`/api/invoices`)
* `GET /api/invoices/` - List invoices (generated automatically by the subscription lifecycle)
* `GET /api/invoices/{id}/` - Retrieve an invoice

### Payments (`/api/payments`)
* `GET|POST /api/payments/attempts/` - List / create payment attempts
* `GET /api/payments/attempts/{id}/` - Retrieve a payment attempt
* `POST /api/payments/webhook/` - Provider webhook (idempotent, deduplicated)

## Installation and Launch

1. Clone the project:
   ```bash
   git clone https://github.com/neklyudovv/chargeflow.git
   cd chargeflow
   ```

2. Set up the `.env` file based on `.env.example`

3. Run the project with Docker:
   ```bash
   docker-compose up --build
   ```

4. API will be available at:
   ```
   http://localhost:8000/
   ```

> **Local development (SQLite, no Docker)**
> ```bash
> python manage.py migrate
> python manage.py runserver
> ```
>
> **Run tests:**
> ```bash
> pytest
> ```
>
> **CI/CD:**
> Github Actions runs the full test suite against PostgreSQL on every push and pull request to `main`, and fails the build if coverage drops below 70%. See `.github/workflows/ci.yml`.

## TODO

* [x] Background jobs (Celery / Redis) for async billing runs
* [x] Dunning / retry logic for failed payments
* [ ] Real payment provider integration (currently a mock provider)
* [ ] Invoice PDF generation
* [ ] Email notifications
* [ ] Reporting / analytics app
