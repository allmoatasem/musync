import { useState, useEffect } from 'react'
import { api } from '../api'
import FilePicker from './FilePicker'

export default function WatchView() {
  const [source, setSource] = useState('')
  const [dest, setDest] = useState('')
  const [watching, setWatching] = useState(false)
  const [watchSource, setWatchSource] = useState<string | null>(null)
  const [watchDest, setWatchDest] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  // Poll watch status every 2s
  useEffect(() => {
    const poll = async () => {
      try {
        const s = await api.watchStatus()
        setWatching(s.watching)
        setWatchSource(s.source)
        setWatchDest(s.dest)
      } catch { /* server not ready yet */ }
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [])

  const handleStart = async () => {
    if (!source || !dest) return
    setLoading(true); setMessage(null)
    try {
      await api.watchStart(source, dest)
      setWatching(true); setWatchSource(source); setWatchDest(dest)
      setMessage({ ok: true, text: 'Watch started. Every save to the source will sync automatically.' })
    } catch (e: unknown) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setLoading(false)
    }
  }

  const handleStop = async () => {
    setLoading(true)
    try {
      await api.watchStop()
      setWatching(false); setWatchSource(null); setWatchDest(null)
      setMessage({ ok: true, text: 'Watch stopped.' })
    } catch (e: unknown) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="panel">
      <div className="panel-title">Watch</div>
      <p style={{ color: 'var(--text-dim)', marginBottom: 24, lineHeight: 1.6 }}>
        Watch mode automatically syncs the source to the destination every time you save.
        A 1-second debounce prevents partial saves from triggering early.
      </p>

      <FilePicker label="Source (watched file)" value={source} onChange={setSource} placeholder="Pick source project" />
      <FilePicker label="Destination" value={dest} onChange={setDest} placeholder="Pick destination project" />

      <div style={{ marginTop: 20, display: 'flex', gap: 10 }}>
        <button
          className="btn btn-primary"
          disabled={!source || !dest || watching || loading}
          onClick={handleStart}
        >
          {loading && !watching ? <><span className="spinner" /> Starting…</> : '◉  Start Watching'}
        </button>
        <button
          className="btn btn-danger"
          disabled={!watching || loading}
          onClick={handleStop}
        >
          {loading && watching ? <><span className="spinner" /> Stopping…</> : '◻  Stop'}
        </button>
      </div>

      {message && (
        <div className={`result-box ${message.ok ? 'success' : 'error'}`} style={{ marginTop: 16 }}>
          <span className={`status-dot ${message.ok ? 'green' : 'red'}`} />
          {message.text}
        </div>
      )}

      <div className={`watch-status ${watching ? 'active' : ''}`} style={{ marginTop: 24 }}>
        <span className={`status-dot ${watching ? 'green' : ''}`} />
        <div>
          <div style={{ fontWeight: 600 }}>{watching ? 'Watching' : 'Not watching'}</div>
          {watching && watchSource && (
            <div style={{ color: 'var(--text-dim)', fontSize: 11, marginTop: 4, fontFamily: 'monospace' }}>
              {watchSource}<br />→ {watchDest}
            </div>
          )}
        </div>
      </div>

      {watching && (
        <div style={{ marginTop: 16, color: 'var(--text-dim)', fontSize: 12, lineHeight: 1.7 }}>
          <strong style={{ color: 'var(--text)' }}>How it works:</strong><br />
          Every save to the source file is detected within ~1 second.
          The destination is updated automatically and a snapshot is saved to{' '}
          <code style={{ color: 'var(--accent)' }}>.musync/</code> so you can revert in History.
        </div>
      )}
    </div>
  )
}
