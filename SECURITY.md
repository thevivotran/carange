# Security Policy

## Supported Versions

Carange is deployed continuously from the `main` branch — there are no maintained
release branches. Security fixes are applied to `main` only.

## Reporting a Vulnerability

This app is designed for **self-hosted, local-network use and ships with no
authentication layer** (see the Security note in the [README](README.md)). Please do
not run it on a publicly exposed port.

If you find a security vulnerability (e.g. SQL injection, XSS, path traversal,
authentication/authorization bypass in a future auth feature, or a dependency with a
known exploit), please report it privately rather than opening a public issue:

- Open a [private security advisory](https://github.com/thevivotran/carange/security/advisories/new)
  on GitHub, **or**
- Email **thevivotran@gmail.com** with details and reproduction steps.

Please include:
- A description of the vulnerability and its impact
- Steps to reproduce (and a PoC if possible)
- Affected version/commit

We'll acknowledge reports within a few days and aim to release a fix promptly,
crediting reporters (unless they prefer otherwise) in the [CHANGELOG](CHANGELOG.md).
