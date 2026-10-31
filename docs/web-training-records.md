# Web training history

Open `/static/records.html`, or choose **训练记录** in the existing `/app`
training page. The page uses the existing Smart Fitness username/password login
and the same `auth_token` / `auth_username` storage keys as the training page.
No frontend build step or new dependency is required.

The approved light/green interface supports action filtering, loading older
records, session details, and the latest **saved AI coach report** for that
session. Mobile uses list → detail → back; desktop shows both columns. Returning
keeps the filter and list position. Opening a record only issues GET requests:
it never generates a report, calls a model, or starts training hardware.

Only the authenticated user's data is read. Missing/foreign sessions return the
same 404 response. A zero score/count stays zero; NULL is shown as missing.
Failed reports can be retried independently of the training metrics. Expired
authentication clears the displayed data and returns to login. Late responses
are cancelled/ignored after selection or account changes.

## HTTP contract

All queries require `Authorization: Bearer <existing login token>`.

| Endpoint | Result |
| --- | --- |
| `GET /api/v2/sessions/history` | Existing `sessions` array, plus `has_more`, `next_offset`, and the user's `exercises`. Each row also has `has_report`. |
| `GET /api/v2/sessions/detail/{session_id}` | `{ "session": { ... } }`, only for the current owner. |
| `GET /api/v2/ai_coach/reports?session_id=…&limit=1` | Existing compact archive listing. |
| `GET /api/v2/ai_coach/reports/{report_id}` | Existing archive content, also owner-restricted. |

History defaults to 50 rows as before. Optional `limit` is 1–200, `offset` is
nonnegative, and `exercise` is an exact action identifier (max 100 characters).
Invalid pagination returns 422. Sorting is `start_time DESC, session_id DESC`;
`next_offset` is null at the end. The legacy `user_id` parameter remains ignored.
Filtering and report markers never include another user's rows. Databases that
do not yet have an AI archive table can still list training history.

The Web page fetches 20 rows at a time and shows the loaded count, not an invented
total. Offset pages reflect the current database; concurrent additions/removals
may shift pages. Reload to refresh the list; duplicate session IDs are suppressed.

Reports come from `ai_coach_reports`, including `overall_assessment`, `guidance`,
`rep_by_rep_notes`, and `data_gaps` when present. The original stored JSON can
also be expanded. Older `ai/workout_reports` are a separate report format and are
not migrated or merged by this feature.

## Validation

With the existing backend dependencies installed:

```sh
python -B backend/test_web_records.py
python -m pytest -q backend/test_web_records.py backend/test_fitness_agent_toolkit.py
node --check backend/static/records.js
```

The route suite launches a fresh process and temporary SQLite database before
import-time initialization. It tests real bcrypt/JWT login, user isolation,
stable pagination, exact filters, invalid inputs, zero/NULL values, saved report
reads, static navigation, and history without an archive table. It prevents
dotenv loading and does not run application lifespan/background jobs.

Browser acceptance: login failure/success → filter → load more → detail/report →
mobile back → logout → empty account; verify report/list failures and retries,
401 clearing, quick selection, and a refreshed session link. Inspect narrow and
wide layouts and escape report text. Hardware, model generation, and production
data migration require their own checks and are outside this read-only UI.

The toolkit package initializer is included because the baseline omitted it:
that omission prevented `main_v2_extra` (including history and report routes)
from loading. `.gitignore` explicitly permits package `__init__.py` files.
