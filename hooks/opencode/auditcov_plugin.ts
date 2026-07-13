// AuditCov transparent Read adapter. The installer uses this marker for safe removal.
import type { Plugin } from "@opencode-ai/plugin"
import { isAbsolute, resolve } from "node:path"
import { appendFile, mkdir } from "node:fs/promises"
import { dirname, join } from "node:path"
import { homedir } from "node:os"

const serverUrl = (process.env.AUDITCOV_SERVER_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "")

type SessionInfo = {
  id: string
  parentID?: string
  title?: string
}

const sessionCache = new Map<string, Promise<SessionInfo>>()

function warningPath() {
  const state = process.env.XDG_STATE_HOME ?? join(homedir(), ".local", "state")
  return join(state, "auditcov", "hook-warnings.log")
}

async function warn(client: any, message: string) {
  try {
    const path = warningPath()
    await mkdir(dirname(path), { recursive: true, mode: 0o700 })
    await appendFile(path, `${new Date().toISOString()} opencode ${message}\n`, { mode: 0o600 })
  } catch {}
  await client.app.log({
    body: { service: "auditcov", level: "warn", message },
  }).catch(() => {})
}

async function sessionInfo(client: any, sessionID: string): Promise<SessionInfo> {
  const cached = sessionCache.get(sessionID)
  if (cached) return cached
  const pending = (async () => {
    const response = await client.session.get({ path: { id: sessionID } })
    const value = response?.data ?? response
    if (!value || typeof value !== "object") throw new Error(`session ${sessionID} was not found`)
    return {
      id: typeof value.id === "string" ? value.id : sessionID,
      parentID: typeof value.parentID === "string" ? value.parentID : undefined,
      title: typeof value.title === "string" && value.title.trim() ? value.title : undefined,
    }
  })()
  sessionCache.set(sessionID, pending)
  try {
    return await pending
  } catch (error) {
    sessionCache.delete(sessionID)
    throw error
  }
}

async function sessionIdentity(client: any, sessionID: string) {
  try {
    const current = await sessionInfo(client, sessionID)
    const parent = current.parentID ? await sessionInfo(client, current.parentID) : undefined
    return {
      agent_session_id: sessionID,
      parent_agent_session_id: parent?.id,
      agent_session_title: current.title,
      parent_agent_session_title: parent?.title,
    }
  } catch (error) {
    await warn(client, `unable to resolve session parent for ${sessionID}: ${String(error)}`)
    return { agent_session_id: sessionID }
  }
}

async function post(client: any, path: string, payload: Record<string, unknown>) {
  try {
    const response = await fetch(serverUrl + path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(3000),
    })
    const value = await response.json() as any
    if (!response.ok) {
      await warn(client, `server HTTP ${response.status} for ${path}: ${value?.error ?? "unknown error"}`)
      return undefined
    }
    return value
  } catch (error) {
    await warn(client, `server unavailable for ${path}: ${String(error)}`)
    return undefined
  }
}

function readRange(args: any) {
  const start = Number.isInteger(args.offset) && args.offset > 0 ? args.offset : 1
  const end = Number.isInteger(args.limit) && args.limit > 0 ? start + args.limit - 1 : undefined
  return { start, end }
}

function actualRange(output: any, fallback: { start: number, end?: number }) {
  const metadata = output?.metadata ?? output
  return {
    start: Number.isInteger(metadata?.lineStart) ? metadata.lineStart : fallback.start,
    end: Number.isInteger(metadata?.lineEnd) ? metadata.lineEnd : fallback.end,
  }
}

function succeeded(output: any) {
  if (output == null) return false
  if (typeof output === "object" && (output.error || output.isError === true)) return false
  return true
}

export const AuditCov: Plugin = async ({ client, directory }) => ({
  "tool.execute.before": async (input, output) => {
    if (input.tool.toLowerCase() !== "read") return
    const filePath = output.args?.filePath
    if (typeof filePath !== "string" || !filePath) return
    const range = readRange(output.args)
    const identity = await sessionIdentity(client, input.sessionID)
    const payload = {
      agent_type: "opencode",
      ...identity,
      call_id: input.callID,
      file_path: isAbsolute(filePath) ? filePath : resolve(directory, filePath),
      start_line: range.start,
      end_line: range.end,
    }
    const result = await post(client, "/api/read/before", payload)
    if (!result?.tracked || !result?.modified) return
    output.args.offset = result.start_line
    output.args.limit = result.limit
  },

  "tool.execute.after": async (input, output) => {
    if (input.tool.toLowerCase() !== "read") return
    const args = input.args as any
    const filePath = args?.filePath
    if (typeof filePath !== "string" || !filePath) return
    const range = actualRange(output, readRange(args))
    const identity = await sessionIdentity(client, input.sessionID)
    await post(client, "/api/read/after", {
      agent_type: "opencode",
      ...identity,
      call_id: input.callID,
      file_path: isAbsolute(filePath) ? filePath : resolve(directory, filePath),
      start_line: range.start,
      end_line: range.end,
      success: succeeded(output),
      tool_result: output,
    })
  },
})
