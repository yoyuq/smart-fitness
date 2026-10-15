# Validation record (storage candidate)

Production was not switched. The original local checkout and both SQLite files remain untouched. Work is based on GitHub main `db87216a99efe3a1ad9b37c86f86c8edc47eccd5`; unrelated personal WIP was not folded into this branch.

| Requirement | Executed evidence |
|---|---|
| One connection/configuration | API/auth/background/MCP/training database tests on native PostgreSQL 17 and SQLite; cross-process SQLite first-boot DDL test |
| Real data migration | Online snapshots of both local files: 22 source tables, 7,492 rows. Full source-content digests and IDs verified in isolated PostgreSQL. Existing 7,374 orphan relationships preserved only through explicit rehearsal opt-in; default remains reject |
| Safe recovery | Export from current PostgreSQL after new user writes, including ID sequence history; new SQLite verified rather than returning to old snapshots |
| Private media and AI | Generated JPEGs through actual training buffers, keyframes, clip save, JWT playback, signed URLs, ownership rejection, AI multi-frame reads and legacy cache roots |
| Durable failure handling | Transport outage/restart/bounded retry, checksum failures, paused writer vs pump, PUT timeout after delete, late clip frames, crash after prefix delete intent, explicit shutdown failure with live thread |
| SDK integration | Installed real COS SDK constructor checked without network; fake object server tests cover private ACL, checksum, signing and delete contracts. No live bucket configured |
| Process interruption | Actual Uvicorn HTTP process register/train/write/stop, force kill, restart, login/read history/new writes; tested independently of TestClient |
| Deployment | Compose config validated locally. Local Docker daemon unresponsive; Linux GitHub Actions builds and starts/restarts the image. Refer to the exact workflow run for container results |

The candidate regression command uses 13 storage/business/vision test modules, including real HTTP process restart. Before the final small additions it passed 103 PostgreSQL tests (24.40 s) and 102 SQLite tests plus one PostgreSQL-only skip (20.85 s). Final exact-head test counts and CI status belong in the PR acceptance report; these earlier numbers are not a substitute for final-head checks.

Independent reviews found and drove repairs rather than relaxing the checks. No external human rescue or production secret was required for the isolated tests. Model/API billing for this interactive development session is unavailable and is not reported as zero. No live model response or real COS account is claimed by these storage tests.

Additional review of the deployment candidate drove three more repairs: write readiness now checks all canonical table/sequence permissions plus a rolled-back real INSERT/UPDATE/DELETE; media import classifies whole-prefix tombstones before attempting old frames; batch telemetry rejects every non-finite number without partial inserts or invalid JSON error responses. A generated concurrency counterexample also covers DELETE acknowledgements arriving after a late PUT; claim tokens retain the compensating cleanup intent. Final exact-head results are recorded on child PR #14 and the parent delivery PR.
