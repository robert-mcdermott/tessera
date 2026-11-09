# Security: threat model and review

The threat model for Tessera, and a review against the [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/) (ASVS 4.0.3) at level 1, with what each finding led to (PLT-15). Written in wave 7, before other organizations adopt Tessera; revisit it when a wave adds a way in (a new kind of upload, outbound request, or sign-in).

## What is being protected

| Asset | Why it matters |
|---|---|
| **The ledger's human work**: curation decisions, people and groups, profiles, accounts | Hard to recreate; the only part of the data home that cannot be fetched again |
| **Accounts and sessions** | An account's role decides who may change everyone's records |
| **What is not yet public**: suggestions, notes, unpublished profiles and group pages, internal opportunities, saved reports | Inside the organization only; the public site shows what people chose to publish |
| **The server** | Holds the configuration, secrets in its environment, and backups |

The records themselves come from public sources (PubMed, NIH RePORTER, ClinicalTrials.gov, ORCID, iCite): their confidentiality is not at stake, their integrity is (a link a curator confirmed should not change behind their back).

## Who might attack, and how they get in

| Actor | Entry points | Main threats |
|---|---|---|
| **Someone on the internet** | The sign-in page and the health and branding endpoints (everything else needs a session); the public site (static files) | Password guessing, session theft, requests forged from another site, injection through anything that reaches a page |
| **A signed-in user beyond their role** (a viewer, a researcher, a group's manager) | Every API route | Changing what their role does not allow, reading another person's unpublished work, reaching an admin function |
| **Content from the public sources** | Titles, abstracts, names, and affiliations in what a sync fetches | Script or markup in a record reaching a page (stored cross-site scripting); a crafted record breaking a query |
| **A file someone gives Tessera** | Branding images (admins), backup archives (`tessera restore`), roster and group files | A script in an SVG; an archive that writes outside the data home; a file that exhausts memory |
| **Whoever controls an outbound endpoint** | The single sign-on provider, the optional assistant's endpoint, the public sources | Forged ID tokens; answers that try to steer the assistant |

**Trust boundaries.** The browser and the API (every request authenticated and authorized by route); the reverse proxy and the app (the app trusts `X-Forwarded-Proto` and `-Host` only for cookies, its origin check, and the sign-on address); the app and the data home (only Tessera's own user writes it); Tessera and the outside services it calls (only the hosts the configuration names, over HTTPS).

## Controls, by threat

- **Guessing passwords.** Passwords of at least 12 characters (NIST SP 800-63B: length, no composition rules), hashed with scrypt; after five failures in 15 minutes for one username (or one address, when not behind a proxy) sign-in is refused until the window passes; an unknown username costs the same time as a wrong password. Behind a proxy, the proxy counts by address (`deploy/proxy/nginx.conf` limits sign-in attempts). Single sign-on brings the organization's own policies and multi-factor authentication.
- **Stolen or forged sessions.** 256-bit random tokens, stored only as hashes; the cookie is HttpOnly, SameSite=Strict, and Secure over HTTPS; sessions end after 12 idle hours and at most 7 days; signing out revokes the session, and changing a password ends the others.
- **Requests forged from another site.** Every change needs the session's CSRF token in a header, and requests whose `Origin` or `Sec-Fetch-Site` say another site are refused.
- **Reaching beyond a role.** Every API route has an entry in one table of access policies, and a route without one is refused. Curation, group management, and profiles are checked for the person or group concerned. Tests sign in as each role and try every route.
- **Injection.** The web app builds pages with DOM methods and text, never HTML strings (no `innerHTML` anywhere); highlights are private-use characters, never markup; the public site escapes every value and checks every link; SQL is parameterized; full-text queries are rebuilt from words, so operators in a search are inert; the configuration file is written by a TOML writer that escapes every value.
- **Files.** Branding images are checked by their first bytes, limited to 2 MB, stored under names drawn from their content, and SVGs with scripts, event handlers, embedded documents, or references to other files are refused; every branding image is served with a policy that runs nothing. `tessera restore` extracts only regular files, under known folders, with no absolute paths or `..`.
- **Pages served from Tessera's origin.** A content security policy with no third-party sources and no inline scripts or styles; the public-page preview, built only from escaped values, is allowed no scripts at all. `X-Content-Type-Options`, `Referrer-Policy`, `frame-ancestors 'none'`, `Cross-Origin-Opener-Policy`, and `Permissions-Policy` on every response; API responses are not cached.
- **Outbound requests.** Only to hosts in the configuration (the sources, the single sign-on provider, the assistant's endpoint), over HTTPS. ORCID iDs are checked, check digit included, before they are used in an address. Nothing a user types becomes an address Tessera fetches.
- **Single sign-on.** The authorization-code flow with PKCE (S256), a state used once and bound to the browser, and a nonce; the ID token's signature, issuer, audience, expiry, and nonce are checked before anyone is signed in (ADR-0011).
- **The assistant** is off unless an admin turns it on, and logs every exchange in full. Its tools only read the organization's records, and what it drafts (a summary, a biosketch's text) waits for a person to approve or use it; text a source or a user gives it cannot make it change anything.
- **Secrets** are read from the environment only; the configuration names the variables that hold them.

## Review against ASVS 4.0.3, level 1

✓ meets the requirements; ◐ meets them with a recorded gap.

| Chapter | | What Tessera does |
|---|---|---|
| V2 Authentication | ◐ | Length rules, scrypt, throttling, single sign-on, no default accounts. **Gaps:** 2.1.7 (passwords are not checked against a list of breached passwords) and 2.2.3 (no message when a password changes). Use single sign-on, which brings the organization's own checks; a local list of common passwords, and a message when [mail] is set up, would close both |
| V3 Session management | ✓ | As above: tokens, cookie flags, idle and absolute limits, revocation |
| V4 Access control | ◐ | Deny by default, checked per route and per person or group, tested for every role. **Gap:** 4.3.1 (multi-factor authentication for administrative interfaces): local admins sign in with a password. Use single sign-on with the provider's multi-factor authentication for admins, keeping one local admin as a way back in |
| V5 Validation, sanitization, and encoding | ✓ | Parameterized queries, inert search syntax, output by text or escaping, checked links |
| V6 Stored cryptography | ✓ | scrypt for passwords, hashes of session tokens and sign-on states; no home-made cryptography beyond RSA signature verification, checked against OpenSSL |
| V7 Error handling and logging | ✓ | Unexpected errors are logged and answered with a plain message, never a stack trace; no passwords, tokens, or keys in logs |
| V8 Data protection | ✓ | No secrets or tokens in addresses; API responses not cached; unpublished work never reaches the public site |
| V9 Communication | ✓ | HTTPS at the reverse proxy, with HSTS in the examples; `tessera doctor --production` warns of addresses that are not HTTPS; outbound calls over HTTPS |
| V10 Malicious code | ✓ | Dependencies locked with hashes (`uv.lock`); `pip-audit` of the locked runtime dependencies found no known vulnerabilities (2026-11-09) |
| V11 Business logic | ✓ | Decisions logged with their actor; the last admin cannot be removed; one data job at a time |
| V12 Files and resources | ✓ | Uploads checked by content and size, stored outside the web root under generated names; archives extracted safely; no server-side requests to addresses users give |
| V13 API | ✓ | JSON with problem details; every route authorized; CSRF tokens; tokens never in addresses |
| V14 Configuration | ✓ | Security headers on every response; reference service files run unprivileged and sandboxed; secrets in the environment |

**Fixed in the review:** the public-page preview, which is served from Tessera's own origin, now allows no scripts at all; the nginx example limits sign-in attempts by address, which the app cannot do behind a proxy; and (in slice 4) SVG uploads are checked and every branding image is served so nothing in it runs.

**Recorded, with what would close them:** the three gaps above, and two that the reverse proxy covers: the app sets no limit of its own on the size of a request's body (the examples limit it to 4 MB), and it cannot count sign-in attempts by address behind a proxy (the nginx example does; Caddy needs its rate-limit module).
