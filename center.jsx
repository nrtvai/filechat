// FileChat — center column (empty state, transcript, composer) + right panel
// Depends on data.jsx and components.jsx globals.

const { useState: useS, useEffect: useE, useRef: useR } = React;

// —— composer ——————————————————————————————————————————————

function Composer({ big, files, onSend, value, onChange }) {
  const [hidden, setHidden] = useS(new Set());
  const visibleFiles = files.filter(f => !hidden.has(f.id));
  const removeFile = (id) => setHidden(prev => new Set([...prev, id]));
  // reset hidden when files prop changes (state switch)
  useE(() => { setHidden(new Set()); }, [files]);
  const ready = visibleFiles.some(f => f.status === 'ready');
  const anyFiles = visibleFiles.length > 0;
  const allReady = anyFiles && visibleFiles.every(f => f.status === 'ready');
  const indexing = visibleFiles.some(f => f.status === 'indexing' || f.status === 'reading' || f.status === 'queued');

  let helper = null;
  if (!anyFiles) helper = { tone: 'idle', text: "Attach one or more files to begin. FileChat only answers from sources you give it." };
  else if (!ready) helper = { tone: 'work', text: "Processing your files. You can write the question now and send it the moment they're ready." };
  else if (indexing) helper = { tone: 'work', text: "Some files are still indexing. Ready sources will be used; the rest will be added as they finish." };
  else helper = { tone: 'ok', text: `Ready to answer from ${visibleFiles.length} source${visibleFiles.length === 1 ? '' : 's'}.` };

  const canSend = ready && value.trim().length > 0;

  return (
    <div style={{
      width: '100%',
      maxWidth: big ? 720 : 820,
      margin: '0 auto',
    }}>
      {big && (
        <div style={{ marginBottom: 14, textAlign: 'center' }}>
          <div className="mono caps" style={{ color: 'var(--ink-4)' }}>{helper.text}</div>
        </div>
      )}
      <div style={{
        border: '1px solid var(--rule)',
        background: 'var(--card)',
        borderRadius: 2,
        boxShadow: big ? 'var(--shadow-lift)' : 'var(--shadow)',
        transition: 'box-shadow .2s',
      }}>
        {/* attached file chips */}
        {anyFiles && (
          <div style={{
            display: 'flex', flexWrap: 'wrap', gap: 6,
            padding: '10px 12px 2px',
            borderBottom: '1px solid var(--rule-2)',
          }}>
            {visibleFiles.map(f => (
              <FileChip key={f.id} f={f} onRemove={() => removeFile(f.id)} />
            ))}
          </div>
        )}

        <textarea
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={anyFiles ? "Ask a question about these files…" : "Start by attaching files — then ask a question."}
          rows={big ? 3 : 2}
          style={{
            width: '100%', padding: '14px 16px',
            background: 'transparent', border: 0, outline: 'none',
            fontSize: big ? 18 : 15,
            fontFamily: 'var(--f-serif)',
            color: 'var(--ink)',
            lineHeight: 1.45,
          }}
        />

        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '8px 10px 8px 10px',
          borderTop: '1px solid var(--rule-2)',
        }}>
          <button className="btn btn-ghost btn-sm">
            <IconPaperclip /> Attach
          </button>
          <div style={{width: 1, height: 18, background: 'var(--rule-2)'}} />
          <button className="btn btn-ghost btn-sm mono" style={{fontSize:11, color:'var(--ink-3)'}}>
            Grounded · strict
          </button>

          <div style={{flex: 1}} />

          <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>
            ⌘↵ to send
          </div>
          <button
            className={canSend ? "btn btn-accent btn-sm" : "btn btn-sm"}
            disabled={!canSend}
            onClick={onSend}
            style={{opacity: canSend ? 1 : .55}}
          >
            Ask <IconSend />
          </button>
        </div>
      </div>

      {!big && (
        <div style={{
          marginTop: 8, display: 'flex', justifyContent: 'space-between',
          fontSize: 11, color: 'var(--ink-4)',
        }}>
          <span className="mono">{helper.text}</span>
          <span className="mono">local model · ollama/llama3.1-70b</span>
        </div>
      )}
    </div>
  );
}

function FileChip({ f, onRemove }) {
  const [hoverX, setHoverX] = useS(false);
  const label = f.status === 'ready' ? 'ready'
              : f.status === 'indexing' ? `indexing ${Math.round(f.progress*100)}%`
              : f.status === 'reading' ? `reading ${Math.round(f.progress*100)}%`
              : 'queued';
  return (
    <div title={f.name} style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '4px 6px 4px 6px',
      background: 'var(--paper)',
      border: '1px solid var(--rule-2)',
      borderRadius: 2,
      fontSize: 12,
      color: 'var(--ink-2)',
      maxWidth: 320,
    }}>
      <span className="mono" style={{
        fontSize: 9, letterSpacing: '0.04em',
        background: 'var(--ink)', color: 'var(--paper)',
        padding: '2px 4px',
      }}>{f.ext}</span>
      <span style={{whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis', maxWidth: 200}}>
        {f.name}
      </span>
      <span className="mono" style={{fontSize: 10, color: 'var(--ink-4)'}}>·</span>
      <span className="mono" style={{fontSize: 10, color: f.status === 'ready' ? 'var(--ok)' : 'var(--accent)'}}>
        {label}
      </span>
      <button
        title="Remove from context"
        onMouseEnter={() => setHoverX(true)}
        onMouseLeave={() => setHoverX(false)}
        onClick={onRemove}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          width: 16, height: 16, borderRadius: 2, flexShrink: 0,
          background: hoverX ? 'var(--paper-3)' : 'transparent',
          color: hoverX ? 'var(--ink-2)' : 'var(--ink-4)',
          transition: 'background .12s, color .12s',
          marginLeft: 2,
        }}
      >
        <IconClose />
      </button>
    </div>
  );
}

// —— empty state ——————————————————————————————————————————

function EmptyState({ onAttach, files, onSend, value, onChange }) {
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      padding: '60px 40px 40px',
      minHeight: '100%',
      justifyContent: 'center',
    }}>
      <div style={{ textAlign: 'center', marginBottom: 28, maxWidth: 640 }}>
        <div className="mono caps" style={{ color: 'var(--accent)', marginBottom: 14 }}>
          New session · no files yet
        </div>
        <h1 style={{
          fontFamily: 'var(--f-serif)',
          fontSize: 44, lineHeight: 1.08,
          letterSpacing: '-0.018em', margin: '0 0 16px',
          fontWeight: 400,
          color: 'var(--ink)',
          textWrap: 'pretty',
        }}>
          Attach your files.<br />
          <span style={{color: 'var(--ink-3)', fontStyle: 'italic'}}>Ask anything grounded in them.</span>
        </h1>
        <p style={{
          fontSize: 15, color: 'var(--ink-3)', margin: '0 auto', maxWidth: 520,
          fontFamily: 'var(--f-serif)', lineHeight: 1.5,
        }}>
          FileChat only answers from the PDFs, documents, spreadsheets, and notes you
          attach. Nothing leaves your machine unless you point it at a remote provider.
        </p>
      </div>

      <div style={{ width: '100%', maxWidth: 720, marginBottom: 24 }}>
        <Composer big files={files} onSend={onSend} value={value} onChange={onChange} />
      </div>

      <div style={{
        display: 'flex', gap: 10, alignItems: 'center',
        marginBottom: 36,
      }}>
        <button className="btn" onClick={onAttach}>
          <IconPaperclip /> Attach files…
        </button>
        <span className="mono" style={{ fontSize: 11, color: 'var(--ink-4)' }}>
          or drop anywhere — PDF · DOCX · XLSX · CSV · TXT · MD
        </span>
      </div>

      <div style={{
        width: '100%', maxWidth: 720,
        borderTop: '1px solid var(--rule-2)',
        paddingTop: 18,
      }}>
        <div className="mono caps" style={{ color: 'var(--ink-4)', marginBottom: 10 }}>
          What you can ask, once files are ready
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          {SUGGESTED_PROMPTS.map((p, i) => (
            <div key={i} style={{
              padding: '10px 12px',
              border: '1px solid var(--rule-2)',
              borderRadius: 2,
              fontSize: 13, color: 'var(--ink-2)',
              fontFamily: 'var(--f-serif)',
              background: 'var(--card)',
              cursor: 'not-allowed',
              opacity: 0.7,
            }}>
              <span className="mono" style={{ color: 'var(--ink-4)', fontSize: 10, marginRight: 8 }}>
                {String(i + 1).padStart(2, '0')}
              </span>
              {p}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// —— selected / processing preamble —————————————————————————

function ProcessingPreamble({ files, state, value, onChange, onSend }) {
  const total = files.length;
  const ready = files.filter(f => f.status === 'ready').length;
  const overall = files.reduce((a, f) => a + f.progress, 0) / Math.max(1, total);
  const allQueued = files.every(f => f.status === 'queued');

  return (
    <div style={{
      padding: '40px 40px 28px',
      maxWidth: 820, margin: '0 auto',
      width: '100%',
    }}>
      <div className="mono caps" style={{ color: 'var(--accent)', marginBottom: 10 }}>
        {allQueued ? 'Files attached · not yet processed' : state === 'processing' ? 'Processing files' : 'Files ready'}
      </div>
      <h2 style={{
        fontFamily: 'var(--f-serif)',
        fontSize: 28, lineHeight: 1.1, margin: '0 0 6px',
        fontWeight: 400, letterSpacing: '-0.01em',
      }}>
        {allQueued
          ? `${total} files attached. Begin processing to ask questions.`
          : state === 'processing'
            ? `Reading and indexing ${total} files…`
            : `${total} files ready. Ask anything.`}
      </h2>
      <p style={{ color: 'var(--ink-3)', fontSize: 14, margin: '0 0 18px', maxWidth: 620 }}>
        {allQueued
          ? "You can review what was attached below. Processing runs locally and usually takes under a minute for a few hundred pages."
          : state === 'processing'
            ? "Files are parsed, chunked, and embedded on-device. You can compose your question now; it will send the moment everything is ready."
            : "All sources are indexed and strict grounding is on. Answers will cite specific pages and sections."}
      </p>

      {/* files as a table */}
      <div className="card" style={{ overflow: 'hidden', marginBottom: 20 }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '28px 2.4fr 60px 80px 1fr 16px',
          columnGap: 12, padding: '8px 14px',
          borderBottom: '1px solid var(--rule)',
          background: 'var(--paper-2)',
        }}>
          {['', 'File', 'Type', 'Size', 'Status', ''].map((h, i) => (
            <div key={i} className="mono caps" style={{ color: 'var(--ink-3)', fontSize: 9.5 }}>{h}</div>
          ))}
        </div>
        {files.map((f, i) => (
          <div key={f.id} style={{
            display: 'grid',
            gridTemplateColumns: '28px 2.4fr 60px 80px 1fr 16px',
            columnGap: 12, padding: '12px 14px',
            borderBottom: i === files.length - 1 ? 0 : '1px solid var(--rule-2)',
            alignItems: 'center',
          }}>
            <FileMark ext={f.ext} />
            <div style={{minWidth: 0}}>
              <div style={{
                fontSize: 13, color: 'var(--ink)',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
              }}>{f.name}</div>
              {f.status !== 'ready' && (
                <div style={{ marginTop: 6, width: '100%' }}>
                  <Capillary p={f.progress} indeterminate={f.status === 'queued'} />
                </div>
              )}
            </div>
            <div className="mono" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{f.ext}</div>
            <div className="mono num" style={{ fontSize: 11, color: 'var(--ink-3)' }}>{f.size}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <StatusDot status={f.status} />
              <span className="mono" style={{ fontSize: 11, color: 'var(--ink-2)', textTransform: 'capitalize' }}>
                {f.note}
              </span>
            </div>
            <button className="btn btn-ghost btn-sm" style={{padding: '0 4px', color: 'var(--ink-4)'}}>
              <IconClose />
            </button>
          </div>
        ))}
      </div>

      {/* overall bar */}
      {state !== 'ready' && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 14,
          padding: '10px 14px', background: 'var(--paper-2)',
          border: '1px solid var(--rule-2)', borderRadius: 2, marginBottom: 20
        }}>
          <div className="mono caps" style={{ color: 'var(--ink-3)' }}>
            {allQueued ? 'Overall' : 'Processing'}
          </div>
          <div style={{ flex: 1 }}>
            <Capillary p={overall} indeterminate={allQueued} />
          </div>
          <div className="mono num" style={{ fontSize: 11, color: 'var(--ink-3)' }}>
            {ready} / {total} ready
          </div>
          {allQueued && (
            <button className="btn btn-primary btn-sm">Start processing</button>
          )}
        </div>
      )}

      <Composer files={files} onSend={onSend} value={value} onChange={onChange} />
    </div>
  );
}

// —— transcript ————————————————————————————————————————————

function Transcript({ files, onSend, value, onChange }) {
  return (
    <div style={{
      flex: 1, minHeight: 0, overflow: 'auto',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        maxWidth: 820, width: '100%', margin: '0 auto',
        padding: '32px 40px 24px',
        flex: 1,
      }}>
        {/* turn 1 — user */}
        <div style={{ marginBottom: 6, display: 'flex', justifyContent: 'flex-end' }}>
          <div className="mono caps" style={{color:'var(--ink-4)'}}>You · 2:14 PM</div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 28 }}>
          <div className="bubble bubble-user">
            <p style={{margin: 0, fontFamily: 'var(--f-serif)', fontSize: 15.5, color: 'var(--ink)'}}>
              {SAMPLE_TURNS[0].text}
            </p>
          </div>
        </div>

        {/* turn 2 — assistant */}
        <div style={{ marginBottom: 10, display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{
            fontFamily: 'var(--f-serif)', fontStyle: 'italic',
            fontSize: 13, color: 'var(--ink-3)',
          }}>FileChat</div>
          <div style={{ flex: 1, height: 1, background: 'var(--rule-2)' }} />
          <div className="mono caps" style={{ color: 'var(--ink-4)' }}>
            {SAMPLE_TURNS[1].intro}
          </div>
        </div>
        <div className="bubble bubble-ai" style={{maxWidth: 'none'}}>
          {SAMPLE_TURNS[1].paras.map((p, i) => {
            if (p.kind === 'h') return (
              <h3 key={i} style={{
                fontFamily: 'var(--f-serif)', fontSize: 16, fontWeight: 500,
                margin: '18px 0 8px', color: 'var(--ink)',
                display: 'flex', alignItems: 'baseline', gap: 10,
              }}>
                <span style={{color: 'var(--accent)'}}>§</span>
                {p.text}
              </h3>
            );
            if (p.kind === 'note') return (
              <div key={i} className="mono" style={{
                fontSize: 11, color: 'var(--ink-3)', marginTop: 16,
                padding: '8px 12px', background: 'var(--paper-2)',
                border: '1px dashed var(--rule)', borderRadius: 2,
              }}>
                Note · {p.text}
              </div>
            );
            return (
              <p key={i} style={{
                fontFamily: 'var(--f-serif)', fontSize: 15.5, lineHeight: 1.6,
                color: 'var(--ink-2)', margin: '0 0 10px', textWrap: 'pretty',
              }}>
                {p.text}
                {p.cites && p.cites.map(n => (
                  <sup key={n} className="cite">{n}</sup>
                ))}
              </p>
            );
          })}

          <div style={{
            marginTop: 18, display: 'flex', alignItems: 'center', gap: 10,
            flexWrap: 'wrap', paddingTop: 12, borderTop: '1px solid var(--rule-2)',
          }}>
            <span className="mono caps" style={{ color: 'var(--ink-4)' }}>Sources</span>
            {CITATIONS.map(c => (
              <div key={c.n} style={{
                display: 'inline-flex', alignItems: 'center', gap: 6,
                padding: '3px 8px 3px 4px',
                border: '1px solid var(--rule-2)', borderRadius: 2,
                fontSize: 11, color: 'var(--ink-2)',
                background: 'var(--card)',
                cursor: 'pointer',
              }}>
                <span className="mono" style={{
                  fontSize: 9, background: 'var(--accent)', color: '#fff',
                  padding: '2px 4px',
                }}>{c.n}</span>
                <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.file}
                </span>
                <span className="mono" style={{ color: 'var(--ink-4)', fontSize: 10 }}>{c.loc}</span>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 6, marginTop: 16 }}>
            <button className="btn btn-ghost btn-sm">Copy</button>
            <button className="btn btn-ghost btn-sm">Continue</button>
            <button className="btn btn-ghost btn-sm">Re-ground</button>
            <div style={{flex: 1}} />
            <button className="btn btn-ghost btn-sm" style={{color: 'var(--ink-4)'}}>Wasn't useful</button>
          </div>
        </div>
      </div>

      {/* docked composer */}
      <div style={{
        position: 'sticky', bottom: 0,
        borderTop: '1px solid var(--rule)',
        background: 'linear-gradient(to bottom, transparent, var(--paper) 24%)',
        padding: '28px 40px 20px',
      }}>
        <div style={{maxWidth: 820, margin: '0 auto'}}>
          <Composer files={files} onSend={onSend} value={value} onChange={onChange} />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, {
  Composer, FileChip, EmptyState, ProcessingPreamble, Transcript,
});
