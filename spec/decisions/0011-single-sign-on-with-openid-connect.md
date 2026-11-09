# ADR-0011: Single sign-on with OpenID Connect, beside local accounts

- **Status:** Accepted
- **Date:** 2026-11-08

## Context

Local accounts (ADR-0009) are enough for a small organization, but a larger one wants people to sign in with the identity they already have, under its own multi-factor authentication and offboarding, and to manage who may curate or administer from its identity provider. Adopters use different providers: Microsoft Entra ID, Okta, Google, Keycloak, and others. The maintainer asked that local accounts stay the default and that single sign-on be optional, with roles optionally mapped from the provider (PLT-10).

## Decision

- **OpenID Connect, with any standards-based provider.** `[auth.oidc] issuer` turns it on; everything else about the provider comes from its discovery document. The client secret is read from the environment, never from the configuration file.
- **The authorization-code flow with PKCE** (S256), a `state` value, and a `nonce`. What the callback needs is kept in the ledger, keyed by a hash of the state, used once, and expired after ten minutes; the state is also in a cookie on the browser that began the sign-in, so a sign-in cannot be finished in another browser. The code is exchanged at the token endpoint over TLS with the client secret.
- **The ID token is checked before anyone is signed in:** its RS256 signature against the provider's published keys (read once and read again for a key Tessera has not seen), its issuer, its audience (and authorized party), its expiry and issue time with a minute's leeway, and its nonce. Signatures are verified with the standard library: RSA verification is one modular exponentiation and a comparison with the PKCS #1 v1.5 encoding of the SHA-256 digest. Other algorithms are refused with a message that names them.
- **Accounts are linked by the provider's subject identifier**, per issuer. At the first sign-in, an account is matched by email address when the provider marks the address verified (or when the configuration trusts its addresses, as with a single-tenant Entra ID application), or created, unless creating accounts is turned off. An account made this way has no password.
- **Roles from the provider are replaced at every sign-in.** A role mapped from an app role or group is stored as coming from the provider; roles given in Tessera stay as they are. The last active admin is never removed this way.
- **A local admin always works.** The password form stays on the sign-in page, behind single sign-on's button, for accounts that have a password.
- Tessera's own session (ADR-0009) starts after a sign-in either way; signing out ends it, not the provider's.

## Consequences

- No new dependency: PKCE, the token checks, and RSA verification are a few hundred lines with the standard library and the HTTP client Tessera already uses, tested with a scripted provider and checked end to end against Keycloak.
- Providers whose ID tokens are signed with elliptic-curve keys (ES256) need a setting at the provider to use RS256, which every provider named supports and most use by default.
- Signing out of Tessera does not sign anyone out of the provider; a provider's own sign-out is unaffected.
- Entra ID leaves groups out of tokens for people in more than about 200, so the guide recommends its app roles.

## Alternatives

- **SAML 2.0:** common for older enterprise applications, but XML signatures are hard to check safely without a library, and every provider named supports OpenID Connect for new applications. It can be added later if an adopter needs it.
- **A JOSE or OpenID Connect library:** convenient, but it would add a cryptography dependency for one algorithm's verification.
- **Trusting the ID token without its signature** (allowed when it comes straight from the token endpoint over TLS): checking the signature as well costs little and guards against a misdirected token endpoint.
