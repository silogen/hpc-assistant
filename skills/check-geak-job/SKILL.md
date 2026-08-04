---
name: check-geak-job
description: Check the status of a GEAK optimization job and retrieve results. Use when the user asks about a running or completed GEAK job.
allowed-tools: Bash(*/scripts/check-geak-job.sh *), Read, Grep, Glob
---

# Check GEAK Job

Run the check script with the job ID (`$ARGUMENTS`):

```
# Claude Code
./.claude/skills/check-geak-job/scripts/check-geak-job.sh $ARGUMENTS

# OpenCode
./.opencode/skills/check-geak-job/scripts/check-geak-job.sh $ARGUMENTS
```

The script outputs JSON with `id`, `state` (COMPLETED / FAILED / RUNNING), and details. Summarize the output for the user.

Do NOT modify or recreate the check script. If it fails, report the error as-is. Do not attempt workarounds.
