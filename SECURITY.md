# Security policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 1.x     | ✅ Yes    |
| < 1.0   | ❌ No     |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities privately via one of these channels:
- **GitHub Security Advisories:** [Report a vulnerability](https://github.com/Developmi/agency-analytics-kit/security/advisories/new)
- **Email:** miguel@developmi.com - encrypt with PGP if the finding is critical.

Include in your report:
- Description of the vulnerability and its potential impact.
- Steps to reproduce or a proof-of-concept.
- Affected versions.
- Any suggested mitigations.

## Response timeline

| Stage | Target time |
|---|---|
| Acknowledgment | 48 hours |
| Initial assessment | 5 business days |
| Fix or mitigation | 30 days (critical: 7 days) |
| Public disclosure | After fix is available |

## Disclosure policy

This project follows coordinated disclosure. We ask that you give us reasonable time to address the vulnerability before public disclosure. We will credit reporters in the release notes unless anonymity is requested.

## Security best practices for this project

1. **Never commit `.env` files** - the `.gitignore` blocks them; use `.env.example` as a template
2. **Postgres** is bound to `127.0.0.1` only - no public database access
3. **Metabase** uses a read-only database user (`metabase_reader`)
4. **Pipeline tokens** live in environment variables, never in code
5. **Docker networks** isolate services - Metabase cannot route traffic to the pipeline container
6. **Telegram alerts** use a bot token with minimal permissions
