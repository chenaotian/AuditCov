// AuditCov Read hook probe. This marker is used by the installer and uninstaller.
import type { Plugin } from "@opencode-ai/plugin"
import { appendFile, mkdir } from "node:fs/promises"
import { dirname, join } from "node:path"
import { homedir } from "node:os"

function logPath() {
  const stateHome = process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state")
  return process.env.AUDITCOV_READ_HOOK_LOG ?? join(stateHome, "auditcov-read-hook-probe", "events.jsonl")
}

export const AuditCovReadHookProbe: Plugin = async ({ client }) => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool.toLowerCase() !== "read") return

      const path = logPath()
      const event = {
        recorded_at: new Date().toISOString(),
        probe_client: "opencode",
        hook: "tool.execute.before",
        pid: process.pid,
        session_id: input.sessionID,
        call_id: input.callID,
        tool_name: input.tool,
        read_parameters: output.args,
        hook_input: input,
      }

      try {
        await mkdir(dirname(path), { recursive: true, mode: 0o700 })
        await appendFile(path, JSON.stringify(event) + "\n", { encoding: "utf8", mode: 0o600 })
      } catch (error) {
        await client.app
          .log({
            body: {
              service: "auditcov-read-hook-probe",
              level: "error",
              message: "Failed to record read tool parameters",
              extra: { error: String(error), path },
            },
          })
          .catch(() => {})
      }
    },
  }
}
