# Development docs

Contracts for the Assessment Based Student Performance Analysis API. The entity model stays in the [root README](../README.md).

## Scope

This phase covers assessment only:

1. Assign a module to a lecturer through a lecture plan.
2. Define the assessment distribution and its assessments.
3. Record each student's mark per assessment.

A distribution is `COMPLETED` when every assessment in it is `GRADED`. The `FINAL` distribution reaching `COMPLETED` finishes the module's assessment run, and the model stops there. Each module's run stands alone. Nothing is combined across modules, semesters, or academic years.

| Doc | What it locks |
| --- | --- |
| [API conventions](api-conventions.md) | Auth, clients, errors, pagination, roles |
| [Resource contract](api-resources.md) | CRUD routes, bodies, and domain checks |
| [Sequences](sequences.md) | Login, publish, marking, and distribution completion |
| [Development plan](development-plan.md) | Apps, build order, and acceptance checks |

Implemented today:

- `POST /api/token/`
- `POST /api/token/refresh/` for a JSON refresh token

Every other route in these docs is the contract to build. Cookie auth, logout, and blacklist are required before the web client can call protected routes.
