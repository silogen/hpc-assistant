# Frontier resource-management documentation

## OLCF

- [Frontier User Guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html) – node-hour calculation, viewing usage (`showusage`), allocation overuse policy, compute nodes, data and storage
- [Data Storage and Transfers](https://docs.olcf.ornl.gov/data/index.html) – storage areas, home and Project Home quotas, Orion work areas, Kronos project quotas
- [Accounts and Projects](https://docs.olcf.ornl.gov/accounts/accounts_and_projects.html) – Frontier allocation requests, user accounts, joining projects
- [OLCF Policy Guide](https://docs.olcf.ornl.gov/accounts/olcf_policy_guide.html) – data retention, purge, quotas
- [myOLCF overview](https://docs.olcf.ornl.gov/services_and_applications/myolcf/index.html)
- [myOLCF account pages](https://docs.olcf.ornl.gov/services_and_applications/myolcf/account_pages/account_pages.html)
- [myOLCF project pages](https://docs.olcf.ornl.gov/services_and_applications/myolcf/project_pages/project_pages.html)

## Hardware

- [AMD Instinct MI250X](https://www.amd.com/en/products/accelerators/instinct/mi200/mi250x.html)

## Generic tools

- [Slurm sacct](https://slurm.schedmd.com/sacct.html)
- [GNU Coreutils](https://www.gnu.org/software/coreutils/manual/html_node/index.html) – `id`, `groups`, `df`, `du`

## Notes

- The public documentation does not describe a command for Orion project quota; ask OLCF support.
- The storage table lists a 50 GB home quota, while an example `quota` output in the same guide shows 5,000 MB. Read actual limits from `quota -Qs`.
- The Frontier User Guide and the Data Storage page disagree on some Kronos backup flags.
