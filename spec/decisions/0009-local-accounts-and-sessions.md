# ADR-0009: Local accounts, server-side sessions, and one access policy per route

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Wave 1 ran in single-operator mode: no sign-in, a server reachable from this machine only, and every decision recorded under one configured name. Wave 2 lets several people use Tessera with roles (admin, curator, researcher, viewer), curators limited to a unit or a person, and researchers reviewing their own suggestions (PLT-06, CUR-06). Single sign-on is planned for wave 6 (PLT-10), so local accounts must work alone and later sit beside it. There is no budget for new runtime dependencies or external services.

## Decision

- **Accounts and roles live in the ledger:** `account`, `role_grant` (a role, optionally scoped to a unit or a person), and `auth_session`. Grants add up. A researcher account is linked to its person.
- **Passwords are hashed with the standard library's scrypt** (`hashlib.scrypt`, N = 2^15, r = 8, p = 1, a 16-byte salt), stored with their parameters so the cost can rise later. Passwords need at least 12 characters and have no composition rules, in the spirit of NIST SP 800-63B. An unknown username costs as much time as a wrong password. Repeated failures for one username or address are refused for 15 minutes.
- **Sessions are server-side.** The browser holds an opaque random token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` behind HTTPS); the ledger stores only its SHA-256 hash, so a copied ledger cannot sign anyone in. Sessions end after an idle period and a maximum age, on sign-out, and when the account is disabled or its password changes.
- **Every change carries a CSRF token** (a per-session value the web app sends in `X-CSRF-Token`), and changes a browser sends from another site (`Origin` or `Sec-Fetch-Site`) are refused in both modes.
- **One access policy per route.** A table maps every API route to the least role it needs; a test fails when a route has no entry, so nothing ships without a decision. Checks that depend on which person a request concerns (a scoped curator, a researcher) run in the handler. Authentication runs in a middleware, so it also covers what is not an API route (the OpenAPI document, files); authorization runs as an app-wide dependency, which sees the matched route.
- **Single-operator mode stays** for a server on this machine only: the configuration refuses any other address without accounts, and the server answers only to requests addressed to `localhost` or `127.0.0.1`, which also defeats DNS rebinding.
- **Decisions record the signed-in person's name.**

## Consequences

- No new dependency and nothing to run beside Tessera. Single sign-on can later create the same sessions.
- Session lookups are one indexed ledger read per request, written back at most once a minute.
- The throttle is per process; a deployment with several server processes allows proportionally more attempts.
- Tessera does not terminate TLS; serving it beyond this machine needs HTTPS in front (a reverse proxy), and `tessera doctor` says so.
- API scripts sign in like the web app (a session cookie and the CSRF token). Personal API tokens are left for later.

## Alternatives

- **JWT or signed client-side sessions:** stateless, but hard to revoke on sign-out or when an account is disabled.
- **A password-hashing library (argon2, bcrypt):** stronger defaults in some respects, but a new compiled dependency; scrypt is memory-hard and in the standard library.
- **Authorization decorators on each route:** easy to forget on a new route; one table with a completeness test cannot be forgotten.
- **Requiring accounts everywhere:** would end the zero-setup laptop pilot that single-operator mode serves.
