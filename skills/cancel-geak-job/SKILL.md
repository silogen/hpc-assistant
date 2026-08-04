---
name: cancel-geak-job
description: Cancel a running GEAK optimization job. Use when the user wants to stop or abort a GEAK job.
allowed-tools: Bash(*/scripts/cancel-geak-job.sh *), Read
---

# Cancel GEAK Job

Run the cancel script with the job ID (`$ARGUMENTS`):

```
# Claude Code
./.claude/skills/cancel-geak-job/scripts/cancel-geak-job.sh $ARGUMENTS

# OpenCode
./.opencode/skills/cancel-geak-job/scripts/cancel-geak-job.sh $ARGUMENTS
```

The script outputs JSON with `id`, `slurm_job_id`, and `state` (CANCELLED on success). Summarize the result for the user.

After cancelling, if a background geak-monitor is running for this job, use TaskList and TaskStop to stop it.

Do NOT modify or recreate the cancel script. If it fails, report the error as-is.
