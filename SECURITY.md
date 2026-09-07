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
3. **Metabase** is opt-in (`--profile metabase`) and connects with a dedicated read-only role (`metabase_reader`) that only exists when `METABASE_READER_ENABLED=true` (OFF by default; see ARCHITECTURE.md → Postgres Users and Access)
4. **Pipeline tokens** live in environment variables, never in code
5. **Docker networks** scope container traffic; Metabase attaches to the internal net to reach Postgres, so its security boundary is the restricted read-only DB role, not network isolation (see ARCHITECTURE.md → Honest Network Posture)
6. **Telegram alerts** use a bot token with minimal permissions
