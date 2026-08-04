---
name: lumi-management
description: Check LUMI project quotas and allocations using lumi-workspaces, lumi-quota, lumi-allocations, lumi-check-quota, lumi-ldap-userinfo, and lumi-ldap-projectinfo. Use when the user asks about quota, billing units, allocations, or project/user info on LUMI.
---

# LUMI Daily Management

Helps users check project quotas, storage usage, and compute/storage allocations (billing units) on the LUMI supercomputer using the built-in daily management commands.

## When to Use This Skill

Use this skill when:

- The user asks about project quota, storage usage, or limits on LUMI
- The user wants to see remaining billing units or allocations for their project(s)
- The user wants to check if they are about to run out of quota or allocations
- The user needs user or project information from LDAP (e.g. project membership, quota summary)
- The user mentions `lumi-workspaces`, `lumi-quota`, `lumi-allocations`, `lumi-check-quota`, `lumi-ldap-userinfo`, or `lumi-ldap-projectinfo`

## Overview

LUMI provides several commands to inspect project allocations and quota. Data source and freshness differ:

- **Lustre-based (almost real-time):** `lumi-workspaces` and `lumi-quota` read from the filesystem and reflect current quota/usage quickly.
- **Periodic (not instantaneous):** `lumi-allocations`, `lumi-ldap-userinfo`, and `lumi-ldap-projectinfo` use data gathered in the background on a schedule; use them for allocations and billing units or LDAP metadata, not for up-to-the-minute quota.

## Commands

### lumi-workspaces

Shows quota and allocations of your projects as obtained directly from Lustre (almost real-time). Takes no command-line options.

```bash
lumi-workspaces
```

### lumi-quota

Shows quota of your projects. Information is from Lustre and is almost real-time.

**All your projects:**

```bash
lumi-quota
```

**One specific project:**

```bash
lumi-quota --project project_46XXXXXXX
```

**Detailed output (soft and hard quota; columns Quota and Limit):**

```bash
lumi-quota -v
```

Replace `project_46XXXXXXX` with the actual project ID (e.g. from `groups` or `lumi-allocations`).

### lumi-allocations

Shows active allocations and remaining billing units of your projects. Data is updated periodically in the background and is not instantaneous.

**All your projects:**

```bash
lumi-allocations
```

**One specific project:**

```bash
lumi-allocations --project project_46XXXXXXX
```

### lumi-check-quota

Checks whether any of your projects are about to run out of quota or allocations. Prints warnings if so; produces no output if nothing is close to running out.

```bash
lumi-check-quota
```

### lumi-ldap-userinfo

Shows your user information from the LDAP directory. Also includes project quota information, but that data is updated periodically and is not as up to date as `lumi-quota`.

```bash
lumi-ldap-userinfo
```

### lumi-ldap-projectinfo

Shows information about your projects from the LDAP directory, including allocations and quota. Data is updated periodically and is not as up to date as `lumi-workspaces` and `lumi-quota`.

**All your projects:**

```bash
lumi-ldap-projectinfo
```

**One specific project:**

```bash
lumi-ldap-projectinfo --project project_46XXXXXXX
```

## When to Use Which Command

| Need | Command |
| ---- | ------- |
| Near real-time quota and usage | `lumi-workspaces` or `lumi-quota`; use `lumi-quota -v` for soft/hard limits (Quota/Limit columns) |
| Remaining billing units / allocations | `lumi-allocations` |
| Quick check: am I about to run out? | `lumi-check-quota` |
| User or project metadata from LDAP | `lumi-ldap-userinfo` or `lumi-ldap-projectinfo` |

For a single project, add `--project project_46XXXXXXX` to `lumi-quota`, `lumi-allocations`, or `lumi-ldap-projectinfo`.

## Resources

- [LUMI Daily management](https://docs.lumi-supercomputer.eu/runjobs/lumi_env/dailymanagement/) – Official documentation for these commands.
