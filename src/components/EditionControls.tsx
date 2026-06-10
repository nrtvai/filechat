import { ShieldCheck } from "lucide-react";
import type { CurrentUser, Edition, MembershipRole } from "../types";

function modeValue(user: CurrentUser) {
  if (!user.enterprise_enabled) return "community";
  return `enterprise:${user.role}`;
}

function parseMode(value: string): { edition: Edition; role: MembershipRole } {
  if (value === "community") return { edition: "community", role: "owner" };
  const role = value.split(":")[1];
  return {
    edition: "enterprise",
    role: role === "owner" || role === "admin" || role === "member" ? role : "admin",
  };
}

export function EditionControls({ user, setTestMode }: { user: CurrentUser; setTestMode: (edition: Edition, role: MembershipRole) => Promise<void> }) {
  const canSwitchMode = Boolean(user.capabilities.switch_test_mode);
  return (
    <div className="edition-controls">
      <div className={`edition-pill ${user.enterprise_enabled ? "enterprise" : "community"}`}>
        <ShieldCheck size={13} />
        <span>{user.enterprise_enabled ? "Enterprise" : "Community"}</span>
        <small>{user.role}</small>
      </div>
      {canSwitchMode && (
        <label className="role-switch">
          <span className="mono caps">test mode</span>
          <select
            aria-label="Test mode"
            value={modeValue(user)}
            onChange={(event) => {
              const next = parseMode(event.target.value);
              void setTestMode(next.edition, next.role);
            }}
          >
            <option value="community">Community</option>
            <option value="enterprise:owner">Enterprise owner</option>
            <option value="enterprise:admin">Enterprise admin</option>
            <option value="enterprise:member">Enterprise member</option>
          </select>
        </label>
      )}
    </div>
  );
}
