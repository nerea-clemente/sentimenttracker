"use client";

import { IS_STATIC, snapshotSetup, type SetupFinding } from "@/lib/api";

const LEVEL: Record<string, { cls: string; heading: string }> = {
  blocker: {
    cls: "banner danger",
    heading: "cannot produce a measurement until these are fixed",
  },
  warning: {
    cls: "banner warn",
    heading: "figures will be incomplete, or something is unmonitored",
  },
  ok: { cls: "banner info", heading: "checked and healthy" },
};

/**
 * What is still a placeholder, rendered from the same checks `cib doctor` runs.
 *
 * A reader who opens this dashboard and finds it empty deserves to be told why on the page. The
 * alternative is that an unconfigured install looks like a measured finding of low coverage,
 * which is the exact misreading this tool exists to prevent.
 */
export function SetupChecklist() {
  const setup = snapshotSetup();
  if (!setup) return null;

  const groups: ("blocker" | "warning" | "ok")[] = ["blocker", "warning", "ok"];
  return (
    <>
      <h2>Setup</h2>
      <p className="lede small">
        {setup.blockers} blocker(s), {setup.warnings} warning(s). Run{" "}
        <code className="inline">cib doctor</code> for the same list in the terminal
        {IS_STATIC ? ", as of this snapshot" : ""}.
      </p>
      {groups.map((level) => {
        const findings = setup.findings.filter((f) => f.level === level);
        if (!findings.length) return null;
        return (
          <div className={LEVEL[level].cls} key={level}>
            <strong>
              {level.toUpperCase()} ({findings.length}) — {LEVEL[level].heading}
            </strong>
            <ul>
              {findings.map((f: SetupFinding) => (
                <li key={f.code + f.title} style={{ marginBottom: "0.5rem" }}>
                  <strong style={{ display: "inline" }}>{f.title}</strong>
                  {f.detail && <div className="small">{f.detail}</div>}
                  {f.fix && (
                    <div className="small" style={{ marginTop: "0.2rem" }}>
                      fix: <code className="inline">{f.fix}</code>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </>
  );
}
