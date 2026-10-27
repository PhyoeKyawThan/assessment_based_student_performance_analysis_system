# API conventions

Base URL is the Django host. Bodies are `application/json`. Field names are `snake_case`. Primary keys are UUID strings and are returned as `id`.

Timestamps are ISO-8601 in UTC. Dates are `YYYY-MM-DD`. Decimal values are JSON strings with up to two places, for example `"18.50"`, so mark math does not depend on binary floats.

## Clients

| Client | Header | Access after login |
| --- | --- | --- |
| Web | `Client-Type: web` | HttpOnly cookies. No tokens in the JSON body. |
| Android and any other caller | `Client-Type: mobile`, or omit the header | JSON `access` and `refresh`. |

Unknown `Client-Type` values follow the mobile path. That matches `CustomTokenObtainPairView`, which defaults the header to `mobile`.

Production cookies:

| Cookie | Max age | Flags |
| --- | --- | --- |
| `access_token` | 60 minutes | `HttpOnly`, `Secure`, `SameSite=Lax` |
| `refresh_token` | 7 days | `HttpOnly`, `Secure`, `SameSite=Lax` |

`Secure` is off when `DEBUG` is true so local HTTP still works. Lifetimes match `SIMPLE_JWT`.

### Browser callers

A page served from another origin can only reach the API if `django-cors-headers` allows it:

| Setting | Value |
| --- | --- |
| `CORS_ALLOWED_ORIGINS` | space-separated from the environment, defaulting to the Expo web dev ports `http://localhost:8081`, `:19000`, `:19006` |
| `CORS_ALLOWED_ORIGIN_REGEXES` | only when `DEBUG=True`: any host on those ports, because `expo start --host lan` is reached through the machine's LAN address |
| `CORS_ALLOW_HEADERS` | the defaults plus `client-type`, which selects the table above |
| `CORS_ALLOW_CREDENTIALS` | `True`, so the cookie path works from an allowed origin |

A rejected preflight shows up in the client as `TypeError: NetworkError when attempting to fetch resource.`, the same symptom as a missing `client-type` in `CORS_ALLOW_HEADERS`, so check the response for an `Access-Control-Allow-Origin` header before debugging the client. Deployments must not rely on the `DEBUG` regexes: set `CORS_ALLOWED_ORIGINS` to the exact origins instead, since credentialed requests plus a host wildcard would let any local page call the API.

## Authentication

Login uses email. `users.User.USERNAME_FIELD` is `email`.

Protected routes accept either:

1. `Authorization: Bearer <access>`
2. The `access_token` cookie, only when the Bearer header is absent

Bearer wins when both are present. `users.authentication.CookieJWTAuthentication` reads the `access_token` cookie only when no `Authorization` header is sent, so the web client can call protected routes without a token in the body.

Access lifetime is 60 minutes. Refresh lifetime is 7 days. Refresh rotation is on. `BLACKLIST_AFTER_ROTATION` is set, but `rest_framework_simplejwt.token_blacklist` is not installed. Add that app and migrate it before logout or rotation can revoke a refresh token.

Role is loaded from the database on each request. Clients do not send a role claim.

## Roles

| Role | Scope |
| --- | --- |
| `ADMIN` and superuser | Full catalog, enrollment, plans, and marks. |
| `TEACHER` | Own lecture plans, assessments, and marks for those modules. Read-only catalog. |
| `STUDENT` | Own enrollments, published plans for those modules, and own marks. |

`IsLecturerRole` also allows `ADMIN`. Object-level checks below are stricter than that class: a teacher only writes modules they own.

A student who asks for another student's row receives `404`, not `403`, so the API does not reveal that the row exists.

## Pagination and filters

List routes use page-number pagination.

| Query | Default | Max |
| --- | --- | --- |
| `page` | `1` |  |
| `page_size` | `20` | `100` |

```json
{
  "count": 42,
  "next": "http://localhost:8000/api/modules/?page=2",
  "previous": null,
  "results": []
}
```

Filters listed on each route are exact matches unless the contract says otherwise. Unknown query keys are ignored.

## Status codes

| Code | When |
| --- | --- |
| `200` | Read or update succeeded. |
| `201` | Create succeeded. |
| `204` | Delete succeeded. Empty body. |
| `400` | Body fails validation. |
| `401` | Missing or expired access token. |
| `403` | Authenticated, wrong role or not the owner. |
| `404` | Missing row, or hidden from this caller. |
| `409` | Unique rule or illegal state change. |

## Error body

New routes use one shape. SimpleJWT login and refresh keep their own `detail` string.

```json
{
  "code": "validation_error",
  "detail": "Lecture plan cannot be published.",
  "fields": {
    "percentage": ["Percentages on this plan sum to 80, not 100."]
  }
}
```

| `code` | Typical status |
| --- | --- |
| `validation_error` | `400` |
| `authentication_failed` | `401` |
| `permission_denied` | `403` |
| `not_found` | `404` |
| `conflict` | `409` |

`fields` is omitted when the error is not tied to a field.

## Writes

- Create and update responses return the same representation as retrieve.
- `PATCH` changes only the fields that are present.
- `PUT` is not used.
- Deletes that would orphan marks, or break a published scheme, return `409`.
- Bulk writes are atomic. If one row fails, nothing in that request is saved.

## Push notifications

Two events reach a device: a mark written for a student, and an assessment that moves to `OPEN`. The
message is sent after the API has committed its write, in the same request, to `https://exp.host/--/api/v2/push/send`.

`EXPO_ACCESS_TOKEN` in `.env` is optional. Without it every send is logged and skipped, so a local
run never reaches Expo. `manage.py test` blanks the token before the suite starts, and the push
tests patch the transport, so no test can deliver for real.

A token the service reports as `DeviceNotRegistered` is cleared, which is what happens after a
device is wiped or the app is uninstalled. The client registers again on its next sign-in.
