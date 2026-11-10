# API v1 upgrade

This release replaces the unversioned HTTP contract. Update clients together
with the application; old business routes have no compatibility aliases.
No database migration is required.

| Previous prefix | New prefix |
|---|---|
| `/api/auth/` | `/api/v1/auth/` |
| `/api/accounts/` | `/api/v1/` |
| `/api/accounts/keys/` | `/api/v1/api-keys/` |
| `/api/plans/`, `/api/subscriptions/`, `/api/invoices/` | Corresponding `/api/v1/` collection |
| `/api/payments/attempts/` | `/api/v1/payment-attempts/` |
| `/api/payments/webhook/` | `/api/v1/webhooks/payments/` |

- Lists return `{next, previous, results}`. Follow cursor URLs; `page_size` is 1–100, default 25.
- Errors return `{code, message, fields}`. Nested field paths use dots.
- Relation fields in responses use `_id` names.
- Customer and plan updates use PATCH; PUT is removed.
- Missing/foreign references in a create body return 400 field errors; inaccessible resource URLs return 404.
- Credential/invitation creation responses omit the former human-oriented `note` field.
- `/health/` returns `{"status":"ok"}` instead of plain text.
- JSON is the only request format. Unknown body fields and list query parameters are rejected.
- Subscription replay returns 200; payment-attempt replay retains 201. Both return the original resource ID.
- Service-key scopes and human role restrictions remain enforced. Raw keys are still returned once at creation.

Swagger is at `/api/docs/`, ReDoc at `/api/redoc/`, and the schema at `/api/schema/`.
The source contract is `openapi.yaml`. Regenerate it with:

```bash
python manage.py spectacular --validate --fail-on-warn --file openapi.yaml
```

CI rejects schema drift. HTTP tests validate actual response bodies against the
schema; they do not duplicate operation names and documentation text.
