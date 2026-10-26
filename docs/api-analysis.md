# Analysis contract

Read models on top of `assessment_result`, `module_performance`, and `grade_scale`. Ranking and cohort figures are computed in the request. They are not stored.

Who can call:

| Route | Admin | Teacher | Student |
| --- | --- | --- | --- |
| `GET /api/me/performance/` | no | no | own rows |
| `GET /api/analysis/students/{student_id}/` | yes | modules they teach, other modules hidden | own id only, else `404` |
| `GET /api/analysis/modules/{module_id}/` | yes | own module | `404` |
| `GET /api/analysis/academics/{academic_id}/` | yes | own modules inside that year | `404` |

## Snapshot formula

For one enrollment, group results by `assessment_distribution`.

```text
distribution_ratio   = sum(marks_obtained) / sum(max_mark of assessments that have a result)
contribution         = distribution_ratio * percentage
obtained_percentage  = sum(contribution) over distributions that have at least one result
```

Missing results are left out of both sums. The snapshot then understates the final module mark until those items are graded. The response field `coverage_percentage` is the sum of `percentage` for distributions that have at least one result for this student, so a client can show "40 of 100 weight marked".

`obtained_percentage` is rounded to two decimal places, half up.

The academic year's `grade_scale` row whose range contains `obtained_percentage` supplies `letter_grade` and `grade_point`. If no band matches, the write still saves the percentage and returns `409` on the result request with `fields.grade_scale`. Fix the scale, then recompute.

Stored row:

```json
{
  "id": "<uuid>",
  "enrollment_id": "<uuid>",
  "module_id": "<uuid>",
  "student_id": "<uuid>",
  "grade_scale_id": "<uuid>",
  "obtained_percentage": "76.50",
  "coverage_percentage": "100.00",
  "letter_grade": "B",
  "grade_point": "3.00",
  "computed_at": "2027-03-20T09:00:00Z"
}
```

`letter_grade` and `grade_point` are copies taken at `computed_at`. A later edit to `grade_scale` does not change old snapshots until recompute.

`DROPPED` enrollments have no snapshot. If a student is dropped after marks exist, the next recompute deletes that snapshot.

## `GET /api/analysis/students/{student_id}/`

Query: `academic_id` optional. Without it, every snapshot for that student is returned, grouped by academic year.

`200`:

```json
{
  "student": {
    "id": "<uuid>",
    "full_name": "Aye Aye",
    "email": "aye@university.edu"
  },
  "academic_years": [
    {
      "academic_id": "<uuid>",
      "academic_year": "2026-2027",
      "gpa": "3.25",
      "total_credit_hours": 12,
      "modules": [
        {
          "module_id": "<uuid>",
          "module_code": "CS101",
          "module_name": "Programming Fundamentals",
          "credit_hours": 3,
          "enrollment_status": "COMPLETED",
          "obtained_percentage": "76.50",
          "coverage_percentage": "100.00",
          "letter_grade": "B",
          "grade_point": "3.00",
          "rank": 4,
          "cohort_size": 40,
          "components": [
            {
              "assessment_type": "QUIZ",
              "percentage": 20,
              "marks_obtained": "62.00",
              "max_mark": "80.00",
              "contribution": "15.50"
            }
          ]
        }
      ]
    }
  ]
}
```

GPA uses modules in that year whose enrollment is `COMPLETED` and whose `coverage_percentage` is `100.00`.

```text
gpa = sum(grade_point * credit_hours) / sum(credit_hours)
```

Rounded to two decimal places, half up. If no module qualifies, `gpa` is `null` and `total_credit_hours` is `0`.

`rank` is 1-based among `ENROLLED` and `COMPLETED` students in the same module who have a snapshot, ordered by `obtained_percentage` descending. Ties share the same rank, and the next rank skips (1, 2, 2, 4). A teacher calling this route only receives modules they teach. `gpa` for a teacher is omitted (`null`) because it would mix hidden modules. Admin and the student receive the full GPA.

## `GET /api/analysis/modules/{module_id}/`

Cohort for one module. Students with no snapshot are counted in `unenrolled_marks` only as "no snapshot yet". Dropped enrollments are excluded.

```json
{
  "module": {
    "id": "<uuid>",
    "module_code": "CS101",
    "module_name": "Programming Fundamentals",
    "credit_hours": 3,
    "academic_year": "2026-2027"
  },
  "cohort_size": 40,
  "with_snapshot": 36,
  "average_percentage": "71.20",
  "median_percentage": "74.00",
  "grade_distribution": [
    {"letter": "A", "count": 6},
    {"letter": "B", "count": 14},
    {"letter": "C", "count": 10},
    {"letter": "D", "count": 4},
    {"letter": "F", "count": 2}
  ],
  "components": [
    {
      "assessment_type": "QUIZ",
      "percentage": 20,
      "average_ratio": "0.78"
    }
  ],
  "students": [
    {
      "student_id": "<uuid>",
      "full_name": "Aye Aye",
      "enrollment_status": "ENROLLED",
      "obtained_percentage": "76.50",
      "letter_grade": "B",
      "grade_point": "3.00",
      "rank": 4
    }
  ]
}
```

`students` is paginated with the shared page contract. Summary fields (`cohort_size`, averages, `grade_distribution`, `components`) describe the full cohort, not the current page.

`average_ratio` is the mean of per-student `distribution_ratio` for students who have at least one result in that type.

Median is the mean of the two central values when `with_snapshot` is even.

## `GET /api/analysis/academics/{academic_id}/`

Admin sees every module in the year. A teacher sees only modules they teach. `module_count` and the list follow that scope.

```json
{
  "academic_id": "<uuid>",
  "academic_year": "2026-2027",
  "module_count": 2,
  "modules": [
    {
      "module_id": "<uuid>",
      "module_code": "CS101",
      "module_name": "Programming Fundamentals",
      "lecturer_name": "U Hla",
      "cohort_size": 40,
      "average_percentage": "71.20",
      "fail_count": 2
    }
  ]
}
```

`fail_count` is the number of snapshots whose `grade_point` is `0.00`. The pass boundary is whatever the academic year's scale encodes. The API does not invent a second threshold.

The list is ordered by `module_code`. Pagination applies to `modules`.
