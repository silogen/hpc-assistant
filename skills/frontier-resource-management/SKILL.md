---
name: frontier-resource-management
description: Check Frontier allocations, storage, and project access.
---

# Frontier Daily Management

Helps users check compute allocations, filesystem usage and quota, and user/project information on the Frontier supercomputer (OLCF) using the documented OLCF commands and the myOLCF portal.

## When to Use This Skill

- The user asks about project quota, storage usage, limits, or purge risk.
- The user needs remaining compute allocation or a cost estimate.
- The user wants an early warning about allocation or storage exhaustion.
- The user needs identity, project membership, or allocation metadata.

## Overview

Frontier charges compute in **node-hours**: requested nodes × elapsed running time. A node has 4 AMD MI250X packages (8 GCDs, 64 GB HBM2E each), 512 GB host memory, and 56 application cores by default (64 physical, 8 reserved). A job that uses one core or one GCD is still charged for the whole node.

Data sources and freshness differ:

- `showusage` reports usage from January 1 through midnight of the previous day, computed daily from completed jobs. Jobs that finished today may not appear yet.
- `sacct` shows recent job and step accounting. It is useful context, not the allocation ledger.
- Filesystem quota commands report one specific filesystem at query time, not compute allocation.
- `id`/`groups` show Unix group memberships for the session. Use myOLCF for authoritative account and project metadata.

Set `PROJECT` to the charge account and `PROJECT_DIR` to the project's directory name; the two can differ in case.

## Commands

### Workspaces

The [data guide](https://docs.olcf.ornl.gov/data/index.html) documents these storage areas for standard projects (enhanced/sensitive projects may differ):

| Area | Path | Default quota | Backup / purge |
| --- | --- | --- | --- |
| User Home | `$HOME` / `/ccs/home/USER` | 50 GB | Backed up; not purged |
| Project Home | `/ccs/proj/PROJECT_DIR` | 50 GB | Backed up; not purged |
| Orion Member Work | `/lustre/orion/PROJECT_DIR/scratch/USER` | 50 TB | Not backed up; 90-day purge |
| Orion Project Work | `/lustre/orion/PROJECT_DIR/proj-shared` | 50 TB | Not backed up; 90-day purge |
| Orion World Work | `/lustre/orion/PROJECT_DIR/world-shared` | 50 TB | Not backed up; 90-day purge; readable outside the project |
| Kronos archive | `/nl/kronos/olcf/PROJECT_DIR/{users/USER,proj-shared,world-shared}` | 200 TB shared across the three areas | Not purged; retention rules apply after project end |

`$MEMBERWORK`, `$PROJWORK`, and `$WORLDWORK` point to the Orion work roots (append `/PROJECT_DIR`).

### Quota and usage

**User home:**

```bash
quota -Qs
```

Distinguish block (bytes) and file (inode) limits. A full home directory can break job startup even if application output goes to Orion.

**Project Home:**

```bash
df -h "/ccs/proj/$PROJECT_DIR"
```

**Orion work areas:** the public documentation gives paths, defaults, and purge policy, but no project quota command. `df` on Orion shows filesystem capacity, not remaining project quota, and `du -sh -- DIR` measures usage only. Ask OLCF (`help@olcf.ornl.gov`) for the project's Orion quota. Avoid project-wide recursive `du` scans on the shared metadata servers.

**Kronos archive** (from a host where Kronos is mounted; it is not mounted on Frontier compute nodes):

```bash
mmlsquota -g "$PROJECT_DIR" --block-size 1T kronos
```

### Compute allocations

```bash
showusage
showusage -h
```

`showusage` lists your projects' allocation and usage; for one project, read its row or its usage page in [myOLCF](https://my.olcf.ornl.gov). The guide documents no project-selection flag.

Recent jobs for one project, at allocation level (`-X` excludes step rows):

```bash
sacct -X --account="$PROJECT" --starttime="$START_DATE" \
  --format=JobID,Account,State,ExitCode,AllocNodes,Elapsed,Start,End
```

Estimate charge as `AllocNodes × Elapsed` hours. Failed jobs are charged too.

### Early warnings

OLCF has no single "am I about to run out" command. To check:

1. Run `showusage` and note that it lags by a day; add running and recent jobs.
2. Check home and Project Home quota; get Orion/Kronos quota from OLCF if needed.
3. Compare headroom with planned node-hours and expected output size.
4. Report each resource as sufficient, at risk, or unknown (e.g. if a command failed or the data is stale).

Exceeding an allocation lowers job priority and adds restrictions (see **Allocation Overuse Policy** in the [Frontier guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html)) rather than blocking jobs outright.

### User and project information

```bash
id
groups
```

For account details use **myOLCF → My Account → My Profile**. The myOLCF project pages show project metadata, allocation, usage, and membership (visibility depends on your role); joining another project is requested there and approved by the PI.

## When to Use Which Command

| Need | Command / source |
| --- | --- |
| Storage locations | Table above; `$MEMBERWORK`, `$PROJWORK`, `$WORLDWORK` |
| Home quota (bytes and inodes) | `quota -Qs` |
| Project Home usage | `df -h /ccs/proj/PROJECT_DIR` |
| Orion project quota | Not documented publicly; check with OLCF support |
| Kronos project quota | `mmlsquota -g PROJECT_DIR --block-size 1T kronos` |
| Remaining compute allocation | `showusage`, myOLCF project usage (updated daily) |
| Recent job accounting | `sacct -X --account=... --starttime=...` |
| User/project metadata | `id`/`groups`, myOLCF |

## Pitfalls

- Charging is per node-hour regardless of how many GCDs or cores are used.
- Orion's 90-day purge is not a backup; archive important data to Kronos or elsewhere early.
- Node-local NVMe (`--constraint=nvme`, mounted at `/mnt/bb/USER`) lasts only for the job; copy results out before it ends.

## Resources

- [OLCF Frontier User Guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html) – hardware, node-hours, `showusage`, storage.
- [OLCF Data Storage and Transfers](https://docs.olcf.ornl.gov/data/index.html) – quota commands, paths, purge, Kronos.
- [OLCF Accounts and Projects](https://docs.olcf.ornl.gov/accounts/accounts_and_projects.html) – allocations, accounts, joining projects.
- [More documentation links](references/resources.md).
