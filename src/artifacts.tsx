import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { defineCatalog } from "@json-render/core";
import { JSONUIProvider, Renderer, defineRegistry } from "@json-render/react";
import type { Spec } from "@json-render/react";
import { schema } from "@json-render/react/schema";
import mermaid from "mermaid";
import { z } from "zod";
import { api } from "./api";
import type { Artifact, Citation, InsightNarrative, JsonRenderSpec } from "./types";

type ArtifactAction = {
  type: "copy" | "source" | "select" | "noop";
  payload?: Record<string, unknown>;
};

type ArtifactRendererProps = {
  artifact: Artifact;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
  onSelectArtifact?: (artifact: Artifact) => void;
};
type ChartPoint = { label: string; value: number; source_id?: number; source_chunk_id?: string };
type TimelineItem = { label: string; date?: string; description?: string; status?: string; sourceChunkId?: string };
type ValueItem = { label: string; value: number; actual?: number; target?: number; x?: number; y?: number };
type HeatmapRow = { label: string; cells: { column: string; value: number }[] };

const textAlign = z.enum(["left", "center", "right"]).optional();

const catalog = defineCatalog(schema, {
  components: {
    ArtifactCard: {
      props: z.object({
        title: z.string().optional(),
        caption: z.string().optional(),
      }),
      description: "A contained artifact surface with an optional title and caption.",
    },
    Stack: {
      props: z.object({
        gap: z.enum(["xs", "sm", "md", "lg"]).optional(),
        direction: z.enum(["vertical", "horizontal"]).optional(),
      }),
      description: "A layout stack for grouping artifact content.",
    },
    TextBlock: {
      props: z.object({
        text: z.string(),
        tone: z.enum(["body", "muted", "strong"]).optional(),
        align: textAlign,
      }),
      description: "A paragraph or compact text block.",
    },
    Metric: {
      props: z.object({
        label: z.string(),
        value: z.string(),
        delta: z.string().optional(),
      }),
      description: "A labeled metric value.",
    },
    DataTable: {
      props: z.object({
        columns: z.array(z.string()),
        rows: z.array(z.array(z.string())),
      }),
      description: "A compact table with string columns and rows.",
    },
    Quote: {
      props: z.object({
        text: z.string(),
        source: z.string().optional(),
      }),
      description: "A sourced quotation or excerpt.",
    },
    Badge: {
      props: z.object({
        label: z.string(),
        tone: z.enum(["neutral", "accent", "success", "warning"]).optional(),
      }),
      description: "A small status badge.",
    },
    Divider: {
      props: z.object({}),
      description: "A visual divider.",
    },
    SourceButton: {
      props: z.object({
        label: z.string(),
        chunkId: z.string(),
      }),
      description: "A button that opens a cited source chunk.",
    },
    ActionButton: {
      props: z.object({
        label: z.string(),
        action: z.enum(["copy", "select", "noop"]).optional(),
        value: z.string().optional(),
      }),
      description: "A safe local artifact action.",
    },
    MiniChart: {
      props: z.object({
        title: z.string().optional(),
        values: z.array(z.object({ label: z.string(), value: z.number() })).min(1),
      }),
      description: "A tiny bar chart for grounded numeric comparisons.",
    },
    Timeline: {
      props: z.object({
        items: z.array(z.object({
          label: z.string(),
          date: z.string().optional(),
          description: z.string().optional(),
          status: z.string().optional(),
          sourceChunkId: z.string().optional(),
        })).min(1),
      }),
      description: "A cited roadmap or milestone timeline.",
    },
    WaterfallChart: {
      props: z.object({ values: z.array(z.object({ label: z.string(), value: z.number() })).min(1), label: z.string().optional() }),
      description: "A contribution bridge for positive and negative deltas.",
    },
    HeatmapMatrix: {
      props: z.object({
        rows: z.array(z.object({ label: z.string(), cells: z.array(z.object({ column: z.string(), value: z.number() })) })).min(1),
        rowLabel: z.string().optional(),
        columnLabel: z.string().optional(),
        valueLabel: z.string().optional(),
      }),
      description: "A two-dimensional matrix heatmap.",
    },
    ProgressBars: {
      props: z.object({
        values: z.array(z.object({ label: z.string(), actual: z.number(), target: z.number(), value: z.number().optional() })).min(1),
        label: z.string().optional(),
      }),
      description: "Progress against target rows.",
    },
    FunnelChart: {
      props: z.object({ values: z.array(z.object({ label: z.string(), value: z.number() })).min(1), label: z.string().optional() }),
      description: "A sequential drop-off chart.",
    },
    TreemapChart: {
      props: z.object({ values: z.array(z.object({ label: z.string(), value: z.number() })).min(1), label: z.string().optional() }),
      description: "A compact part-to-whole area chart.",
    },
    MekkoChart: {
      props: z.object({ values: z.array(z.object({ label: z.string(), value: z.number() })).min(1), label: z.string().optional() }),
      description: "A simple mix chart for contribution shares.",
    },
    BubbleChart: {
      props: z.object({
        values: z.array(z.object({ label: z.string(), x: z.number(), y: z.number(), value: z.number().optional() })).min(1),
        xLabel: z.string().optional(),
        yLabel: z.string().optional(),
      }),
      description: "A portfolio plot across two numeric measures.",
    },
  },
  actions: {},
});

const { registry } = defineRegistry(catalog, {
  components: {
    ArtifactCard: ({ props, children }) => (
      <section className="json-artifact-card">
        {props.title && <h4>{props.title}</h4>}
        {props.caption && <p className="json-artifact-caption">{props.caption}</p>}
        {children}
      </section>
    ),
    Stack: ({ props, children }) => (
      <div className={`json-stack ${props.direction === "horizontal" ? "horizontal" : "vertical"} gap-${props.gap ?? "md"}`}>
        {children}
      </div>
    ),
    TextBlock: ({ props }) => (
      <p className={`json-text ${props.tone ?? "body"} align-${props.align ?? "left"}`}>{props.text}</p>
    ),
    Metric: ({ props }) => (
      <div className="json-metric">
        <span>{props.label}</span>
        <strong>{props.value}</strong>
        {props.delta && <small>{props.delta}</small>}
      </div>
    ),
    DataTable: ({ props }) => (
      <div className="json-table-wrap">
        <table className="json-table">
          <thead><tr>{props.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>
            {props.rows.map((row, rowIndex) => (
              <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={`${rowIndex}-${cellIndex}`}>{cell}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
    Quote: ({ props }) => (
      <blockquote className="json-quote">
        <p>{props.text}</p>
        {props.source && <cite>{props.source}</cite>}
      </blockquote>
    ),
    Badge: ({ props }) => <span className={`json-badge ${props.tone ?? "neutral"}`}>{props.label}</span>,
    Divider: () => <hr className="json-divider" />,
    SourceButton: ({ props }) => {
      const action = useArtifactAction();
      return (
        <button className="artifact-inline-action" type="button" onClick={() => action({ type: "source", payload: { chunkId: props.chunkId } })}>
          {props.label}
        </button>
      );
    },
    ActionButton: ({ props }) => {
      const action = useArtifactAction();
      return (
        <button className="artifact-inline-action" type="button" onClick={() => action({ type: props.action ?? "noop", payload: { value: props.value } })}>
          {props.label}
        </button>
      );
    },
    MiniChart: ({ props }) => {
      const values = Array.isArray(props.values) ? props.values : [];
      const max = Math.max(...values.map((item) => item.value), 1);
      if (!values.length) return <div className="artifact-render-error">Chart data was not available.</div>;
      return (
        <div className="json-mini-chart">
          {props.title && <strong>{props.title}</strong>}
          {values.map((item) => (
            <div className="json-mini-row" key={item.label}>
              <span>{item.label}</span>
              <div><i style={{ width: `${Math.max((item.value / max) * 100, 3)}%` }} /></div>
              <em>{item.value.toLocaleString()}</em>
            </div>
          ))}
        </div>
      );
    },
    Timeline: ({ props }) => {
      const action = useArtifactAction();
      const items = props.items as TimelineItem[];
      return (
        <ol className="json-timeline">
          {items.map((item, index) => (
            <li key={`${item.label}-${index}`}>
              <div>
                {item.date && <span className="mono">{item.date}</span>}
                <strong>{item.label}</strong>
                {item.status && <em>{item.status}</em>}
              </div>
              {item.description && <p>{item.description}</p>}
              {item.sourceChunkId && (
                <button className="artifact-inline-action" type="button" onClick={() => action({ type: "source", payload: { chunkId: item.sourceChunkId } })}>
                  Open source
                </button>
              )}
            </li>
          ))}
        </ol>
      );
    },
    WaterfallChart: ({ props }) => <ValueBars className="json-waterfall" values={props.values as ValueItem[]} signed />,
    FunnelChart: ({ props }) => <ValueBars className="json-funnel" values={props.values as ValueItem[]} funnel />,
    TreemapChart: ({ props }) => <Treemap values={props.values as ValueItem[]} />,
    MekkoChart: ({ props }) => <Mekko values={props.values as ValueItem[]} />,
    ProgressBars: ({ props }) => <ProgressBars values={props.values as ValueItem[]} />,
    HeatmapMatrix: ({ props }) => <Heatmap rows={props.rows as HeatmapRow[]} />,
    BubbleChart: ({ props }) => <BubblePlot values={props.values as ValueItem[]} xLabel={props.xLabel} yLabel={props.yLabel} />,
  },
});

const ArtifactActionContext = createContext<((action: ArtifactAction) => void) | null>(null);

function useArtifactAction() {
  return useContext(ArtifactActionContext) ?? (() => undefined);
}

export function ArtifactRenderer({ artifact, citations, onCitationClick, onSelectArtifact }: ArtifactRendererProps) {
  const handleAction = (action: ArtifactAction) => {
    if (action.type === "source") {
      const chunkId = String(action.payload?.chunkId ?? "");
      const citation = citations.find((item) => item.chunk_id === chunkId);
      if (citation) onCitationClick(citation);
      return;
    }
    if (action.type === "copy") {
      const value = String(action.payload?.value ?? JSON.stringify(artifact.spec, null, 2));
      void window.navigator.clipboard?.writeText(value);
      return;
    }
    if (action.type === "select") {
      onSelectArtifact?.(artifact);
    }
  };

  return (
    <ArtifactActionContext.Provider value={handleAction}>
      <section className={`artifact-shell artifact-${artifact.kind}`}>
        <div className="artifact-header">
          <div>
            <span className="mono caps">{artifact.kind.replace("_", " ")}</span>
            <h3>{artifact.title}</h3>
            {artifact.caption && <p>{artifact.caption}</p>}
          </div>
          <button className="artifact-inline-action" type="button" onClick={() => handleAction({ type: "select" })}>Inspect</button>
        </div>
        <ArtifactExports artifact={artifact} />
        {artifact.kind === "chart" ? (
          <ChartArtifact artifact={artifact} citations={citations} onCitationClick={onCitationClick} />
        ) : artifact.kind === "file_draft" ? (
          <FileDraftArtifact artifact={artifact} />
        ) : artifact.kind === "mermaid" ? (
          <MermaidArtifact artifact={artifact} />
        ) : (
          <JsonArtifact artifact={artifact} />
        )}
        {artifact.source_chunk_ids.length > 0 && (
          <div className="artifact-sources">
            {artifact.source_chunk_ids.map((chunkId) => {
              const citation = citations.find((item) => item.chunk_id === chunkId);
              return (
                <button key={chunkId} type="button" onClick={() => citation && onCitationClick(citation)} disabled={!citation}>
                  {citation ? `Source ${citation.ordinal}` : "Source"}
                </button>
              );
            })}
          </div>
        )}
      </section>
    </ArtifactActionContext.Provider>
  );
}

function ArtifactExports({ artifact }: { artifact: Artifact }) {
  const hasData = artifactHasTabularData(artifact);
  const hasOpenDesign = artifact.kind === "file_draft" && typeof artifact.spec.open_design === "object" && artifact.spec.open_design !== null;
  return (
    <div className="artifact-export-actions">
      <button className="artifact-inline-action" type="button" onClick={() => void window.navigator.clipboard?.writeText(insightTextForArtifact(artifact))}>Copy</button>
      <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "md")}>Markdown</a>
      <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "pdf")}>PDF</a>
      <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "json")}>JSON</a>
      <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "notion")}>Notion</a>
      {hasData && <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "csv")}>CSV</a>}
      {hasOpenDesign && <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "od")}>Open Design ZIP</a>}
    </div>
  );
}

function ValueBars({ values, className, signed = false, funnel = false }: { values: ValueItem[]; className: string; signed?: boolean; funnel?: boolean }) {
  const max = Math.max(...values.map((item) => Math.abs(item.value)), 1);
  return (
    <div className={`json-value-bars ${className}`}>
      {values.map((item, index) => {
        const width = Math.max((Math.abs(item.value) / max) * 100, 3);
        return (
          <div className="json-value-row" key={`${item.label}-${index}`}>
            <span>{item.label}</span>
            <div><i className={signed && item.value < 0 ? "negative" : ""} style={{ width: funnel ? `${Math.max(100 - index * 10, width)}%` : `${width}%` }} /></div>
            <em>{item.value.toLocaleString()}</em>
          </div>
        );
      })}
    </div>
  );
}

function ProgressBars({ values }: { values: ValueItem[] }) {
  return (
    <div className="json-progress-bars">
      {values.map((item, index) => {
        const actual = Number(item.actual ?? 0);
        const target = Math.max(Number(item.target ?? 0), 1);
        const pct = Math.max(0, Math.min((actual / target) * 100, 140));
        return (
          <div className="json-progress-row" key={`${item.label}-${index}`}>
            <div><span>{item.label}</span><em>{actual.toLocaleString()} / {target.toLocaleString()}</em></div>
            <i><b style={{ width: `${Math.min(pct, 100)}%` }} /></i>
          </div>
        );
      })}
    </div>
  );
}

function Heatmap({ rows }: { rows: HeatmapRow[] }) {
  const values = rows.flatMap((row) => row.cells.map((cell) => cell.value));
  const max = Math.max(...values, 1);
  return (
    <div className="json-heatmap">
      {rows.map((row) => (
        <div className="json-heatmap-row" key={row.label}>
          <span>{row.label}</span>
          {row.cells.map((cell) => (
            <em key={`${row.label}-${cell.column}`} style={{ opacity: 0.22 + Math.min(cell.value / max, 1) * 0.78 }}>
              <small>{cell.column}</small>
              {cell.value.toLocaleString()}
            </em>
          ))}
        </div>
      ))}
    </div>
  );
}

function Treemap({ values }: { values: ValueItem[] }) {
  const total = values.reduce((sum, item) => sum + Math.max(item.value, 0), 0) || 1;
  return (
    <div className="json-treemap">
      {values.slice(0, 10).map((item) => (
        <div key={item.label} style={{ flexBasis: `${Math.max((Math.max(item.value, 0) / total) * 100, 10)}%` }}>
          <span>{item.label}</span>
          <strong>{item.value.toLocaleString()}</strong>
        </div>
      ))}
    </div>
  );
}

function Mekko({ values }: { values: ValueItem[] }) {
  const total = values.reduce((sum, item) => sum + Math.max(item.value, 0), 0) || 1;
  return (
    <div className="json-mekko">
      {values.slice(0, 12).map((item, index) => (
        <div key={item.label} style={{ width: `${Math.max((Math.max(item.value, 0) / total) * 100, 4)}%` }} className={`slice-${index % 8}`}>
          <span>{item.label}</span>
          <em>{((Math.max(item.value, 0) / total) * 100).toFixed(1)}%</em>
        </div>
      ))}
    </div>
  );
}

function BubblePlot({ values, xLabel, yLabel }: { values: ValueItem[]; xLabel?: string; yLabel?: string }) {
  const xs = values.map((item) => Number(item.x ?? 0));
  const ys = values.map((item) => Number(item.y ?? 0));
  const minX = Math.min(...xs, 0);
  const maxX = Math.max(...xs, 1);
  const minY = Math.min(...ys, 0);
  const maxY = Math.max(...ys, 1);
  const xFor = (value: number) => ((value - minX) / (maxX - minX || 1)) * 88 + 6;
  const yFor = (value: number) => 94 - ((value - minY) / (maxY - minY || 1)) * 88;
  return (
    <div className="json-bubble-wrap">
      <svg className="json-bubble" viewBox="0 0 100 100" role="img" aria-label={`${xLabel ?? "X"} by ${yLabel ?? "Y"}`}>
        <line x1="6" y1="94" x2="96" y2="94" />
        <line x1="6" y1="4" x2="6" y2="94" />
        {values.slice(0, 24).map((item, index) => (
          <g key={`${item.label}-${index}`}>
            <circle cx={xFor(Number(item.x ?? 0))} cy={yFor(Number(item.y ?? 0))} r="3.5" />
            {index < 6 && <text x={xFor(Number(item.x ?? 0)) + 2} y={yFor(Number(item.y ?? 0)) - 2}>{item.label}</text>}
          </g>
        ))}
      </svg>
      <div className="chart-axis-labels mono"><span>{xLabel ?? "X"}</span><span>{yLabel ?? "Y"}</span></div>
    </div>
  );
}

function ChartArtifact({ artifact, citations, onCitationClick }: { artifact: Artifact; citations: Citation[]; onCitationClick: (citation: Citation) => void }) {
  const spec = normalizeChartSpec(artifact.spec);
  if (!spec.values.length) {
    return <div className="artifact-render-error">This chart could not be rendered because it has no valid data points.</div>;
  }
  let chart: ReactNode;
  if (spec.chart_type === "line") {
    chart = <LineChartArtifact artifact={artifact} spec={spec} citations={citations} onCitationClick={onCitationClick} />;
  } else if (spec.chart_type === "pie") {
    chart = <PieChartArtifact artifact={artifact} spec={spec} citations={citations} onCitationClick={onCitationClick} />;
  } else {
    chart = <BarChartArtifact artifact={artifact} spec={spec} citations={citations} onCitationClick={onCitationClick} />;
  }
  return (
    <>
      {chart}
      {spec.insight_narrative && <InsightNarrativePanel narrative={spec.insight_narrative} />}
    </>
  );
}

function InsightNarrativePanel({ narrative }: { narrative: InsightNarrative }) {
  return (
    <section className="insight-panel" aria-label="Insight narrative">
      <div className="insight-panel-head">
        <span className="mono caps">Reviewed insight</span>
        <strong>{narrative.headline}</strong>
      </div>
      <div className="insight-panel-grid">
        <InsightBlock title="Meaning" value={narrative.meaning} />
        <InsightBlock title="So what" value={narrative.so_what} />
      </div>
      <InsightList title="Evidence" values={narrative.evidence} />
      <InsightList title="Recommended actions" values={narrative.recommended_actions} />
      <InsightQuestionList questions={narrative.follow_up_questions} />
      <InsightList title="Caveats" values={narrative.caveats} />
      <div className="insight-meta mono">Confidence: {narrative.confidence}</div>
    </section>
  );
}

function InsightBlock({ title, value }: { title: string; value: string }) {
  if (!value) return null;
  return (
    <div className="insight-block">
      <span className="mono caps">{title}</span>
      <p>{value}</p>
    </div>
  );
}

function InsightList({ title, values }: { title: string; values: string[] }) {
  if (!values.length) return null;
  return (
    <div className="insight-list">
      <span className="mono caps">{title}</span>
      <ul>
        {values.map((value, index) => <li key={`${title}-${index}`}>{value}</li>)}
      </ul>
    </div>
  );
}

function InsightQuestionList({ questions }: { questions: InsightNarrative["follow_up_questions"] }) {
  if (!questions.length) return null;
  return (
    <div className="insight-list">
      <span className="mono caps">Questions to answer next</span>
      <ul>
        {questions.map((question) => <li key={question.id}>{question.question}</li>)}
      </ul>
    </div>
  );
}

function BarChartArtifact({
  artifact,
  spec,
  citations,
  onCitationClick
}: {
  artifact: Artifact;
  spec: ReturnType<typeof normalizeChartSpec>;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
}) {
  const max = Math.max(...spec.values.map((item) => Math.abs(item.value)), 1);
  return (
    <div className={`native-chart chart-${spec.chart_type}`}>
      <div className="chart-plot" role="img" aria-label={artifact.title}>
        {spec.values.map((item, index) => {
          const chunkId = chartChunkId(item, artifact);
          const citation = chunkId ? citations.find((candidate) => candidate.chunk_id === chunkId) : undefined;
          return (
            <button
              className="chart-bar-row"
              key={`${item.label}-${index}`}
              type="button"
              onClick={() => citation && onCitationClick(citation)}
              disabled={!citation}
              aria-label={citation ? `Open source for ${item.label}` : item.label}
            >
              <span>{item.label}</span>
              <i><b style={{ width: `${Math.max((Math.abs(item.value) / max) * 100, 3)}%` }} /></i>
              <em>{item.value.toLocaleString()}</em>
            </button>
          );
        })}
      </div>
      <div className="chart-axis-labels mono">
        <span>{spec.x_label}</span>
        <span>{spec.y_label}</span>
      </div>
    </div>
  );
}

function LineChartArtifact({
  artifact,
  spec,
  citations,
  onCitationClick
}: {
  artifact: Artifact;
  spec: ReturnType<typeof normalizeChartSpec>;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
}) {
  const width = 640;
  const height = 260;
  const pad = { top: 18, right: 28, bottom: 42, left: 52 };
  const values = spec.values;
  const min = Math.min(...values.map((item) => item.value), 0);
  const max = Math.max(...values.map((item) => item.value), 1);
  const span = max - min || 1;
  const xFor = (index: number) => pad.left + (values.length === 1 ? 0 : (index / (values.length - 1)) * (width - pad.left - pad.right));
  const yFor = (value: number) => pad.top + ((max - value) / span) * (height - pad.top - pad.bottom);
  const points = values.map((item, index) => `${xFor(index)},${yFor(item.value)}`).join(" ");
  return (
    <div className="native-chart chart-line">
      <svg className="chart-line-svg" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={artifact.title}>
        <line className="chart-axis" x1={pad.left} y1={pad.top} x2={pad.left} y2={height - pad.bottom} />
        <line className="chart-axis" x1={pad.left} y1={height - pad.bottom} x2={width - pad.right} y2={height - pad.bottom} />
        <polyline className="chart-line-path" points={points} fill="none" />
        {values.map((item, index) => (
          <g key={`${item.label}-${index}`}>
            <circle className="chart-line-point" cx={xFor(index)} cy={yFor(item.value)} r="4.5">
              <title>{`${item.label}: ${item.value.toLocaleString()}`}</title>
            </circle>
            {(index === 0 || index === values.length - 1 || values.length <= 4) && (
              <text className="chart-svg-label" x={xFor(index)} y={height - 18} textAnchor={index === 0 ? "start" : index === values.length - 1 ? "end" : "middle"}>
                {item.label}
              </text>
            )}
          </g>
        ))}
        <text className="chart-svg-label" x={pad.left} y="12">{max.toLocaleString()}</text>
        <text className="chart-svg-label" x={pad.left} y={height - pad.bottom - 4}>{min.toLocaleString()}</text>
      </svg>
      <ChartPointList artifact={artifact} spec={spec} citations={citations} onCitationClick={onCitationClick} />
      <div className="chart-axis-labels mono">
        <span>{spec.x_label}</span>
        <span>{spec.y_label}</span>
      </div>
    </div>
  );
}

function PieChartArtifact({
  artifact,
  spec,
  citations,
  onCitationClick
}: {
  artifact: Artifact;
  spec: ReturnType<typeof normalizeChartSpec>;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
}) {
  const size = 260;
  const radius = 95;
  const center = size / 2;
  const total = spec.values.reduce((sum, item) => sum + Math.max(item.value, 0), 0);
  if (total <= 0) {
    return <div className="artifact-render-error">This pie chart could not be rendered because its values are not positive.</div>;
  }
  let angle = -90;
  const slices = spec.values.map((item, index) => {
    const sweep = (Math.max(item.value, 0) / total) * 360;
    const path = describeArc(center, center, radius, angle, angle + sweep);
    angle += sweep;
    return { item, path, className: `slice-${index % 8}` };
  });
  return (
    <div className="native-chart chart-pie">
      <div className="chart-pie-layout">
        <svg className="chart-pie-svg" viewBox={`0 0 ${size} ${size}`} role="img" aria-label={artifact.title}>
          {slices.map((slice, index) => (
            <path className={`chart-pie-slice ${slice.className}`} d={slice.path} key={`${slice.item.label}-${index}`}>
              <title>{`${slice.item.label}: ${slice.item.value.toLocaleString()}`}</title>
            </path>
          ))}
        </svg>
        <ChartPointList artifact={artifact} spec={spec} citations={citations} onCitationClick={onCitationClick} total={total} />
      </div>
      <div className="chart-axis-labels mono">
        <span>{spec.x_label}</span>
        <span>{spec.y_label}</span>
      </div>
    </div>
  );
}

function ChartPointList({
  artifact,
  spec,
  citations,
  onCitationClick,
  total
}: {
  artifact: Artifact;
  spec: ReturnType<typeof normalizeChartSpec>;
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
  total?: number;
}) {
  return (
    <div className="chart-point-list">
      {spec.values.map((item, index) => {
        const chunkId = chartChunkId(item, artifact);
        const citation = chunkId ? citations.find((candidate) => candidate.chunk_id === chunkId) : undefined;
        const percent = total ? ` · ${((item.value / total) * 100).toFixed(1)}%` : "";
        return (
          <button
            className="chart-point-button"
            key={`${item.label}-${index}`}
            type="button"
            onClick={() => citation && onCitationClick(citation)}
            disabled={!citation}
            aria-label={citation ? `Open source for ${item.label}` : item.label}
          >
            <span>{item.label}</span>
            <em>{item.value.toLocaleString()}{percent}</em>
          </button>
        );
      })}
    </div>
  );
}

function chartChunkId(item: ChartPoint, artifact: Artifact) {
  return item.source_chunk_id ?? (typeof item.source_id === "number" ? artifact.source_chunk_ids[item.source_id - 1] : undefined);
}

function artifactHasTabularData(artifact: Artifact) {
  const spec = artifact.spec;
  if (Array.isArray(spec.values) || Array.isArray(spec.rows)) return true;
  const elements = isRecord(spec.elements) ? spec.elements : {};
  return Object.values(elements).some((element) => isRecord(element) && element.type === "DataTable");
}

function insightTextForArtifact(artifact: Artifact) {
  const spec = artifact.spec;
  const narrative = normalizeInsightNarrative(spec.insight_narrative);
  if (narrative) {
    return [
      narrative.headline,
      "",
      `Meaning: ${narrative.meaning}`,
      `So what: ${narrative.so_what}`,
      "",
      "Evidence:",
      ...narrative.evidence.map((item) => `- ${item}`),
      "",
      "Recommended actions:",
      ...narrative.recommended_actions.map((item) => `- ${item}`),
      "",
      "Questions to answer next:",
      ...narrative.follow_up_questions.map((item) => `- ${item.question}`),
      "",
      "Caveats:",
      ...narrative.caveats.map((item) => `- ${item}`),
      "",
      `Confidence: ${narrative.confidence}`,
    ].join("\n").trim();
  }
  const facts = Array.isArray(spec.source_facts) ? spec.source_facts.map(String).join("\n") : "";
  if (facts) return facts;
  if (typeof spec.content === "string") return spec.content;
  return `${artifact.title}\n\n${artifact.caption}\n\n${JSON.stringify(spec, null, 2)}`;
}

function polarToCartesian(centerX: number, centerY: number, radius: number, angleInDegrees: number) {
  const angleInRadians = ((angleInDegrees - 90) * Math.PI) / 180;
  return {
    x: centerX + radius * Math.cos(angleInRadians),
    y: centerY + radius * Math.sin(angleInRadians),
  };
}

function describeArc(x: number, y: number, radius: number, startAngle: number, endAngle: number) {
  const start = polarToCartesian(x, y, radius, endAngle);
  const end = polarToCartesian(x, y, radius, startAngle);
  const largeArcFlag = endAngle - startAngle <= 180 ? "0" : "1";
  return [
    `M ${x} ${y}`,
    `L ${start.x} ${start.y}`,
    `A ${radius} ${radius} 0 ${largeArcFlag} 0 ${end.x} ${end.y}`,
    "Z",
  ].join(" ");
}

function FileDraftArtifact({ artifact }: { artifact: Artifact }) {
  const spec = normalizeDraftSpec(artifact.spec);
  return (
    <div className="file-draft-preview">
      <div className="draft-actions">
        <span className="mono">{spec.filename}</span>
      </div>
      <pre className="artifact-code"><code>{spec.preview}</code></pre>
    </div>
  );
}

function MermaidArtifact({ artifact }: { artifact: Artifact }) {
  const diagram = typeof artifact.spec.diagram === "string" ? artifact.spec.diagram : "";
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function renderDiagram() {
      setFailed(false);
      setSvg("");
      try {
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base" });
        await mermaid.parse(diagram, { suppressErrors: false });
        const result = await mermaid.render(`artifact-${artifact.id}`, diagram);
        if (!cancelled) setSvg(result.svg);
      } catch {
        if (!cancelled) setFailed(true);
      }
    }
    if (diagram) void renderDiagram();
    else setFailed(true);
    return () => { cancelled = true; };
  }, [artifact.id, diagram]);

  if (failed) {
    return <pre className="artifact-code"><code>{diagram || "Invalid Mermaid diagram"}</code></pre>;
  }
  return <div className="mermaid-frame" aria-label={artifact.title} dangerouslySetInnerHTML={{ __html: svg || "" }} />;
}

function JsonArtifact({ artifact }: { artifact: Artifact }) {
  const spec = useMemo(() => coerceJsonRenderSpec(artifact.spec), [artifact.spec]);
  if (!spec) {
    return <pre className="artifact-code"><code>{JSON.stringify(artifact.spec, null, 2)}</code></pre>;
  }
  return (
    <JSONUIProvider registry={registry} initialState={{}} handlers={{}}>
        <Renderer spec={spec} registry={registry} fallback={UnknownArtifactComponent} />
    </JSONUIProvider>
  );
}

function UnknownArtifactComponent() {
  return <div className="artifact-render-error">Unsupported artifact component.</div>;
}

function coerceJsonRenderSpec(value: Record<string, unknown>): Spec | null {
  if (typeof value.root !== "string" || !value.elements || typeof value.elements !== "object") return null;
  const elements = value.elements as JsonRenderSpec["elements"];
  if (!elements[value.root]) return null;
  return { root: value.root, elements } as Spec;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function normalizeChartSpec(value: Record<string, unknown>) {
  const rawValues = Array.isArray(value.values) ? value.values : Array.isArray(value.data) ? value.data : [];
  const values: ChartPoint[] = [];
  for (const item of rawValues) {
    if (!isRecord(item)) continue;
    const label = String(item.label ?? item.name ?? item.category ?? "").trim();
    const number = Number(item.value);
    if (!label || !Number.isFinite(number)) continue;
    values.push({
      label,
      value: number,
      source_id: typeof item.source_id === "number" ? item.source_id : undefined,
      source_chunk_id: typeof item.source_chunk_id === "string" ? item.source_chunk_id : undefined,
    });
  }
  const chartType = value.chart_type === "line" || value.chart_type === "pie" ? value.chart_type : "bar";
  return {
    chart_type: chartType,
    values,
    x_label: typeof value.x_label === "string" ? value.x_label : "Category",
    y_label: typeof value.y_label === "string" ? value.y_label : "Value",
    insight_narrative: normalizeInsightNarrative(value.insight_narrative),
  };
}

function normalizeInsightNarrative(value: unknown): InsightNarrative | null {
  if (!isRecord(value)) return null;
  const headline = stringValue(value.headline);
  const meaning = stringValue(value.meaning);
  const soWhat = stringValue(value.so_what);
  if (!headline || !meaning || !soWhat) return null;
  return {
    headline,
    meaning,
    evidence: stringArray(value.evidence),
    so_what: soWhat,
    recommended_actions: stringArray(value.recommended_actions),
    follow_up_questions: Array.isArray(value.follow_up_questions)
      ? value.follow_up_questions.flatMap((item) => {
          if (!isRecord(item)) return [];
          const id = stringValue(item.id);
          const question = stringValue(item.question);
          if (!id || !question) return [];
          return [{
            id,
            group: stringValue(item.group) === "data" ? "data" : "business",
            question,
            options: Array.isArray(item.options) ? item.options.filter(isRecord) : [],
            default_option: stringValue(item.default_option) || null,
            requires_reference: Boolean(item.requires_reference),
          }];
        })
      : [],
    caveats: stringArray(value.caveats),
    confidence: value.confidence === "low" || value.confidence === "medium" || value.confidence === "high" ? value.confidence : "medium",
    source_columns: stringArray(value.source_columns),
  };
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.map(stringValue).filter(Boolean) : [];
}

function normalizeDraftSpec(value: Record<string, unknown>) {
  const filename = typeof value.filename === "string" && value.filename.trim() ? value.filename : "draft.md";
  const content = typeof value.content === "string" ? value.content : JSON.stringify(value.content ?? value, null, 2);
  return {
    filename,
    preview: content.length > 2400 ? `${content.slice(0, 2400)}\n...` : content,
  };
}
