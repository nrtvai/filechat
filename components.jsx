// FileChat — main UI components
// Depends on data.jsx globals.

const { useState, useEffect, useRef, useMemo } = React;

// —— atoms ——————————————————————————————————————————————————

function StatusDot({ status }) {
  const cls = {
    queued: 'dot-idle', reading: 'dot-working', indexing: 'dot-working',
    ready: 'dot-ready', failed: 'dot-err'
  }[status] || 'dot-idle';
  return <span className={`dot ${cls}`} />;
}

function FileMark({ ext }) {
  return <div className="filemark">{ext}</div>;
}

function Capillary({ p, indeterminate }) {
  return (
    <div
      className={"cap " + (indeterminate ? "indeterminate" : "")}
      style={{ "--p": p ?? 0 }}
    />
  );
}

function IconPaperclip() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 11.5L12.5 20a5 5 0 1 1-7-7L14 4.5a3.5 3.5 0 0 1 5 5L10.5 18a2 2 0 1 1-3-3L15 7.5"/>
    </svg>
  );
}
function IconSend() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14M13 5l7 7-7 7"/>
    </svg>
  );
}
function IconSessions() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M4 6h16M4 12h16M4 18h10"/>
    </svg>
  );
}
function IconFiles() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/>
      <path d="M14 3v5h5"/>
    </svg>
  );
}
function IconClose() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round">
      <path d="M1 1l8 8M9 1l-8 8"/>
    </svg>
  );
}
function IconChevronR() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 2l4 4-4 4"/>
    </svg>
  );
}
function IconPlus() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
      <path d="M6 1v10M1 6h10"/>
    </svg>
  );
}
function IconSearch() {
  return (
    <svg width="13" height="13" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
      <circle cx="6" cy="6" r="4.25"/><path d="M9.2 9.2l3 3"/>
    </svg>
  );
}

// —— topbar ————————————————————————————————————————————————

function TopBar({ onSidebar, railOpen, onMobile, mobile, provider, onState, state }) {
  return (
    <header style={{
      height: 48, flex: '0 0 48px',
      borderBottom: '1px solid var(--rule)',
      display: 'flex', alignItems: 'center', gap: 16,
      padding: '0 16px 0 12px',
      background: 'var(--paper)',
      position: 'relative',
      zIndex: 20,
    }}>
      <button className="btn btn-ghost btn-sm" onClick={onSidebar} aria-label="Toggle sidebar" style={{ padding: '0 8px' }}>
        <IconSessions />
      </button>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <div style={{
          fontFamily: 'var(--f-serif)', fontSize: 19, letterSpacing: '-0.01em',
          fontWeight: 500, color: 'var(--ink)',
        }}>FileChat</div>
        <div className="mono caps" style={{ color: 'var(--ink-4)', fontSize: 9.5 }}>
          local · v0.8.2
        </div>
      </div>

      <div style={{ flex: 1 }} />

      <div className="mono" style={{
        fontSize: 11, color: 'var(--ink-3)',
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '4px 10px', border: '1px solid var(--rule)', borderRadius: 2,
        background: 'var(--card)'
      }}>
        <span className="dot dot-ready" /> {provider}
      </div>

      <div className="mono" style={{
        fontSize: 10, color: 'var(--ink-4)', letterSpacing: '0.1em', textTransform: 'uppercase'
      }}>
        grounded · strict
      </div>
    </header>
  );
}

// —— left rail ——————————————————————————————————————————————

function LeftRail({ open, mode, setMode, files, state, onStateDemo }) {
  if (!open) {
    return (
      <aside style={{
        width: 48, borderRight: '1px solid var(--rule)',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        padding: '14px 0', gap: 4, background: 'var(--paper)',
      }}>
        <button className="btn btn-ghost btn-sm" title="Sessions" style={{padding:'0 8px'}}>
          <IconSessions />
        </button>
        <button className="btn btn-ghost btn-sm" title="Files" style={{padding:'0 8px'}}>
          <IconFiles />
        </button>
      </aside>
    );
  }
  return (
    <aside style={{
      width: 264, flex: '0 0 264px',
      borderRight: '1px solid var(--rule)',
      display: 'flex', flexDirection: 'column',
      background: 'var(--paper)',
      minHeight: 0,
    }}>
      {/* mode toggle */}
      <div style={{ padding: '12px 12px 8px' }}>
        <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'flex-start', height: 34 }}>
          <IconPlus /> New session
        </button>
      </div>
      <div style={{ padding: '0 12px 10px' }}>
        <div style={{ position: 'relative' }}>
          <div style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: 'var(--ink-4)' }}>
            <IconSearch />
          </div>
          <input
            placeholder="Search sessions…"
            style={{
              width: '100%', height: 30, padding: '0 10px 0 30px',
              background: 'var(--card)', border: '1px solid var(--rule-2)',
              borderRadius: 2, fontSize: 12, color: 'var(--ink)',
            }}
          />
        </div>
      </div>

      <div className="seg" style={{ margin: '0 12px 10px' }}>
        <button className={mode === 'sessions' ? 'on' : ''} onClick={() => setMode('sessions')}>Sessions</button>
        <button className={mode === 'files' ? 'on' : ''} onClick={() => setMode('files')}>
          Files <span style={{ marginLeft: 4, opacity: .6 }}>{files.length}</span>
        </button>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '0 6px 10px' }}>
        {mode === 'sessions' ? <SessionList /> : <FileRailList files={files} />}
      </div>

      {/* demo state switcher — always shown, tagged 'demo' */}
      <div style={{ borderTop: '1px solid var(--rule)', padding: 12, background: 'var(--paper-2)' }}>
        <div className="mono caps" style={{ color: 'var(--ink-3)', marginBottom: 8 }}>Demo · app state</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 4 }}>
          {APP_STATES.map(s => (
            <button
              key={s}
              onClick={() => onStateDemo(s)}
              className="mono"
              style={{
                height: 24, fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase',
                border: '1px solid var(--rule)', borderRadius: 2,
                background: state === s ? 'var(--ink)' : 'var(--card)',
                color: state === s ? 'var(--paper)' : 'var(--ink-3)',
              }}
            >{s}</button>
          ))}
        </div>
      </div>
    </aside>
  );
}

function SessionList() {
  return (
    <div>
      <div className="mono caps" style={{ padding: '8px 10px 4px', color: 'var(--ink-4)' }}>Recent</div>
      {SESSIONS.map(s => (
        <div
          key={s.id}
          style={{
            display: 'flex', flexDirection: 'column', gap: 2,
            padding: '8px 10px', borderRadius: 2,
            background: s.active ? 'var(--card)' : 'transparent',
            border: s.active ? '1px solid var(--rule-2)' : '1px solid transparent',
            cursor: 'pointer',
            marginBottom: 1,
          }}
          onMouseEnter={e => { if (!s.active) e.currentTarget.style.background = 'var(--paper-2)'; }}
          onMouseLeave={e => { if (!s.active) e.currentTarget.style.background = 'transparent'; }}
        >
          <div style={{
            fontSize: 13, color: 'var(--ink)',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            fontWeight: s.active ? 500 : 400,
          }}>{s.title}</div>
          <div style={{ display: 'flex', justifyContent: 'space-between', color: 'var(--ink-4)', fontSize: 11 }}>
            <span>{s.updated}</span>
            <span className="mono num">{s.files} files</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function FileRailList({ files }) {
  if (!files.length) {
    return (
      <div style={{ padding: '20px 12px', color: 'var(--ink-4)', fontSize: 12 }}>
        No files in this session yet.
      </div>
    );
  }
  return (
    <div style={{ padding: '4px' }}>
      {files.map(f => (
        <div key={f.id} style={{
          padding: '8px 8px',
          borderBottom: '1px solid var(--rule-2)',
          display: 'flex', gap: 10, alignItems: 'flex-start'
        }}>
          <FileMark ext={f.ext} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12, color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {f.name}
            </div>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', marginTop: 2, display:'flex', alignItems:'center', gap:6 }}>
              <StatusDot status={f.status} />
              <span style={{ textTransform: 'capitalize' }}>{f.status}</span>
              <span style={{ color: 'var(--ink-4)' }}>·</span>
              <span style={{ color: 'var(--ink-4)' }}>{f.size}</span>
            </div>
            {f.status !== 'ready' && (
              <div style={{ marginTop: 6 }}>
                <Capillary p={f.progress} indeterminate={f.status === 'queued'} />
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, {
  StatusDot, FileMark, Capillary,
  IconPaperclip, IconSend, IconSessions, IconFiles, IconClose, IconChevronR, IconPlus, IconSearch,
  TopBar, LeftRail, SessionList, FileRailList,
});
