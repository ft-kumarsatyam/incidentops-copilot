import { incident } from "../test/factories";
import { evidenceLabel, humanizeTimeline, inTab, matchesQuery, percent, providerLabel, relativeTime, sortIncidents, statusMeta } from "./format";

describe("status and severity labels", () => {
  it("uses plain language and flags what needs attention", () => {
    expect(statusMeta("awaiting_approval")).toMatchObject({ label: "Needs approval", needsAttention: true });
    expect(statusMeta("needs_human")).toMatchObject({ label: "Needs a person", needsAttention: true });
    expect(statusMeta("failed").needsAttention).toBe(true);
    expect(statusMeta("succeeded").needsAttention).toBe(false);
  });

  it("does not crash on an unknown status from a newer server", () => {
    expect(statusMeta("something_new")).toMatchObject({ label: "something_new", tone: "neutral" });
  });
});

describe("filtering and sorting", () => {
  const open = incident({ run_id: "a", status: "awaiting_approval" });
  const done = incident({ run_id: "b", status: "succeeded" });
  const rejected = incident({ run_id: "c", status: "cancelled" });

  it("splits open and resolved", () => {
    expect([open, done, rejected].filter((i) => inTab(i, "open"))).toEqual([open]);
    expect([open, done, rejected].filter((i) => inTab(i, "resolved"))).toEqual([done, rejected]);
    expect([open, done, rejected].filter((i) => inTab(i, "all"))).toHaveLength(3);
  });

  it("searches summary, service, id and owner case-insensitively", () => {
    expect(matchesQuery(open, "CHECKOUT")).toBe(true);
    expect(matchesQuery(open, "payments")).toBe(true);
    expect(matchesQuery(open, "inc-1")).toBe(false);
    expect(matchesQuery(open, "CHECKOUT-API")).toBe(true);
    expect(matchesQuery(open, "nothing like this")).toBe(false);
    expect(matchesQuery(open, "   ")).toBe(true);
  });

  it("puts incidents that need attention first, then newest", () => {
    const olderAttention = incident({ run_id: "old", status: "needs_human", updated_at: "2026-01-01T00:00:00Z" });
    const newerDone = incident({ run_id: "new", status: "succeeded", updated_at: "2026-02-01T00:00:00Z" });
    const newerAttention = incident({ run_id: "newer", status: "failed", updated_at: "2026-03-01T00:00:00Z" });

    expect(sortIncidents([newerDone, olderAttention, newerAttention]).map((i) => i.run_id)).toEqual([
      "newer",
      "old",
      "new",
    ]);
  });
});

describe("formatting helpers", () => {
  it("describes time relative to now", () => {
    const now = Date.parse("2026-01-01T12:00:00Z");
    expect(relativeTime("2026-01-01T11:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-01-01T11:30:00Z", now)).toBe("30 minutes ago");
    expect(relativeTime("2025-12-30T12:00:00Z", now)).toBe("2 days ago");
    expect(relativeTime(null, now)).toBe("");
  });

  it("formats percentages and labels", () => {
    expect(percent(0.876)).toBe("88%");
    expect(percent(null)).toBe("n/a");
    expect(providerLabel("rules")).toBe("Rules engine");
    expect(providerLabel("claude:claude-sonnet-5-5")).toBe("Claude (claude-sonnet-5-5)");
    expect(evidenceLabel("runbook:checkout-api.md")).toBe("Runbook: checkout-api.md");
    expect(evidenceLabel("github")).toBe("GitHub");
  });
});

describe("humanizeTimeline", () => {
  it.each([
    ["incident received", "Report received"],
    ["research attempt 1", "Started gathering evidence"],
    ["research attempt 2", "Gathered more evidence (attempt 2)"],
    ["retrieved 3 runbook chunks", "Found 3 relevant runbook sections"],
    ["retrieved 1 runbook chunks", "Found 1 relevant runbook section"],
    ["assessed as SEV-1 by rules", "Assessed as SEV-1 by Rules engine"],
    ["critic: awaiting_approval", "Safety review passed"],
    ["critic: needs_human (missing fields)", "Review escalated to a person (missing fields)"],
    ["human decision: approved", "A person approved"],
    ["human decision: rejected", "A person rejected"],
    ["monitoring timed out; impact is unverified", "Monitoring timed out, so impact is unverified"],
  ])("%s -> %s", (raw, expected) => {
    expect(humanizeTimeline(raw)).toBe(expected);
  });

  it("passes through lines it does not know", () => {
    expect(humanizeTimeline("something new")).toBe("something new");
  });
});
