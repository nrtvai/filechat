// FileChat — seed data and small utilities
// Exposed as globals so Babel scripts can share.

const SESSIONS = [
  { id: 's1', title: "Annual report — variance", updated: "Today", active: true, files: 4 },
  { id: 's2', title: "Contract redlines, Q1 batch", updated: "Yesterday", files: 7 },
  { id: 's3', title: "Research — mycelium papers", updated: "Apr 14", files: 12 },
  { id: 's4', title: "Kitchen renovation quotes", updated: "Apr 11", files: 3 },
  { id: 's5', title: "Thesis — chapter 4 sources", updated: "Apr 08", files: 9 },
  { id: 's6', title: "Board deck notes", updated: "Apr 02", files: 5 },
  { id: 's7', title: "Personal tax — 2025", updated: "Mar 28", files: 11 },
];

const SEED_FILES = [
  { id: 'f1', name: "FY25 Annual Report — Final.pdf", ext: "PDF", size: "4.2 MB", pages: 62, status: "ready",     progress: 1.00, chunks: 184, note: "indexed" },
  { id: 'f2', name: "Board commentary — draft v7.docx", ext: "DOCX", size: "318 KB", pages: 14, status: "ready",   progress: 1.00, chunks: 41,  note: "indexed" },
  { id: 'f3', name: "Segment P&L by quarter.xlsx",     ext: "XLSX", size: "612 KB", pages: 8,  status: "indexing",progress: 0.72, chunks: 96,  note: "indexing · 72%" },
  { id: 'f4', name: "Auditor letter 2025-03-14.pdf",   ext: "PDF", size: "221 KB", pages: 3,  status: "reading", progress: 0.34, chunks: 0,   note: "reading · 34%" },
];

const EMPTY_FILES = [];
const SELECTED_FILES = [
  { id: 'f1', name: "FY25 Annual Report — Final.pdf", ext: "PDF", size: "4.2 MB", pages: 62, status: "queued", progress: 0, chunks: 0, note: "queued" },
  { id: 'f2', name: "Board commentary — draft v7.docx", ext: "DOCX", size: "318 KB", pages: 14, status: "queued", progress: 0, chunks: 0, note: "queued" },
  { id: 'f3', name: "Segment P&L by quarter.xlsx", ext: "XLSX", size: "612 KB", pages: 8, status: "queued", progress: 0, chunks: 0, note: "queued" },
  { id: 'f4', name: "Auditor letter 2025-03-14.pdf", ext: "PDF", size: "221 KB", pages: 3, status: "queued", progress: 0, chunks: 0, note: "queued" },
];
const PROCESSING_FILES = [
  { id: 'f1', name: "FY25 Annual Report — Final.pdf", ext: "PDF", size: "4.2 MB", pages: 62, status: "ready",    progress: 1.00, chunks: 184, note: "indexed" },
  { id: 'f2', name: "Board commentary — draft v7.docx", ext: "DOCX", size: "318 KB", pages: 14, status: "indexing",progress: 0.55, chunks: 22, note: "indexing · 55%" },
  { id: 'f3', name: "Segment P&L by quarter.xlsx", ext: "XLSX", size: "612 KB", pages: 8, status: "reading",  progress: 0.18, chunks: 0, note: "reading · 18%" },
  { id: 'f4', name: "Auditor letter 2025-03-14.pdf", ext: "PDF", size: "221 KB", pages: 3, status: "queued",   progress: 0,    chunks: 0, note: "queued" },
];
const READY_FILES = SEED_FILES.map(f => ({ ...f, status: 'ready', progress: 1, note: 'indexed' }));

// A sample transcript — grounded answer about the annual report
const SAMPLE_TURNS = [
  {
    role: 'user',
    text: "What drove the 14% YoY revenue gain in the North America segment, and how does management explain the margin compression in Q3?"
  },
  {
    role: 'ai',
    intro: "Grounded in 4 files · 3 sources cited",
    paras: [
      { kind: 'h', text: "North America revenue · +14.2% YoY" },
      { kind: 'p', text: "The gain is attributed to two compounding factors. First, a full-year contribution from the Western distribution acquisition completed late in FY24 added an estimated $142M to segment revenue", cites: [1] },
      { kind: 'p', text: "Second, unit growth in the specialty category outpaced the broader market by roughly 4 points, which management credits to the relaunched private-label program and to pricing held steady through the first three quarters.", cites: [2] },
      { kind: 'h', text: "Q3 margin compression · −180 bps" },
      { kind: 'p', text: "Gross margin contracted 180 basis points in Q3 against a tough comp. Management points to one-off costs from the Memphis facility transition and an unusually warm autumn that slowed seasonal turn, with a smaller drag from freight inflation that has since normalized.", cites: [3] },
      { kind: 'note', text: "The auditor letter is still being read and has not contributed to this answer yet." },
    ]
  }
];

const CITATIONS = [
  { n: 1, file: "FY25 Annual Report — Final.pdf", loc: "p. 18 · §2.3", excerpt: "The Western acquisition, consolidated for the full fiscal year, contributed approximately $142M to North America segment revenue, compared with a partial-period contribution of $31M in the prior year." },
  { n: 2, file: "Board commentary — draft v7.docx", loc: "§ North America", excerpt: "Specialty unit growth outpaced the broader category by ~4 points. Management attributes the lift to the private-label relaunch and to deliberate price stability held through Q1–Q3." },
  { n: 3, file: "FY25 Annual Report — Final.pdf", loc: "p. 34 · MD&A", excerpt: "Gross margin in the third quarter compressed by 180 bps year-over-year, reflecting non-recurring costs associated with the Memphis facility transition and slower seasonal turn during an unusually warm October." },
];

const PROVIDERS = [
  { key: 'ollama', name: 'Ollama (local)', models: ['llama3.1-70b-q4', 'qwen2.5-32b-q5', 'mistral-small-q4'], active: true },
  { key: 'lmstudio', name: 'LM Studio', models: ['gpt-oss-20b', 'hermes-3-70b'] },
  { key: 'openai', name: 'OpenAI-compatible', models: ['user-defined'] },
];

const SUGGESTED_PROMPTS = [
  "Summarize what changed between drafts v6 and v7.",
  "List every figure above $1M referenced in the annual report.",
  "What does the auditor letter flag as material?",
  "Compare Q2 and Q3 segment performance with page references.",
];

// State keys
const APP_STATES = ["empty", "selected", "processing", "ready", "answered"];

Object.assign(window, {
  SESSIONS, SEED_FILES, EMPTY_FILES, SELECTED_FILES, PROCESSING_FILES, READY_FILES,
  SAMPLE_TURNS, CITATIONS, PROVIDERS, SUGGESTED_PROMPTS, APP_STATES,
});
