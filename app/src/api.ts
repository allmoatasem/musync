/** MuSync HTTP API client — talks to the local FastAPI server on port 7765. */

const PORT = 7765
const BASE = `http://127.0.0.1:${PORT}`

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error('Cannot reach the MuSync server.')
  }
  const json = await res.json()
  if (!res.ok) throw new Error(json.detail ?? `HTTP ${res.status}`)
  return json as T
}

// ── types ─────────────────────────────────────────────────────────────────────

export interface NoteInfo {
  pitch: number
  name: string
  velocity: number
  position: number
  duration: number
}

export interface TrackInfo {
  name: string
  instrument: string
  note_count: number
  notes: NoteInfo[]
}

export interface ProjectInfo {
  title: string
  source_format: string
  ppq: number
  tempo_events: { position: number; bpm: number }[]
  time_signatures: { position: number; numerator: number; denominator: number }[]
  key_signatures: { position: number; fifths: number; mode: string; key_name: string }[]
  tracks: TrackInfo[]
}

export interface SnapshotInfo {
  number: number
  timestamp: string
  message: string
  note_count: number
  summary: string
}

export interface DiffResult {
  summary: string
  tempo_changed: boolean
  time_sig_changed: boolean
  key_sig_changed: boolean
  added: NoteChange[]
  removed: NoteChange[]
  changed: NoteChangeMod[]
}

export interface NoteChange {
  track: string
  pitch: number
  name: string
  position: number
  duration: number
  velocity: number
}

export interface NoteChangeMod extends NoteChange {
  old_duration: number | null
  new_duration: number
  old_velocity: number | null
  new_velocity: number
}

// ── public API ────────────────────────────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(1500) })
    return res.ok
  } catch {
    return false
  }
}

/** Open a native file-picker dialog via the server's /open_file endpoint. */
export async function openFile(extensions?: string[]): Promise<string | null> {
  const params = extensions?.length ? `?extensions=${extensions.join(',')}` : ''
  const result = await request<{ path: string | null }>('GET', `/open_file${params}`)
  return result.path
}

export const api = {
  health: () => request<{ ok: boolean }>('GET', '/health'),

  read: (path: string) =>
    request<ProjectInfo>('GET', `/read?path=${encodeURIComponent(path)}`),

  sync: (source: string, dest: string) =>
    request<{ ok: boolean; note_count: number; snapshot: number }>('POST', '/sync', { source, dest }),

  log: (path: string) =>
    request<{ snapshots: SnapshotInfo[] }>('GET', `/log?path=${encodeURIComponent(path)}`),

  diff: (opts: { path_a: string; path_b?: string; snapshot_a?: number; snapshot_b?: number }) =>
    request<DiffResult>('POST', '/diff', opts),

  revert: (path: string, snapshot: number) =>
    request<{ ok: boolean; backup_snapshot: number; restored_snapshot: number }>('POST', '/revert', { path, snapshot }),

  watchStart: (source: string, dest: string) =>
    request<{ ok: boolean }>('POST', '/watch/start', { source, dest }),

  watchStop: () => request<{ ok: boolean }>('DELETE', '/watch'),

  watchStatus: () =>
    request<{ watching: boolean; source: string | null; dest: string | null }>('GET', '/watch/status'),
}
