import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { defineCatalog } from "@json-render/core";
import { JSONUIProvider, Renderer, defineRegistry } from "@json-render/react";
import type { Spec } from "@json-render/react";
import { schema } from "@json-render/react/schema";
import mermaid from "mermaid";
import { z } from "zod";
import { api } from "./api";
import type { Artifact, Citation, JsonRenderSpec } from "./types";

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

function ChartArtifact({ artifact, citations, onCitationClick }: { artifact: Artifact; citations: Citation[]; onCitationClick: (citation: Citation) => void }) {
  const spec = normalizeChartSpec(artifact.spec);
  if (!spec.values.length) {
    return <div className="artifact-render-error">This chart could not be rendered because it has no valid data points.</div>;
  }
  const max = Math.max(...spec.values.map((item) => Math.abs(item.value)), 1);
  return (
    <div className={`native-chart chart-${spec.chart_type}`}>
      <div className="chart-plot" role="img" aria-label={artifact.title}>
        {spec.values.map((item, index) => {
          const chunkId = item.source_chunk_id ?? (typeof item.source_id === "number" ? artifact.source_chunk_ids[item.source_id - 1] : undefined);
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

function FileDraftArtifact({ artifact }: { artifact: Artifact }) {
  const spec = normalizeDraftSpec(artifact.spec);
  return (
    <div className="file-draft-preview">
      <div className="draft-actions">
        <span className="mono">{spec.filename}</span>
        <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "md")}>Markdown</a>
        <a className="artifact-inline-action" href={api.exportArtifactUrl(artifact.session_id, artifact.id, "json")}>JSON</a>
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
  };
}

function normalizeDraftSpec(value: Record<string, unknown>) {
  const filename = typeof value.filename === "string" && value.filename.trim() ? value.filename : "draft.md";
  const content = typeof value.content === "string" ? value.content : JSON.stringify(value.content ?? value, null, 2);
  return {
    filename,
    preview: content.length > 2400 ? `${content.slice(0, 2400)}\n...` : content,
  };
}
