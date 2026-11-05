# Frontier and Slurm documentation

## Frontier / OLCF

- [Frontier User Guide](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html)
- [Scheduling policy](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#scheduling-policy)
- [Known issues](https://docs.olcf.ornl.gov/systems/frontier_user_guide.html#known-issues)
- [Data Storage and Transfers](https://docs.olcf.ornl.gov/data/index.html)
- [Accounts and Projects](https://docs.olcf.ornl.gov/accounts/accounts_and_projects.html)
- [Containers on Frontier](https://docs.olcf.ornl.gov/software/containers_on_frontier.html)
- [OLCF container examples](https://github.com/olcf/olcf_containers_examples)
- [Python on OLCF Systems](https://docs.olcf.ornl.gov/software/python/index.html)
- [PyTorch on Frontier](https://docs.olcf.ornl.gov/software/analytics/pytorch_frontier.html)
- [myOLCF](https://docs.olcf.ornl.gov/services_and_applications/myolcf/index.html)

## AMD MI250X

- [AMD Instinct MI250X](https://www.amd.com/en/products/accelerators/instinct/mi200/mi250x.html) – vendor figures are per package (128 GB HBM2E); Frontier exposes each package as two 64 GB GCDs.

## Slurm and Apptainer

- [Slurm sbatch](https://slurm.schedmd.com/sbatch.html)
- [Slurm srun](https://slurm.schedmd.com/srun.html)
- [Slurm salloc](https://slurm.schedmd.com/salloc.html)
- [Slurm sacct](https://slurm.schedmd.com/sacct.html)
- [Slurm job arrays](https://slurm.schedmd.com/job_array.html)
- [Apptainer user guide](https://apptainer.org/docs/user/latest/)

## Notes

- The scheduling policy's largest node bin ends at 9,472 nodes, while the Accounts and Projects page mentions 9,408 nodes.
- The NVMe example in the Frontier guide's storage section still uses an older Alpine/GPFS path; current work areas are on Orion (`/lustre/orion`).
- The container guide's examples target different CPE/MPICH versions, and its binding modules are marked experimental. Use one recipe consistently.
