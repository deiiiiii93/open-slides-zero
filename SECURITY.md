# Security Policy

## Reporting Vulnerabilities

Please do not report security vulnerabilities or leaked credentials in public
issues. Contact the maintainers privately with:

- A short description of the issue.
- Steps to reproduce or validate the impact.
- Affected files, endpoints, or versions when known.
- Whether any credential or private data may have been exposed.

If a secret is accidentally committed, rotate or revoke it immediately before
opening a fix.

## Secret Handling

Real credentials must stay in ignored local env files such as `backend/.env`.
Only placeholder examples should be committed. Before publishing changes, check:

```bash
git ls-files | rg '(^|/)\.env($|\.)|\.env'
git check-ignore -v .env backend/.env frontend/.env backend/.env.local frontend/.env.local
```
