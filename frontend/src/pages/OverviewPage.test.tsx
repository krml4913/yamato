import { act, fireEvent, render as rtlRender, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ReactElement } from "react";
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

const render = (ui: ReactElement) => rtlRender(<MemoryRouter>{ui}</MemoryRouter>);

function setToken(on: boolean) {
  document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  if (on) {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  }
}

describe("OverviewPage", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); setToken(false); });

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
    expect(screen.getByText("owner 宛て未読 2")).toBeInTheDocument();
    expect(screen.getByText("一言: 良い日", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("席が落ちた")).toBeInTheDocument();
    expect(screen.getByText("棚卸しの目安に達した")).toBeInTheDocument();
    expect(screen.getByText("#7")).toHaveAttribute("href", "https://github.com/o/r/pull/7");
    expect(screen.getByText("crash")).toHaveClass(status.red);
    expect(screen.getByText(/読めない: ValueError/)).toBeInTheDocument();
    expect(screen.queryByText(/すぐ手が要る/)).toBeNull();   // 凡例は「?」を開くまで出ない
  });

  it("要対応の見出しの「?」で凡例が開く (押す・focus・Esc)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(data())));
    render(<OverviewPage />);
    await screen.findByText("稼働 2/2");
    const btn = screen.getAllByRole("button", { name: "要対応の見方" })[0];
    expect(btn).toHaveAttribute("aria-expanded", "false");
    fireEvent.focus(btn);
    expect(screen.getByRole("note")).toHaveTextContent(/すぐ手が要る/);
    fireEvent.blur(btn);
    expect(screen.queryByRole("note")).toBeNull();
    fireEvent.click(btn);
    expect(screen.getByRole("note")).toBeInTheDocument();
    fireEvent.keyDown(btn, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("作業ログの列は行が無ければ「-」", async () => {
    const d = data();
    d.ships[0].state!.seats = [seat({ name: "quiet", log: "", active: [], reviewing: [], unread: 0 })];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    render(<OverviewPage />);
    await screen.findByText("稼働 1/1");
    const row = screen.getAllByText("quiet").map((e) => e.closest("tr")).find((r) => r)!;
    const cells = row.querySelectorAll("td");
    expect(cells[cells.length - 1]).toHaveTextContent(/^-$/);
  });

  it("艦の頭に要点のバッジを出し、0 件のものは出さない", async () => {
    const d = data();
    d.ships[0].state!.deadline = "deadline 10-17 17:23 (残り 1h54m)";
    d.ships[0].state!.owner_unread = 0;
    d.ships[0].state!.orphans = [];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    render(<OverviewPage />);
    expect(await screen.findByText("稼働 2/2")).toBeInTheDocument();
    expect(screen.getByText("残り 1h54m")).toBeInTheDocument();   // 残り時間はバッジだけ
    expect(screen.getByText(/^deadline 10-17 17:23 \//)).not.toHaveTextContent(/残り/);
    expect(screen.getByText("判断待ち 1")).toBeInTheDocument();
    expect(screen.getAllByText("active 1")).toHaveLength(2);   // バッジと board の件数
    expect(screen.queryByText(/^孤児 \d/)).toBeNull();
    expect(screen.queryByText(/^owner 宛て未読/)).toBeNull();
    expect(screen.queryByText(/^open \d/)).toBeNull();
  });

  it("止まっている艦は動いている艦の下で 1 行に畳む", async () => {
    const d = data();
    const stoppedShip = structuredClone(d.ships[0]);
    stoppedShip.name = "old";
    stoppedShip.state!.seats = [seat({ alive: false, pid: null, last: d.generated_at - 3600 * 137 })];
    d.ships = [stoppedShip, d.ships[0]];   // 止まっている艦が先でも下に回る
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    const { container } = render(<OverviewPage />);
    expect(await screen.findByText("止まっている艦 (1)")).toBeInTheDocument();
    const details = container.querySelector("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(details.querySelector("summary")).toHaveTextContent("old");
    expect(details.querySelector("summary")).toHaveTextContent("最後の動き 137h00m前");
    const names = [...container.querySelectorAll("h2")].map((h) => h.textContent);
    expect(names.indexOf("dev")).toBeLessThan(names.indexOf("止まっている艦 (1)"));
    expect(names.indexOf("止まっている艦 (1)")).toBeLessThan(names.indexOf("old"));
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

  it("読むだけ / 編集できる をモードどおりに出す", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(data())));
    const first = render(<OverviewPage />);
    expect(await screen.findByText(/読むだけ/)).toBeInTheDocument();
    first.unmount();
    setToken(true);
    render(<OverviewPage />);
    expect(await screen.findByText(/編集できる/)).toBeInTheDocument();
    expect(screen.queryByText(/読むだけ/)).toBeNull();
  });

  it("艦の名前は艦のページへのリンク (止まっている艦も)", async () => {
    const d = data();
    const stoppedShip = structuredClone(d.ships[0]);
    stoppedShip.name = "old ship";
    stoppedShip.state!.seats = [seat({ alive: false, pid: null })];
    d.ships = [d.ships[0], stoppedShip];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    render(<OverviewPage />);
    expect(await screen.findByRole("link", { name: "dev" })).toHaveAttribute("href", "/ship/dev");
    expect(screen.getAllByRole("link", { name: "old ship" })[0]).toHaveAttribute("href", "/ship/old%20ship");
  });

  it("異常は 24 時間以内を赤で上に、古いものは畳んで赤くしない", async () => {
    const d = data();
    const now = d.generated_at;
    d.ships[0].state!.anomalies = [
      { kind: "spin_suspected", ts: now - 3600, summary: "最近" },
      { kind: "pr_conflict", ts: now - 30 * 3600, summary: "昨日" },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    const { container } = render(<OverviewPage />);
    expect(await screen.findByText("spin_suspected")).toHaveClass(status.red);
    const old = container.querySelector("details[class*=oldAnomalies]")!;
    expect(old).not.toHaveAttribute("open");
    expect(old.querySelector("summary")).toHaveTextContent("24 時間より前の異常 (1 件)");
    expect(screen.getByText(/pr_conflict/)).not.toHaveClass(status.red);
    expect(old.textContent).toContain("昨日");
  });

  it("古い異常しか無ければ今は無いと出す", async () => {
    const d = data();
    d.ships[0].state!.anomalies = [{ kind: "crash", ts: d.generated_at - 48 * 3600, summary: "前々日" }];
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(d)));
    render(<OverviewPage />);
    expect(await screen.findByText("24 時間以内の異常は無い")).toBeInTheDocument();
  });
});
