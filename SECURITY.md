# Security Policy

## Supported Versions

Security fixes are applied to the latest release on the default branch and to
actively maintained container image tags published to
[Docker Hub](https://hub.docker.com/).

| Version | Supported |
| ------- | --------- |
| Latest on default branch | Yes |
| Older commits / image tags | Best effort |

## Reporting a Vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Use [GitHub Private Vulnerability Reporting](https://github.com/silogen/hpc-assistant/security/advisories/new)
to submit a private security advisory. Only repository maintainers can see the
report until it is published after a fix is available.

If Private Vulnerability Reporting is unavailable, open a minimal public
[GitHub Issue](https://github.com/silogen/hpc-assistant/issues) asking a
maintainer to contact you — **do not include vulnerability details** in the
public issue.

Please include in your private report:

- Description of the vulnerability
- Steps to reproduce
- Affected component (setup scripts, proxy, containers, skills, etc.)
- Potential impact

## What to Expect

- **Acknowledgment** of your report as soon as possible
- **Status updates** as the investigation progresses
- **Coordinated disclosure** after a fix is available

## Scope

In scope:

- Vulnerabilities in AMD-authored code in this repository
- Container images built from this repository
- Unsafe defaults in setup templates or permission rules

Out of scope:

- Vulnerabilities in third-party dependencies (report to the upstream project;
  see [THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt))
- Issues in external services (Aitta, Anthropic API, LUMI AI Factory MCP, etc.)
- Social engineering or physical security

## Secure Development Practices

- Never commit secrets, API keys, or tokens. See `.gitignore`.
- Store credentials only in `~/.config/hpc-assistant/credentials.env` (mode 600).
- Review OpenCode and Claude Code permission rules before deploying to shared
  login nodes.
