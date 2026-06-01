# Security Policy

## Supported Versions

Security fixes target the latest released version of git-standup.

## Reporting a Vulnerability

Please do not open a public issue for vulnerabilities. Email the maintainer or use the repository's private security advisory flow when available.

Include:

- A description of the issue and impact.
- Steps to reproduce.
- Whether commit metadata, file paths, API keys, or generated summaries are exposed.

## Security Expectations

git-standup reads local Git metadata and can send commit summaries to a configured AI provider. It should never send raw file contents, secret values, or API keys. Users should review provider settings before using AI mode with private repositories.
