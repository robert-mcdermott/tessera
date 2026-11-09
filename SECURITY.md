# Security

## Reporting a vulnerability

Please report a vulnerability privately, not in a public issue:

1. On GitHub, open the repository's **Security** tab and choose **Report a vulnerability**. The report reaches the maintainers alone.
2. If that option is not shown, open an issue that asks for a private way to report a security problem, and give no details of the problem in it. A maintainer will reply with one.

Say what an attacker could do, which version you used, and how to reproduce it. You will hear back within a week; a fix, or what we decided and why, follows as soon as it can, and you will be credited in the release notes unless you would rather not be.

## Supported versions

Fixes go into the latest minor release of the current major version (1.x). Upgrading within a major version keeps everything [Compatibility](docs/compatibility.md) promises working, so there is no reason to stay on an older release: upgrade to the latest to receive fixes (see [Upgrading](docs/operations.md#upgrading)).

## How Tessera is kept secure

The threat model and the review against OWASP's Application Security Verification Standard at level 1, with what each finding led to, are in [spec/security.md](spec/security.md). For running Tessera safely: [Running Tessera](docs/operations.md), especially [Running it as a service](docs/operations.md#running-it-as-a-service) and `tessera doctor --production`.
