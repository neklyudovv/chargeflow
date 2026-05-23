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

Chargeflow is a modular Django billing engine that manages the full subscription lifecycle - plans, subscriptions, invoices, and payments - driven by a domain-event bus with transactional integrity, whose handlers run as retriable Celery background tasks.

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

Chargeflow models a real billing engine as a **modular monolith** - no microservices; domain apps coordinate through an in-process event bus, with events and scheduled work handed off to Celery background workers:

* Define **plans** with pricing and billing intervals (day / week / month / year)
* Manage the **subscription lifecycle** through an explicit state machine
* **Invoices are generated automatically** in response to domain events, not by an endpoint
* **Payments** are attempted by background workers and reconciled through idempotent webhooks
* **Failed payments are retried automatically** by a scheduled Celery beat dunning job
* **Coordinated by an event bus** - apps react to domain events instead of calling each other directly; handlers are dispatched as retriable, idempotent Celery tasks after the transaction commits
* **Multi-tenant** by design - every request is scoped to an organization

## Architecture Highlights

The project follows a clean, layered structure with a strict separation of concerns. Each domain app owns its own vertical slice - domain, application, and API layers - which keeps business rules independent of the framework and easy to test. The apps stay decoupled by communicating through a shared **event bus** rather than importing one another's services, so a change in one domain rarely ripples into the others.

### 1. Domain apps

The billing model is split across five Django apps.
* `accounts` holds users, organizations, customers, and API keys.
* `plans` defines the available plans and their pricing.
* `subscriptions` and `invoices` each drive their own state machine — one for the subscription lifecycle, one for invoice states.
* `payments` handles payment attempts and incoming provider webhooks.

### 2. Event Bus (`infrastructure/events.py`)

The core of the design: a domain-event bus (`EventBus`) that keeps the apps decoupled. Instead of one app reaching into another, each publishes domain events and reacts to the ones it cares about. Events are dispatched via `transaction.on_commit()`, so handlers run **only after** the database transaction commits - never on a rolled-back state. Handlers are registered per app in `AppConfig.ready()`.

The bus stays the router; each handler is executed as a **Celery task** (see below), so the event -> task boundary lives in the registration, not in the business code.

Key flows:

* `SubscriptionCreated` / `SubscriptionActivated` -> an `Invoice` is generated automatically
* `PaymentSucceeded` -> invoice marked paid; `PaymentFailed` -> invoice failed -> subscription moves to `OVERDUE`

### 3. Background Jobs (Celery + Redis)

Event handlers are dispatched as **Celery tasks** rather than run inline, so slow work is decoupled from the request and survives a crash. Tasks retry transient errors with backoff, and every handler is **idempotent** - a unique-per-period constraint on invoices, state guards on settlement, and a no-duplicate guard on payment attempts, so a retry or a re-delivered event never double-issues or double-charges.

A **Celery beat** schedule runs a **dunning** cycle that re-issues failed invoices and re-triggers their payment, up to a bounded retry cap - the engine's automatic retry loop for failed billing.

Redis is the broker, `celery_worker` executes tasks, and `celery_beat` schedules them. With no broker configured (local dev, CI), tasks run in-process, so neither Redis nor a worker is needed to run or test the project.

### 4. Domain Layer (`domain/`)

Entities, state-machine transition tables, and domain events. Illegal state changes are impossible by construction: every transition goes through `model.transition_to(new_state)`, which raises `InvalidStatusTransition` on an illegal move.

### 5. Application Layer (`application/services.py`)

Orchestration and use cases. Services wrap their work in `@transaction.atomic` and publish domain events - keeping the API layer thin and the business rules in one place.

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
* `GET /api/invoices/` - List invoices (generated automatically by events)
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
