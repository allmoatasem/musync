import { useState, useEffect } from 'react'
import SyncView from './components/SyncView'
import HistoryView from './components/HistoryView'
import WatchView from './components/WatchView'
import { checkHealth } from './api'

type View = 'sync' | 'history' | 'watch'

const NAV = [
  { id: 'sync' as View,    icon: '⇄',  label: 'Sync' },
  { id: 'history' as View, icon: '◷',  label: 'History' },
  { id: 'watch' as View,   icon: '◉',  label: 'Watch' },
]

export default function App() {
  const [view, setView] = useState<View>('sync')
  const [serverOk, setServerOk] = useState<boolean | null>(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      const ok = await checkHealth()
      if (!cancelled) setServerOk(ok)
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => { cancelled = true; clearInterval(id) }
  }, [])

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-logo">Mu<span>Sync</span></div>
        <nav className="sidebar-nav">
          {NAV.map((n) => (
            <button
              key={n.id}
              className={`nav-item no-drag ${view === n.id ? 'active' : ''}`}
              onClick={() => setView(n.id)}
            >
              <span className="nav-icon">{n.icon}</span>
              {n.label}
            </button>
          ))}
        </nav>
        <div style={{ marginTop: 'auto', padding: '16px 20px', fontSize: 11, color: 'var(--text-dim)', display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{
            width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
            background: serverOk === null ? 'var(--text-dim)' : serverOk ? 'var(--green)' : 'var(--red)',
            boxShadow: serverOk ? '0 0 5px var(--green)' : 'none',
          }} />
          {serverOk === null ? 'Connecting…' : serverOk ? 'Ready' : 'Server offline'}
        </div>
      </aside>

      <div className="main">
        <div className="titlebar" />
        {!serverOk && serverOk !== null && (
          <div style={{ background: 'rgba(224,92,92,0.12)', borderBottom: '1px solid var(--red)', padding: '10px 24px', fontSize: 12, color: 'var(--red)' }}>
            Server not running. Start with: <code style={{ background: 'rgba(255,255,255,0.07)', padding: '1px 5px', borderRadius: 3 }}>musync serve</code>
          </div>
        )}
        {view === 'sync'    && <SyncView />}
        {view === 'history' && <HistoryView />}
        {view === 'watch'   && <WatchView />}
      </div>
    </div>
  )
}
