// FileChat — right panel (Files / Citations / Settings), Tweaks, mobile
// Depends on data.jsx, components.jsx, center.jsx globals.

const { useState: useRS, useEffect: useRE } = React;

// —— right panel ————————————————————————————————————————

function RightPanel({ open, onClose, tab, setTab, files, state }) {
  if (!open) {
    return (
      <aside style={{
        width: 40, borderLeft: '1px solid var(--rule)',
        display: 'flex', flexDirection: 'column', alignItems: 'center',
        padding: '12px 0', gap: 6, background: 'var(--paper)',
      }}>
        {['files', 'citations', 'settings'].map(t => (
          <button
            key={t}
            className="mono"
            onClick={() => { setTab(t); onClose(false); }}
            style={{
              writingMode: 'vertical-rl', transform: 'rotate(180deg)',
              padding: '10px 6px', fontSize: 10.5, letterSpacing: '0.14em',
              textTransform: 'uppercase',
              color: 'var(--ink-3)',
              border: '1px solid transparent',
              borderRadius: 2,
            }}
            onMouseEnter={e => e.currentTarget.style.background = 'var(--paper-2)'}
            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
          >{t}</button>
        ))}
      </aside>
    );
  }
  return (
    <aside style={{
      width: 360, flex: '0 0 360px',
      borderLeft: '1px solid var(--rule)',
      display: 'flex', flexDirection: 'column',
      background: 'var(--paper)',
      minHeight: 0,
    }}>
      <div style={{
        display: 'flex', alignItems: 'stretch',
        borderBottom: '1px solid var(--rule)',
      }}>
        {['files', 'citations', 'settings'].map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className="mono caps"
            style={{
              flex: 1, height: 40, fontSize: 10.5,
              color: tab === t ? 'var(--ink)' : 'var(--ink-3)',
              borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
              marginBottom: -1,
            }}
          >{t}</button>
        ))}
        <button onClick={() => onClose(true)} className="btn btn-ghost" style={{width: 32, borderRadius: 0, color: 'var(--ink-4)'}}>
          <IconClose />
        </button>
      </div>

      <div style={{flex: 1, overflow: 'auto', minHeight: 0}}>
        {tab === 'files' && <FilesTab files={files} state={state} />}
        {tab === 'citations' && <CitationsTab state={state} />}
        {tab === 'settings' && <SettingsTab />}
      </div>
    </aside>
  );
}

function FilesTab({ files, state }) {
  if (!files.length) {
    return (
      <div style={{ padding: 24 }}>
        <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 8 }}>No files</div>
        <p style={{ color: 'var(--ink-3)', fontSize: 13, fontFamily: 'var(--f-serif)' }}>
          Attach files to populate this panel. You'll see each file's parse status, chunk count, and a preview of what FileChat extracted.
        </p>
      </div>
    );
  }
  const total = files.length;
  const ready = files.filter(f => f.status === 'ready').length;
  const chunks = files.reduce((a, f) => a + f.chunks, 0);
  return (
    <div>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--rule-2)' }}>
        <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 6 }}>Session index</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
          <Stat label="Files" value={`${ready}/${total}`} />
          <Stat label="Chunks" value={chunks.toString()} />
          <Stat label="Tokens" value={`${(chunks * 0.38).toFixed(1)}k`} />
        </div>
      </div>
      {files.map(f => <FileDetail key={f.id} f={f} />)}
    </div>
  );
}
function Stat({ label, value }) {
  return (
    <div>
      <div className="mono num" style={{ fontFamily: 'var(--f-serif)', fontSize: 22, color: 'var(--ink)' }}>{value}</div>
      <div className="mono caps" style={{ color: 'var(--ink-4)', fontSize: 9.5, marginTop: 2 }}>{label}</div>
    </div>
  );
}
function FileDetail({ f }) {
  return (
    <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--rule-2)' }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <FileMark ext={f.ext} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, color: 'var(--ink)', marginBottom: 2 }}>{f.name}</div>
          <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
            {f.pages} pp · {f.size} · {f.chunks} chunks
          </div>
        </div>
        <button className="btn btn-ghost btn-sm" style={{ color: 'var(--ink-4)' }}>
          <IconClose />
        </button>
      </div>
      <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8 }}>
        <StatusDot status={f.status} />
        <span className="mono" style={{ fontSize: 10.5, color: 'var(--ink-2)' }}>{f.note}</span>
      </div>
      {f.status !== 'ready' && (
        <div style={{ marginTop: 8 }}>
          <Capillary p={f.progress} indeterminate={f.status === 'queued'} />
        </div>
      )}
    </div>
  );
}

function CitationsTab({ state }) {
  if (state !== 'answered') {
    return (
      <div style={{ padding: 24 }}>
        <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 8 }}>No citations yet</div>
        <p style={{ color: 'var(--ink-3)', fontSize: 13, fontFamily: 'var(--f-serif)' }}>
          Once FileChat answers a question, each referenced source appears here with an excerpt. Clicking a source in the transcript scrolls this panel to the matching note.
        </p>
      </div>
    );
  }
  return (
    <div>
      <div style={{ padding: '14px 16px 8px', borderBottom: '1px solid var(--rule-2)' }}>
        <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 6 }}>Sources · this answer</div>
        <div style={{ fontFamily: 'var(--f-serif)', fontSize: 14, color: 'var(--ink-2)', fontStyle: 'italic' }}>
          Three excerpts, read in order.
        </div>
      </div>
      {CITATIONS.map(c => (
        <div key={c.n} style={{ padding: '16px 16px', borderBottom: '1px solid var(--rule-2)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 8 }}>
            <span className="mono" style={{
              background: 'var(--accent)', color: '#fff', padding: '2px 5px',
              fontSize: 10, letterSpacing: '0.04em',
            }}>{c.n}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12.5, color: 'var(--ink)' }}>{c.file}</div>
              <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>{c.loc}</div>
            </div>
          </div>
          <div style={{
            fontFamily: 'var(--f-serif)', fontSize: 13.5, lineHeight: 1.55,
            color: 'var(--ink-2)',
            borderLeft: '2px solid var(--rule)',
            paddingLeft: 12, fontStyle: 'italic',
          }}>
            "{c.excerpt}"
          </div>
          <div style={{ marginTop: 10, display: 'flex', gap: 6 }}>
            <button className="btn btn-ghost btn-sm">Open source</button>
            <button className="btn btn-ghost btn-sm">Copy excerpt</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function SettingsTab() {
  const [openSection, setOpen] = useRS('provider');
  const Sec = ({ id, title, children }) => (
    <div style={{ borderBottom: '1px solid var(--rule-2)' }}>
      <button
        onClick={() => setOpen(openSection === id ? null : id)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '14px 16px', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <span className="mono caps" style={{ color: 'var(--ink-3)' }}>{title}</span>
        <span style={{
          transform: openSection === id ? 'rotate(90deg)' : 'rotate(0deg)',
          transition: 'transform .2s', color: 'var(--ink-4)',
        }}><IconChevronR /></span>
      </button>
      {openSection === id && (
        <div style={{ padding: '0 16px 16px' }}>{children}</div>
      )}
    </div>
  );
  return (
    <div>
      <Sec id="provider" title="Model provider">
        <div style={{ marginBottom: 12 }}>
          {PROVIDERS.map(p => (
            <label key={p.key} style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '8px 10px', borderRadius: 2,
              background: p.active ? 'var(--card)' : 'transparent',
              border: p.active ? '1px solid var(--rule-2)' : '1px solid transparent',
              marginBottom: 4, cursor: 'pointer',
            }}>
              <input type="radio" name="prov" defaultChecked={p.active} style={{ marginTop: 3, accentColor: 'var(--accent)' }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, color: 'var(--ink)' }}>{p.name}</div>
                <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
                  {p.models.join(' · ')}
                </div>
              </div>
              {p.active && <span className="mono caps" style={{ color: 'var(--ok)', fontSize: 9.5 }}>active</span>}
            </label>
          ))}
        </div>
        <Field label="Endpoint" value="http://127.0.0.1:11434" />
        <Field label="Model" value="llama3.1-70b-q4" />
      </Sec>
      <Sec id="ground" title="Grounding">
        <Row label="Strict grounding" desc="Refuse if not found in attached files."><Toggle on /></Row>
        <Row label="Cite inline" desc="Superscript references in answers."><Toggle on /></Row>
        <Row label="Show excerpt cards" desc="Margin previews of each cited chunk."><Toggle on /></Row>
        <Row label="Retrieval depth" desc="8 chunks per query (balanced)."><span className="mono" style={{color:'var(--ink-3)', fontSize:11}}>8 · balanced</span></Row>
      </Sec>
      <Sec id="index" title="Index & storage">
        <Row label="Embedding model" desc="nomic-embed-text · local"><span className="mono" style={{color:'var(--ink-3)', fontSize:11}}>nomic-embed</span></Row>
        <Row label="Chunk size" desc="768 tokens · 10% overlap"><span className="mono" style={{color:'var(--ink-3)', fontSize:11}}>768</span></Row>
        <Row label="Disk cache" desc="1.3 GB in ~/Library/FileChat"><button className="btn btn-sm">Reveal</button></Row>
      </Sec>
      <Sec id="diag" title="Diagnostics">
        <Log lines={[
          "[14:02:11] session 's1' opened",
          "[14:02:12] reading 'FY25 Annual Report.pdf' — 62 pp",
          "[14:02:18] chunked → 184 · nomic-embed",
          "[14:02:23] indexed. ready.",
          "[14:04:02] retrieval q='NA revenue…' → 8 chunks",
          "[14:04:03] gen → llama3.1-70b-q4 · 892 tok",
        ]} />
      </Sec>
    </div>
  );
}
function Field({ label, value }) {
  return (
    <div style={{ marginTop: 8 }}>
      <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 4 }}>{label}</div>
      <input defaultValue={value} style={{
        width: '100%', height: 30, padding: '0 10px',
        background: 'var(--card)', border: '1px solid var(--rule-2)', borderRadius: 2,
        fontFamily: 'var(--f-mono)', fontSize: 11.5,
      }} />
    </div>
  );
}
function Row({ label, desc, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0', borderBottom: '1px solid var(--rule-2)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, color: 'var(--ink)' }}>{label}</div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>{desc}</div>
      </div>
      {children}
    </div>
  );
}
function Toggle({ on }) {
  const [v, setV] = useRS(!!on);
  return (
    <button onClick={() => setV(!v)} style={{
      width: 34, height: 18, borderRadius: 10,
      background: v ? 'var(--accent)' : 'var(--rule)',
      position: 'relative', transition: 'background .15s',
    }}>
      <span style={{
        position: 'absolute', top: 2, left: v ? 18 : 2, width: 14, height: 14,
        background: '#fff', borderRadius: '50%', transition: 'left .15s',
        boxShadow: '0 1px 2px rgba(0,0,0,.2)',
      }} />
    </button>
  );
}
function Log({ lines }) {
  return (
    <div className="mono" style={{
      padding: 10, background: 'var(--paper-2)', border: '1px solid var(--rule-2)',
      borderRadius: 2, fontSize: 10.5, color: 'var(--ink-3)', lineHeight: 1.6,
      maxHeight: 160, overflow: 'auto',
    }}>
      {lines.map((l, i) => <div key={i}>{l}</div>)}
    </div>
  );
}

// —— Tweaks panel ————————————————————————————————————————

function TweaksPanel({ open, setOpen, cfg, setCfg }) {
  if (!open) return null;
  const set = (k, v) => setCfg({ ...cfg, [k]: v });
  return (
    <div className="tweaks">
      <div className="tweaks-hdr">
        <span>Tweaks</span>
        <button onClick={() => setOpen(false)} className="btn btn-ghost btn-sm" style={{height: 20, padding: '0 4px'}}>
          <IconClose />
        </button>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">App state</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 4 }}>
          {APP_STATES.map(s => (
            <button
              key={s}
              onClick={() => set('state', s)}
              className="mono"
              style={{
                height: 24, fontSize: 10, letterSpacing: '0.04em', textTransform: 'uppercase',
                border: '1px solid var(--rule)',
                background: cfg.state === s ? 'var(--ink)' : 'var(--card)',
                color: cfg.state === s ? 'var(--paper)' : 'var(--ink-3)',
              }}
            >{s}</button>
          ))}
        </div>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">Theme</div>
        <div className="seg">
          {['archive', 'atelier', 'reading'].map(t => (
            <button key={t} onClick={() => set('theme', t)} className={cfg.theme === t ? 'on' : ''}>
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">Type pairing</div>
        <div className="seg">
          {[['mix','serif + sans'], ['serif','all serif'], ['grotesk','all sans']].map(([v, l]) => (
            <button key={v} onClick={() => set('type', v)} className={cfg.type === v ? 'on' : ''}>{l}</button>
          ))}
        </div>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">Density</div>
        <div className="seg">
          {['compact', 'balanced', 'spacious'].map(d => (
            <button key={d} onClick={() => set('density', d)} className={cfg.density === d ? 'on' : ''}>{d}</button>
          ))}
        </div>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">Right panel</div>
        <div className="seg">
          {['files', 'citations', 'settings'].map(t => (
            <button key={t} onClick={() => set('rightTab', t)} className={cfg.rightTab === t ? 'on' : ''}>{t}</button>
          ))}
        </div>
      </div>

      <div className="tweak-row">
        <div className="tweak-label">Viewport</div>
        <div className="seg">
          {[['desktop','desktop'], ['mobile','mobile']].map(([v, l]) => (
            <button key={v} onClick={() => set('view', v)} className={cfg.view === v ? 'on' : ''}>{l}</button>
          ))}
        </div>
      </div>
    </div>
  );
}

// —— Mobile ——————————————————————————————————————————————

function MobileView({ state, files }) {
  return (
    <div style={{
      minHeight: '100vh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', padding: 20,
      background: 'var(--paper-2)',
    }}>
      <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 8 }}>
        FileChat · mobile · state: {state}
      </div>
      <div className="mobile-frame">
        <div className="mobile-inner" style={{display:'flex', flexDirection:'column'}}>
          {/* status bar */}
          <div style={{
            height: 44, display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '0 24px', fontFamily: 'var(--f-mono)', fontSize: 11, color: 'var(--ink)',
          }}>
            <span>9:41</span>
            <span className="mono" style={{letterSpacing: '0.1em'}}>◉ ◉ ◉</span>
          </div>
          {/* header */}
          <div style={{
            padding: '4px 18px 12px', borderBottom: '1px solid var(--rule)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          }}>
            <button className="btn btn-ghost btn-sm" style={{padding:'0 4px'}}>
              <IconSessions />
            </button>
            <div style={{ fontFamily: 'var(--f-serif)', fontSize: 17, fontWeight: 500 }}>
              {state === 'empty' ? 'New session' : 'Annual report — variance'}
            </div>
            <button className="btn btn-ghost btn-sm" style={{padding:'0 4px'}}>
              <IconFiles />
            </button>
          </div>

          {/* content */}
          {state === 'empty' && <MobileEmpty />}
          {state === 'selected' && <MobileFiles files={SELECTED_FILES} state="selected" />}
          {state === 'processing' && <MobileFiles files={PROCESSING_FILES} state="processing" />}
          {state === 'ready' && <MobileFiles files={READY_FILES} state="ready" />}
          {state === 'answered' && <MobileAnswered />}

          {/* composer */}
          <div style={{
            borderTop: '1px solid var(--rule)', padding: '10px 12px 18px',
            background: 'var(--paper)',
          }}>
            <div style={{
              border: '1px solid var(--rule)', background: 'var(--card)',
              borderRadius: 2, padding: '10px 12px',
              display: 'flex', alignItems: 'flex-end', gap: 8,
            }}>
              <button className="btn btn-ghost btn-sm" style={{padding: '0 4px'}}>
                <IconPaperclip />
              </button>
              <div style={{
                flex: 1, fontFamily: 'var(--f-serif)', fontSize: 14,
                color: files.length ? 'var(--ink-3)' : 'var(--ink-4)',
                minHeight: 18,
              }}>
                {files.length ? "Ask a question about these files…" : "Attach files to begin."}
              </div>
              <button className={files.some(f=>f.status==='ready') ? "btn btn-accent btn-sm" : "btn btn-sm"} style={{padding: '0 10px'}}>
                <IconSend />
              </button>
            </div>
            <div className="mono" style={{fontSize:9.5, color:'var(--ink-4)', textAlign:'center', marginTop:8, letterSpacing:'0.1em', textTransform:'uppercase'}}>
              grounded · strict · local
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
function MobileEmpty() {
  return (
    <div style={{flex:1, padding:'24px 20px', overflow:'auto'}}>
      <div className="mono caps" style={{color:'var(--accent)', marginBottom:10}}>No files yet</div>
      <h2 style={{fontFamily:'var(--f-serif)', fontSize:24, lineHeight:1.1, margin:'0 0 10px', fontWeight:400, letterSpacing:'-0.01em'}}>
        Attach files.<br/><span style={{color:'var(--ink-3)', fontStyle:'italic'}}>Ask anything grounded in them.</span>
      </h2>
      <p style={{fontFamily:'var(--f-serif)', color:'var(--ink-3)', fontSize:13, margin:'0 0 16px'}}>
        Answers only come from what you attach. Nothing leaves your device.
      </p>
      <button className="btn btn-primary" style={{width:'100%', height:38}}>
        <IconPaperclip/> Attach files
      </button>
      <div className="mono caps" style={{color:'var(--ink-4)', marginTop:20, marginBottom:8}}>Try asking</div>
      {SUGGESTED_PROMPTS.slice(0,3).map((p,i)=>(
        <div key={i} style={{
          padding:'10px 12px', border:'1px solid var(--rule-2)', borderRadius:2,
          fontSize:12.5, fontFamily:'var(--f-serif)', color:'var(--ink-2)',
          marginBottom: 6, background: 'var(--card)', opacity: .7,
        }}>{p}</div>
      ))}
    </div>
  );
}
function MobileFiles({ files, state }) {
  const ready = files.filter(f=>f.status==='ready').length;
  return (
    <div style={{flex:1, padding:'16px 16px', overflow:'auto'}}>
      <div className="mono caps" style={{color:'var(--accent)', marginBottom:6}}>
        {state === 'ready' ? 'Ready' : state === 'processing' ? 'Processing' : 'Attached'}
      </div>
      <h3 style={{fontFamily:'var(--f-serif)', fontSize:18, margin:'0 0 14px', fontWeight:400}}>
        {state === 'ready' ? `${files.length} files ready.` : `${ready} of ${files.length} ready`}
      </h3>
      {files.map(f => (
        <div key={f.id} style={{
          padding:'10px 12px', border:'1px solid var(--rule-2)', borderRadius:2,
          marginBottom:6, background: 'var(--card)',
        }}>
          <div style={{display:'flex', gap:10, alignItems:'center'}}>
            <FileMark ext={f.ext} />
            <div style={{flex:1, minWidth:0}}>
              <div style={{fontSize:12.5, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>{f.name}</div>
              <div className="mono" style={{fontSize:10, color:'var(--ink-4)', marginTop:2, display:'flex', gap:6, alignItems:'center'}}>
                <StatusDot status={f.status} />
                <span>{f.note}</span>
              </div>
            </div>
          </div>
          {f.status !== 'ready' && <div style={{marginTop:8}}><Capillary p={f.progress} indeterminate={f.status==='queued'} /></div>}
        </div>
      ))}
    </div>
  );
}
function MobileAnswered() {
  return (
    <div style={{flex:1, padding:'16px 16px', overflow:'auto'}}>
      <div style={{
        background:'var(--card)', border:'1px solid var(--rule-2)', padding:'10px 12px',
        marginBottom: 14, fontFamily:'var(--f-serif)', fontSize: 13.5, color:'var(--ink)',
      }}>
        What drove the 14% YoY revenue gain in North America?
      </div>
      <div className="mono caps" style={{color:'var(--ink-4)', marginBottom:6}}>
        FileChat · 3 sources
      </div>
      <p style={{fontFamily:'var(--f-serif)', fontSize:14, lineHeight:1.55, color:'var(--ink-2)', margin:'0 0 10px'}}>
        The gain is attributed to two compounding factors: a full-year contribution from the Western acquisition<sup className="cite">1</sup>, and specialty-category unit growth that outpaced the broader market by ~4 points<sup className="cite">2</sup>.
      </p>
      <div style={{display:'flex', flexWrap:'wrap', gap:6, marginTop:12}}>
        {CITATIONS.slice(0,2).map(c => (
          <div key={c.n} style={{
            display:'inline-flex', alignItems:'center', gap:6,
            padding:'3px 8px 3px 4px', border:'1px solid var(--rule-2)',
            fontSize:10.5, background: 'var(--card)',
          }}>
            <span className="mono" style={{fontSize:9, background:'var(--accent)', color:'#fff', padding:'2px 4px'}}>{c.n}</span>
            <span style={{maxWidth:180, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{c.file}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

Object.assign(window, {
  RightPanel, TweaksPanel, MobileView,
});
