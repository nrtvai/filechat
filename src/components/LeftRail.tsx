import { useState } from "react";
import { FileText, Library, MessageSquarePlus, Search } from "lucide-react";
import type { FileRecord, Session } from "../types";
import { FileMini } from "./shared";

export function LeftRail(props: {
  open: boolean;
  mode: "sessions" | "files";
  setMode: (mode: "sessions" | "files") => void;
  sessions: Session[];
  activeSessionId: string | null;
  setActiveSessionId: (id: string) => void;
  files: FileRecord[];
  createSession: () => void;
}) {
  const [sessionSearch, setSessionSearch] = useState("");
  const normalizedSessionSearch = sessionSearch.trim().toLowerCase();
  const visibleSessions = normalizedSessionSearch
    ? props.sessions.filter((session) => session.title.toLowerCase().includes(normalizedSessionSearch))
    : props.sessions;

  if (!props.open) {
    return <aside className="rail rail-collapsed"><Library size={16} /><FileText size={16} /></aside>;
  }
  return (
    <aside className="rail">
      <button className="primary-action" onClick={props.createSession}><MessageSquarePlus size={15} /> New session</button>
      <div className="search-box"><Search size={13} /><input placeholder="Search sessions" value={sessionSearch} onChange={(event) => setSessionSearch(event.target.value)} /></div>
      <div className="seg">
        <button className={props.mode === "sessions" ? "on" : ""} onClick={() => props.setMode("sessions")}>Sessions</button>
        <button className={props.mode === "files" ? "on" : ""} onClick={() => props.setMode("files")}>Files {props.files.length}</button>
      </div>
      <div className="rail-list">
        {props.mode === "sessions" ? visibleSessions.map((session) => (
          <button key={session.id} className={`session-row ${session.id === props.activeSessionId ? "active" : ""}`} onClick={() => props.setActiveSessionId(session.id)}>
            <span>{session.title}</span>
            <small>{session.file_count} files</small>
          </button>
        )) : props.files.map((file) => <FileMini key={file.id} file={file} />)}
      </div>
    </aside>
  );
}
