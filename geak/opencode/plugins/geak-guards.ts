// Copyright Advanced Micro Devices, Inc.
//
// SPDX-License-Identifier: MIT

/**
 * OpenCode plugin: GEAK security guards.
 *
 * Mirrors the four Claude Code hooks in geak/claude/hooks/:
 *
 *   enforce-geak-bash.sh   → tool.execute.before (bash)   — full fidelity, blocking
 *   protect-scripts.sh     → tool.execute.before (edit)    — full fidelity, blocking
 *   guard-monitor-spawn.sh → session.created (child)       — partial: observational only*
 *   cleanup-monitor.sh     → session.deleted (child)       — good fidelity
 *
 * *OpenCode subagents are child sessions forked via session.fork(). session.created
 *  fires AFTER the fork so the plugin cannot prevent the session from being created.
 *  The lock file is still written on creation, so a duplicate geak-monitor can detect
 *  the collision at startup and self-terminate.
 *
 * Denials are logged to .opencode/logs/geak-hook-denials.log (same semantics as the
 * Claude Code hook logs at .claude/logs/geak-hook-denials.log).
 */

import type { Plugin } from "@opencode-ai/plugin"
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "fs"
import { join } from "path"

export const GeakGuards: Plugin = async ({ directory }) => {
  // ── Setup ──────────────────────────────────────────────────────────────────

  const logDir = join(directory, ".opencode", "logs")
  mkdirSync(logDir, { recursive: true })

  const logFile = join(logDir, "geak-hook-denials.log")

  function logDenial(tool: string, reason: string, target: string): void {
    const line = `${new Date().toISOString()} | ${tool} | ${reason} | ${target}\n`
    writeFileSync(logFile, line, { flag: "a" })
  }

  // ── Regexes (mirrors enforce-geak-bash.sh) ────────────────────────────────

  // Block raw cluster / container commands …
  const CLUSTER_CMDS = /(\s|^|;|&&|\|)(sbatch|srun|squeue|sacct|scancel|singularity)(\s|$|;)/
  // … unless the command is an approved wrapper script.
  // Claude Code path was .claude/skills/; OpenCode installs to .opencode/skills/.
  const APPROVED_WRAPPER =
    /\.opencode\/skills\/[^/]+\/scripts\/(submit-geak-job|check-geak-job|cancel-geak-job)\.sh/

  // ── Protected paths (mirrors protect-scripts.sh) ──────────────────────────

  const PROTECTED = [
    "run-geak.sbatch",
    "setup-geak.sh",
    ".opencode/plugins/",
    ".opencode/agents/",
    ".opencode/skills/",
  ]

  // ── Lock-file helpers (mirrors guard-monitor-spawn.sh / cleanup-monitor.sh) ─

  const JOB_ID_RE = /[0-9]{8}-[0-9]{6}-[a-f0-9]{4}/

  const jobsDir = () => join(directory, "geak-output", "jobs")
  const lockPath = (jobId: string) => join(jobsDir(), jobId, ".monitoring")

  function cleanStaleLocks(): void {
    const dir = jobsDir()
    if (!existsSync(dir)) return
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const lock = join(dir, entry.name, ".monitoring")
      if (!existsSync(lock)) continue
      if (
        existsSync(join(dir, entry.name, "DONE")) ||
        existsSync(join(dir, entry.name, "FAILED"))
      ) {
        rmSync(lock)
      }
    }
  }

  // ── Plugin hooks ───────────────────────────────────────────────────────────

  return {
    // ── enforce-geak-bash.sh + protect-scripts.sh ────────────────────────
    "tool.execute.before": async (input, output) => {
      const tool: string = (input as any).tool ?? ""

      // bash guard
      if (tool === "bash") {
        const cmd: string = (output as any).args?.command ?? ""

        if (CLUSTER_CMDS.test(cmd) && !APPROVED_WRAPPER.test(cmd)) {
          const reason =
            "Raw cluster commands (sbatch/srun/squeue/sacct/scancel/singularity) " +
            "are blocked. Use the approved wrapper scripts under " +
            ".opencode/skills/*/scripts/."
          logDenial("bash", reason, cmd)
          throw new Error(reason)
        }

        if (
          /(\s|^|\/)run-geak\.sbatch(\s|$)/.test(cmd) &&
          !APPROVED_WRAPPER.test(cmd)
        ) {
          const reason =
            "Direct execution of run-geak.sbatch is blocked. " +
            "Use the approved wrapper scripts."
          logDenial("bash", reason, cmd)
          throw new Error(reason)
        }
      }

      // edit / write guard
      if (tool === "edit" || tool === "write") {
        const filePath: string =
          (output as any).args?.file_path ??
          (output as any).args?.path ??
          ""

        for (const pattern of PROTECTED) {
          if (filePath.includes(pattern)) {
            const reason = `'${filePath}' is a protected GEAK workflow file and must not be modified.`
            logDenial("edit/write", reason, filePath)
            throw new Error(reason)
          }
        }
      }
    },

    // ── guard-monitor-spawn.sh (observational — see module docstring) ────
    // OpenCode subagents are child sessions; session.created fires after the
    // fork so we cannot block. We write the lock here and log collisions so
    // the geak-monitor agent can detect duplicates and self-terminate.
    "session.created": async ({ event }: { event: Record<string, any> }) => {
      // Only act on child sessions whose agent is geak-monitor.
      if (!event.parentID) return
      if (event.agent !== "geak-monitor") return

      cleanStaleLocks()

      // Extract the job ID from whatever field OpenCode exposes on the session.
      // Try common candidates; adjust if the schema changes.
      const probe: string =
        event.title ?? event.summary ?? event.prompt ?? event.id ?? ""
      const match = probe.match(JOB_ID_RE)
      const jobId: string | null = match ? match[0] : null

      if (jobId && existsSync(lockPath(jobId))) {
        // Lock already present — duplicate monitor.  Log it; the agent will
        // read the lock on startup and self-terminate.
        logDenial(
          "session.created",
          `Duplicate geak-monitor detected for job ${jobId}`,
          event.id ?? "unknown-session",
        )
        return
      }

      if (jobId && existsSync(join(jobsDir(), jobId))) {
        writeFileSync(lockPath(jobId), new Date().toISOString())
      }
    },

    // ── cleanup-monitor.sh ───────────────────────────────────────────────
    // Claude Code's SubagentStop fires when the subagent exits. The closest
    // OpenCode equivalent is session.deleted on a child session.
    "session.deleted": async ({ event }: { event: Record<string, any> }) => {
      if (!event.parentID) return
      if (event.agent !== "geak-monitor") return
      cleanStaleLocks()
    },
  }
}
