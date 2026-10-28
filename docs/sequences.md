# Sequences

These match the contracts in [api-conventions.md](api-conventions.md) and [api-resources.md](api-resources.md).

## Clients

```mermaid
flowchart LR
    web[Web browser]
    android[Android app]
    api[Django REST API]
    db[(MySQL 8.4)]

    web -->|"Client-Type: web, HttpOnly cookies"| api
    android -->|"Client-Type: mobile, Bearer JSON"| api
    api --> db
```

## Login and refresh

```mermaid
sequenceDiagram
    participant C as Client
    participant API as API
    participant DB as Database

    C->>API: POST /api/token/ email + password
    API->>DB: Authenticate users.User
    alt Client-Type web
        API-->>C: 200 message + access_token and refresh_token cookies
    else mobile or omitted
        API-->>C: 200 access and refresh JSON
    end

    C->>API: Protected request with Bearer or access cookie
    API->>DB: Load user and role
    API-->>C: Resource or 401

    alt mobile refresh
        C->>API: POST /api/token/refresh/ JSON refresh
        API-->>C: 200 new access and refresh
    else web refresh
        C->>API: POST /api/token/refresh/ Client-Type web + cookie
        API-->>C: 200 message + rotated cookies
    end

    C->>API: POST /api/token/logout/
    API->>DB: Blacklist refresh token
    API-->>C: 204 and clear cookies when web
```

Web refresh, cookie authentication, and logout are still to build. Mobile login and JSON refresh exist.

## Academic setup

```mermaid
sequenceDiagram
    participant Admin
    participant API

    Admin->>API: POST /api/academics/
    Admin->>API: POST /api/semesters/
    Admin->>API: POST /api/modules/
    Admin->>API: POST /api/users/ role STUDENT or TEACHER
    Admin->>API: POST /api/enrollments/bulk/
    API-->>Admin: created and skipped
```

Only one academic year is current. Semester dates sit inside that year.

## Assign a module and publish the plan

```mermaid
sequenceDiagram
    participant A as Admin
    participant T as Teacher
    participant API

    A->>API: POST /api/lecture-plans/ module + lecturer
    API-->>A: DRAFT, academic_id set from the module
    loop each assessment type
        T->>API: POST /api/assessment-distributions/
        T->>API: POST /api/assessments/ until count matches total_assessment
    end
    T->>API: POST /api/lecture-plans/{id}/publish/
    alt every type is complete and marks are positive
        API-->>T: 200 PUBLISHED
    else checks fail
        API-->>T: 409 fields, still DRAFT
    end
```

A student does not see the plan until it is `PUBLISHED` and they are enrolled. Only an admin creates a plan — offering a module assigns its teacher — and the teacher then fills in the assessments.

## Record marks

```mermaid
sequenceDiagram
    participant T as Teacher
    participant API
    participant DB as Database

    T->>API: POST /api/assessments/{id}/open/
    T->>API: POST /api/assessment-results/bulk/
    API->>DB: Upsert results
    API-->>T: 200 saved results
    T->>API: POST /api/assessments/{id}/close/
    T->>API: POST /api/assessments/{id}/mark-graded/
    alt every ENROLLED student has a mark
        API-->>T: 200 GRADED
    else a mark is missing
        API-->>T: 409
    end
```

`DROPPED` enrollments are not required for `GRADED`.

## Distribution completion

```mermaid
sequenceDiagram
    participant T as Teacher
    participant API
    participant DB as Database

    T->>API: POST /api/assessments/{id}/mark-graded/
    API->>DB: Set assessment GRADED
    API->>DB: Count assessments in the distribution not yet GRADED
    alt none left
        API->>DB: Set distribution COMPLETED
        alt distribution type is FINAL
            API-->>T: 200 GRADED, distribution COMPLETED, run finished
        else other type
            API-->>T: 200 GRADED, distribution COMPLETED
        end
    else some left
        API-->>T: 200 GRADED, distribution still PENDING
    end
```

The check runs in the same transaction as `mark-graded`. The model stops when the `FINAL` distribution is `COMPLETED`. Nothing is computed or stored after that.

## Student reads own marks

```mermaid
sequenceDiagram
    participant S as Student
    participant API

    S->>API: GET /api/me/enrollments/
    API-->>S: Own modules
    S->>API: GET own assessment results
    API-->>S: Marks for published plans only
    S->>API: Request another student's result
    API-->>S: 404
```

The results route and its filters are defined in api-resources.md.
