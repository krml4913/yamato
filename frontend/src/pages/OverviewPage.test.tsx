import { act, render, screen } from "@testing-library/react";
import { REFRESH_MS, OverviewPage } from "./OverviewPage";
import type { Overview } from "../api/client";
import status from "../status.module.css";

const seat = (over: Record<string, unknown>) => ({
  name: "impl", role: "impl", shift: "per_task", alive: true, pid: 42, status: "busy", waiting_for: null,
  last: null, active: ["T-1"], reviewing: ["T-2"], unread: 3, flags: [], log: "作業中", tokens: 12_345,
  budget: null, last_cost: null, context: { tokens: 90_000, limit: 100_000 }, ...over,
});

const data = (): Overview => ({
  generated_at: 1_700_000_000, agents_error: null,
  waits: [{ ship: "dev", id: "D-1", title: "決める", urgent: true, age: "2h13m", recommendation: "案A" }],
  ships: [
    {
      name: "dev", error: null,
      state: {
        team: "dev", deadline: "deadline なし", hub: "pm", hub_last: null, tokens: 1_500_000, owner_unread: 2,
        multi_repo: true, counts: { active: 1, done: 4 },
        seats: [
          seat({ flags: [{ level: "red", text: "詰まり", detail: "なぜか" }, { level: "warn", text: "止まってる", detail: "" }] }),
          seat({ name: "hl", shift: "headless", last_cost: 0.5, budget: 2, context: null }),
        ],
        board: [{ id: "T-1", title: "実装", state: "active", assignee: "impl", blocked_on: ["T-0"] }],
        prs: [{ item: "T-1", title: "実装", state: "active", repo: "r", number: "7", link: "https://github.com/o/r/pull/7" }],
        anomalies: [{ kind: "crash", ts: 1_700_000_000, summary: "落ちた" }],
        report: { date: "2026-10-16", word: "良い日", tomorrow: "続き" },
        orphans: [{ id: "T-9", title: "孤児", seat: "x", why: "席が落ちた" }],
        memory: [{ role: null, count: 2, days: null, due: true }],
      },
    },
    { name: "bad", error: "ValueError: 壊れた", state: null },
  ],
});

describe("OverviewPage", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

  it("判断待ち・艦の枠・席の表・2 段目・PR・異常・壊れた艦を出す", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(data())));
    render(<OverviewPage />);
    expect(await screen.findByText("owner の判断待ち (1)")).toBeInTheDocument();
    expect(screen.getByText("急ぎ")).toBeInTheDocument();
    expect(screen.getByText("推し: 案A")).toBeInTheDocument();
    expect(screen.getByText("詰まり")).toHaveClass(status.red);
    expect(screen.getByText("止まってる")).toHaveClass(status.warn);
    expect(screen.getAllByText("T-1, T-2 (レビュー)").length).toBeGreaterThan(0);
    expect(screen.getByText(/前回 \$0\.50 \/ 上限 \$2/)).toBeInTheDocument();
    expect(screen.getByText("rotate が近い")).toBeInTheDocument();
    expect(screen.getByText(/1\.5M tok/)).toBeInTheDocument();
    expect(screen.getByText(/owner 宛て未読 2 件/)).toBeInTheDocument();
    expect(screen.getByText("一言: 良い日", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("席が落ちた")).toBeInTheDocument();
    expect(screen.getByText("棚卸しの目安に達した")).toBeInTheDocument();
    expect(screen.getByText("#7")).toHaveAttribute("href", "https://github.com/o/r/pull/7");
    expect(screen.getByText("crash")).toHaveClass(status.red);
    expect(screen.getByText(/読めない: ValueError/)).toBeInTheDocument();
  });

  it("判断待ちが無いときは静かな枠", async () => {
    const d = data(); d.waits = []; d.ships = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    render(<OverviewPage />);
    expect(await screen.findByText("owner の判断待ちは無い")).toBeInTheDocument();
    expect(screen.getByText(/艦がありません/)).toBeInTheDocument();
  });

  it("60 秒ごとに取り直す", async () => {
    vi.useFakeTimers();
    const f = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data())));
    render(<OverviewPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(f).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(REFRESH_MS); });
    expect(f).toHaveBeenCalledTimes(2);
  });

  it("取得に失敗したらそう出す", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("x", { status: 500 }));
    render(<OverviewPage />);
    expect(await screen.findByText(/取得に失敗した/)).toBeInTheDocument();
  });
});
