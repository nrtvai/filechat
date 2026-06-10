import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KeyRound, Loader2, Settings as SettingsIcon, ShieldCheck, X } from "lucide-react";
import { api } from "../api";
import type { ContextProfile, ModelInfo, Settings } from "../types";
import { formatModelPrice } from "./shared";

export function SettingsTab({
  settings,
  contextProfile,
  updateSettings,
  updateContextProfile,
  clearOpenRouterKey,
  canManageProviderKeys,
  heading,
  lockedReason
}: {
  settings: Settings | null;
  contextProfile: ContextProfile;
  updateSettings: (patch: Record<string, unknown>) => Promise<void>;
  updateContextProfile: (patch: Partial<ContextProfile>) => Promise<void>;
  clearOpenRouterKey: () => Promise<void>;
  canManageProviderKeys: boolean;
  heading: string;
  lockedReason?: string;
}) {
  const [apiKey, setApiKey] = useState("");
  const [saving, setSaving] = useState(false);
  const [chatModels, setChatModels] = useState<ModelInfo[]>([]);
  const [embeddingModels, setEmbeddingModels] = useState<ModelInfo[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState<string | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [clearingKey, setClearingKey] = useState(false);
  const chooserRef = useRef<HTMLDivElement>(null);

  const loadModels = useCallback(async (force = false) => {
    if (!canManageProviderKeys) return;
    if (!force && !settings?.openrouter_key_configured) return;
    setLoadingModels(true);
    setModelError(null);
    try {
      const [nextChatModels, nextEmbeddingModels] = await Promise.all([api.models("chat"), api.models("embedding")]);
      setChatModels(nextChatModels);
      setEmbeddingModels(nextEmbeddingModels);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "Could not load OpenRouter models");
    } finally {
      setLoadingModels(false);
    }
  }, [canManageProviderKeys, settings?.openrouter_key_configured]);

  useEffect(() => {
    void loadModels();
  }, [loadModels]);

  const saveKey = async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      await updateSettings({ openrouter_api_key: apiKey.trim() });
      await api.verifyOpenRouter();
      await updateSettings({});
      setApiKey("");
      await loadModels(true);
      window.setTimeout(() => {
        if (typeof chooserRef.current?.scrollIntoView === "function") {
          chooserRef.current.scrollIntoView({ block: "start", behavior: "smooth" });
        }
      }, 50);
    } finally {
      setSaving(false);
    }
  };
  const verifyKey = async () => {
    setVerifying(true);
    setModelError(null);
    try {
      await api.verifyOpenRouter();
      await updateSettings({});
      await loadModels(true);
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "OpenRouter verification failed");
    } finally {
      setVerifying(false);
    }
  };
  const clearKey = async () => {
    setClearingKey(true);
    setModelError(null);
    try {
      await clearOpenRouterKey();
      setApiKey("");
    } catch (err) {
      setModelError(err instanceof Error ? err.message : "Could not clear OpenRouter key");
    } finally {
      setClearingKey(false);
    }
  };
  const providerStatus = settings?.openrouter_provider_status ?? "missing";
  const providerReady = providerStatus === "verified";
  const canClearSavedKey = Boolean(settings?.openrouter_key_configured && settings.openrouter_key_source === "local");
  return (
    <div className="panel-body settings-panel">
      <div className="panel-kicker mono caps">{heading}</div>
      <div className="settings-status">
        <KeyRound size={16} />
        <div>
          <strong>{providerReady ? "OpenRouter verified" : settings?.openrouter_key_configured ? `OpenRouter ${providerStatus}` : "OpenRouter key missing"}</strong>
          <small>{lockedReason ?? (providerReady ? "Model-backed runs can start." : settings?.openrouter_key_configured ? "Verify the key before running model-backed workflows." : "Add a key before indexing or asking questions.")}</small>
          <small>Source: {settings?.openrouter_key_source ?? "loading"}</small>
          <small>Scope: {settings?.settings_scope ?? "single_user"}</small>
          {settings?.openrouter_provider_message && <small>{settings.openrouter_provider_message}</small>}
        </div>
      </div>
      <div className="enterprise-config-note">
        <ShieldCheck size={16} />
        <div>
          <strong>{settings?.edition === "enterprise" ? "Enterprise configuration active" : "Community configuration active"}</strong>
          <small>
            {settings?.edition === "enterprise"
              ? "Enterprise role gates are enabled from environment configuration."
              : "Enable local enterprise testing with FILECHAT_EDITION=enterprise and FILECHAT_AUTH_TEST_MODE=true."}
          </small>
          <small>Use FILECHAT_TRUSTED_AUTH_HEADERS=true only behind a trusted auth adapter that strips inbound role headers.</small>
        </div>
      </div>
      {canManageProviderKeys ? (
        <>
          <label>API key<input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="sk-or-..." /></label>
          <button className="primary-action" onClick={saveKey} disabled={saving || !apiKey.trim()}><KeyRound size={15} /> Save key</button>
          {settings?.openrouter_key_configured && <button className="secondary-action" onClick={verifyKey} disabled={verifying}>{verifying ? <Loader2 size={15} className="spin" /> : <KeyRound size={15} />} Verify key</button>}
          {canClearSavedKey && <button className="secondary-action" onClick={clearKey} disabled={clearingKey}>{clearingKey ? <Loader2 size={15} className="spin" /> : <X size={15} />} Clear saved key</button>}
          {settings?.openrouter_key_source === "env" && <small>Environment keys are managed outside FileChat.</small>}
          <div ref={chooserRef} className="model-chooser">
            <div className="settings-status"><SettingsIcon size={16} /><div><strong>OpenRouter models</strong><small>{loadingModels ? "Loading live model metadata..." : "Choose model profiles for orchestration, analysis, writing, repair, and embeddings."}</small>{modelError && <small className="settings-error">{modelError}</small>}</div></div>
            <label>Routing mode
              <select value={settings?.model_routing_mode ?? "auto"} onChange={(event) => void updateSettings({ model_routing_mode: event.target.value })}>
                <option value="auto">Auto</option>
                <option value="balanced">Balanced</option>
                <option value="deep">Deep</option>
                <option value="manual">Manual</option>
              </select>
            </label>
            <label>Reasoning effort
              <select value={settings?.reasoning_effort ?? "medium"} onChange={(event) => void updateSettings({ reasoning_effort: event.target.value })}>
                <option value="none">None</option>
                <option value="minimal">Minimal</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="xhigh">X-high</option>
              </select>
            </label>
            <ModelSelector kind="chat" label="Chat model" value={settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(chat_model) => updateSettings({ chat_model })} />
            <ModelSelector kind="chat" label="Orchestrator model" value={settings?.orchestrator_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(orchestrator_model) => updateSettings({ orchestrator_model })} />
            <ModelSelector kind="chat" label="Analysis model" value={settings?.analysis_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(analysis_model) => updateSettings({ analysis_model })} />
            <ModelSelector kind="chat" label="Writing model" value={settings?.writing_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(writing_model) => updateSettings({ writing_model })} />
            <ModelSelector kind="chat" label="Repair model" value={settings?.repair_model ?? settings?.chat_model ?? ""} models={chatModels} loading={loadingModels} onSelect={(repair_model) => updateSettings({ repair_model })} />
            <ModelSelector kind="embedding" label="Embedding model" value={settings?.embedding_model ?? ""} models={embeddingModels} loading={loadingModels} onSelect={(embedding_model) => updateSettings({ embedding_model })} />
          </div>
        </>
      ) : (
        <div className="settings-status locked">
          <ShieldCheck size={16} />
          <div>
            <strong>Managed by admins</strong>
            <small>{settings?.chat_model ?? "No chat model selected"}</small>
            <small>{settings?.embedding_model ?? "No embedding model selected"}</small>
          </div>
        </div>
      )}
      <section className="preferences-panel">
        <div className="settings-status">
          <SettingsIcon size={16} />
          <div>
            <strong>Preferences</strong>
            <small>These defaults shape prompt context, artifact display, citations, and draft style.</small>
          </div>
        </div>
        <label>Artifacts in chat
          <select value={contextProfile.artifact_policy} onChange={(event) => void updateContextProfile({ artifact_policy: event.target.value as ContextProfile["artifact_policy"] })}>
            <option value="chart+draft">Chart + draft</option>
            <option value="all">All artifacts</option>
            <option value="ask_each_run">Ask each run</option>
          </select>
        </label>
        <label>Citations
          <select value={contextProfile.citation_display} onChange={(event) => void updateContextProfile({ citation_display: event.target.value as ContextProfile["citation_display"] })}>
            <option value="minimized">Minimized</option>
            <option value="full">Full</option>
          </select>
        </label>
        <label>Drafting
          <select value={contextProfile.drafting_policy} onChange={(event) => void updateContextProfile({ drafting_policy: event.target.value as ContextProfile["drafting_policy"] })}>
            <option value="model_polished_evidence">Model-polished evidence</option>
            <option value="deterministic_template">Deterministic template</option>
            <option value="ask_user_style">Ask user style</option>
          </select>
        </label>
        <label>Titles
          <select value={contextProfile.title_style} onChange={(event) => void updateContextProfile({ title_style: event.target.value as ContextProfile["title_style"] })}>
            <option value="localized_subject_first">Localized subject-first</option>
            <option value="generic">Generic</option>
          </select>
        </label>
      </section>
      {canManageProviderKeys && (
        <>
          <label>OCR model<input defaultValue={settings?.ocr_model ?? ""} onBlur={(event) => updateSettings({ ocr_model: event.target.value })} /></label>
          <label>Retrieval depth<input type="number" min={1} max={24} defaultValue={settings?.retrieval_depth ?? 8} onBlur={(event) => updateSettings({ retrieval_depth: Number(event.target.value) })} /></label>
          <label className="model-check">
            <input type="checkbox" checked={Boolean(settings?.high_cost_confirmation)} onChange={(event) => void updateSettings({ high_cost_confirmation: event.target.checked })} />
            Confirm high-cost or deep runs
          </label>
          <label className="model-check">
            <input type="checkbox" checked={Boolean(settings?.web_search_enabled)} onChange={(event) => void updateSettings({ web_search_enabled: event.target.checked })} />
            Optional web search
          </label>
          <label>Search engine
            <select value={settings?.web_search_engine ?? "auto"} onChange={(event) => void updateSettings({ web_search_engine: event.target.value })}>
              <option value="auto">Auto</option>
              <option value="native">Native</option>
              <option value="exa">Exa</option>
              <option value="parallel">Parallel</option>
              <option value="firecrawl">Firecrawl</option>
            </select>
          </label>
        </>
      )}
      <div className="settings-status"><SettingsIcon size={16} /><div><strong>Strict grounding</strong><small>Answers refuse when the sources do not support them.</small></div></div>
    </div>
  );
}

function ModelSelector(props: {
  kind: "chat" | "embedding";
  label: string;
  value: string;
  models: ModelInfo[];
  loading: boolean;
  onSelect: (modelId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const [priceFilter, setPriceFilter] = useState<"all" | "free" | "paid">("all");
  const [minContext, setMinContext] = useState("");
  const [structuredOnly, setStructuredOnly] = useState(false);
  const [reasoningOnly, setReasoningOnly] = useState(false);
  const [sort, setSort] = useState<"name" | "newest" | "context" | "input" | "output">("name");

  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const minContextValue = Number(minContext) || 0;
    return props.models
      .filter((model) => {
        const haystack = `${model.id} ${model.name}`.toLowerCase();
        const isFree = (model.pricing.prompt ?? 0) === 0 && (model.pricing.completion ?? 0) === 0;
        const supportsStructuredOutput = model.supported_parameters.includes("response_format") || model.supported_parameters.includes("structured_outputs");
        const supportsReasoning = model.supported_parameters.includes("reasoning") || model.supported_parameters.includes("include_reasoning");
        if (normalizedQuery && !haystack.includes(normalizedQuery)) return false;
        if (priceFilter === "free" && !isFree) return false;
        if (priceFilter === "paid" && isFree) return false;
        if (minContextValue && (model.context_length ?? 0) < minContextValue) return false;
        if (structuredOnly && !supportsStructuredOutput) return false;
        if (reasoningOnly && !supportsReasoning) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "newest") return (b.created ?? 0) - (a.created ?? 0);
        if (sort === "context") return (b.context_length ?? 0) - (a.context_length ?? 0);
        if (sort === "input") return (a.pricing.prompt ?? 0) - (b.pricing.prompt ?? 0);
        if (sort === "output") return (a.pricing.completion ?? 0) - (b.pricing.completion ?? 0);
        return a.name.localeCompare(b.name);
      });
  }, [minContext, priceFilter, props.models, query, reasoningOnly, sort, structuredOnly]);

  const selected = props.models.find((model) => model.id === props.value);

  return (
    <section className="model-selector">
      <label>{props.label}
        <select value={props.value} disabled={props.loading || filtered.length === 0} onChange={(event) => void props.onSelect(event.target.value)}>
          {props.value && !filtered.some((model) => model.id === props.value) && <option value={props.value}>{selected?.name ?? props.value}</option>}
          {filtered.map((model) => (
            <option key={model.id} value={model.id}>{model.name || model.id}</option>
          ))}
        </select>
      </label>
      <div className="model-meta mono">
        <span>{props.value || "No model selected"}</span>
        {selected && <span>{selected.context_length?.toLocaleString() ?? "unknown"} ctx · in {formatModelPrice(selected.pricing.prompt)} · out {formatModelPrice(selected.pricing.completion)}</span>}
      </div>
      <div className="model-controls">
        <label>Search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="provider, id, name" /></label>
        <label>Price
          <select value={priceFilter} onChange={(event) => setPriceFilter(event.target.value as "all" | "free" | "paid")}>
            <option value="all">All</option>
            <option value="free">Free</option>
            <option value="paid">Paid</option>
          </select>
        </label>
        <label>Min context<input type="number" min={0} value={minContext} onChange={(event) => setMinContext(event.target.value)} placeholder="0" /></label>
        <label>Sort
          <select value={sort} onChange={(event) => setSort(event.target.value as "name" | "newest" | "context" | "input" | "output")}>
            <option value="name">Name</option>
            <option value="newest">Newest</option>
            <option value="context">Context</option>
            <option value="input">Input price</option>
            <option value="output">Output price</option>
          </select>
        </label>
      </div>
      {props.kind === "chat" && (
        <div className="model-check-row">
          <label className="model-check">
            <input type="checkbox" checked={structuredOnly} onChange={(event) => setStructuredOnly(event.target.checked)} />
            Structured output
          </label>
          <label className="model-check">
            <input type="checkbox" checked={reasoningOnly} onChange={(event) => setReasoningOnly(event.target.checked)} />
            Reasoning
          </label>
        </div>
      )}
      <div className="model-count mono">{props.loading ? "Loading models..." : `${filtered.length} of ${props.models.length} models`}</div>
    </section>
  );
}
