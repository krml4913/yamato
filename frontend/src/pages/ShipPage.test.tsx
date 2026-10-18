import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ShipPage, seatNames } from "./ShipPage";

function setToken(on: boolean) {
  document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  if (on) {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  }
}

const page = () => render(
  <MemoryRouter initialEntries={["/ship/dev"]}>
    <Routes><Route path="/ship/:name" element={<ShipPage />} /></Routes>
  </MemoryRouter>,
);

// 席の一覧を取る config の GET は数えず、ops の POST だけを見る
const ops = (m: ReturnType<typeof vi.fn>) => m.mock.calls.filter(([u]) => String(u).includes("/ops/"));

describe("艦ページの操作", () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setToken(false); });

  it("--edit でなければ操作の入口を出さない", () => {
    setToken(false);
    page();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("md を役割・charter・knowledge に分け、名前で出す。_ の役割は内部として分ける", async () => {
    setToken(false);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      fields: [], raw: "", pending: { changed: false, keys: [] },
      files: [
        { file: "roles/pm.md", kind: "role", bytes: 1 },
        { file: "roles/_memory-curator.md", kind: "role", bytes: 1 },
        { file: "charter.md", kind: "charter", bytes: 1 },
        { file: "knowledge.md", kind: "knowledge", bytes: 1 },
      ],
    }), { status: 200 }))));
    page();
    const link = await screen.findByRole("link", { name: "pm" });
    expect(link.getAttribute("href")).toBe("/ship/dev/files/roles%2Fpm.md");
    expect(screen.getByRole("link", { name: "charter" }).getAttribute("href")).toBe("/ship/dev/files/charter.md");
    expect(screen.getByRole("link", { name: "knowledge" }).getAttribute("href")).toBe("/ship/dev/files/knowledge.md");
    const internal = screen.getByRole("heading", { name: "内部の役割" }).parentElement as HTMLElement;
    expect(within(internal).getByRole("link", { name: "_memory-curator" })).toBeTruthy();
    expect(within(internal).queryByRole("link", { name: "pm" })).toBeNull();
    expect(screen.queryByText("roles/pm.md")).toBeNull();
  });

  it("艦の状態・席の表・board を一覧の部品で頭に出す", async () => {
    setToken(false);
    const state = {
      seats: [{ name: "pm", shift: "persistent", alive: true, pid: 1, status: "idle", waiting_for: null, last: null,
        active: [], reviewing: [], unread: 0, flags: [], tokens: 0, log: "" }],
      deadline: "10-18 22:00", hub: "pm", hub_last: null, tokens: 0, orphans: [], owner_unread: 2,
      counts: { open: 1 }, board: [{ id: "001", state: "open", title: "やること", assignee: null, blocked_on: [] }],
      prs: [], anomalies: [], multi_repo: false, team: "dev", report: null, memory: [],
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string) => Promise.resolve(new Response(JSON.stringify(
      String(u).includes("/overview")
        ? { generated_at: 0, waits: [], ships: [{ name: "dev", state, error: null }, { name: "other", state: null, error: "x" }] }
        : { fields: [] },
    ), { status: 200 }))));
    page();
    expect(await screen.findByText("稼働 1/1")).toBeTruthy();
    expect(screen.getByText("owner 宛て未読 2")).toBeTruthy();
    expect(screen.getByText("やること", { exact: false })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "席" })).toBeTruthy();
  });

  it("普段の操作と危ない操作を分け、危ない操作は halt だけを枠の中に置く", async () => {
    setToken(true);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    const danger = screen.getByRole("group", { name: "危ない操作" });
    expect(within(danger).queryByRole("button", { name: "down --force" })).toBeNull();
    expect(within(danger).getAllByRole("button")).toHaveLength(1);
    expect(within(danger).getByRole("button", { name: "halt" })).toBeTruthy();
    expect(within(danger).queryByRole("button", { name: "down" })).toBeNull();
    const usual = screen.getByRole("group", { name: "普段の操作" });
    for (const n of ["up", "extend", "down"]) expect(within(usual).getByRole("button", { name: n })).toBeTruthy();
  });

  it("操作は状態の要点のすぐ下 (席の表・board の上) に出し、艦名を二重に出さない", async () => {
    setToken(true);
    vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string) => Promise.resolve(new Response(JSON.stringify(
      String(u).includes("/overview")
        ? { generated_at: 0, waits: [], ships: [{ name: "dev", error: null, state: {
          seats: [], deadline: "", hub: "pm", hub_last: null, tokens: 0, orphans: [], owner_unread: 0,
          counts: {}, board: [], prs: [], anomalies: [], multi_repo: false, team: "dev", report: null, memory: [] } }] }
        : { fields: [] },
    ), { status: 200 }))));
    page();
    const send = await screen.findByRole("group", { name: "メッセージを送る" });
    const opsGroup = screen.getByRole("group", { name: "艦の操作" });
    const board = screen.getByRole("heading", { name: "board" });
    expect(send.compareDocumentPosition(opsGroup) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(opsGroup.compareDocumentPosition(board) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText("dev", { exact: false }).filter((e) => /^H[12]$/.test(e.tagName) || e.closest("h2"))).toHaveLength(1);
  });

  it("halt 1 つ向けの説明を書く", async () => {
    setToken(true);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response("{}", { status: 200 }))));
    page();
    const danger = await screen.findByRole("group", { name: "危ない操作" });
    const hints = [...danger.querySelectorAll("span")].map((e) => e.textContent);
    expect(hints).toHaveLength(1);
    expect(danger.textContent).toContain("猶予つきで止めるなら");
  });

  it("艦の状態が読めなくても操作を出し、艦名を二重にしない", async () => {
    setToken(true);
    vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string) => Promise.resolve(new Response(JSON.stringify(
      String(u).includes("/overview")
        ? { generated_at: 0, waits: [], ships: [{ name: "dev", error: "壊れている", state: null }] }
        : { fields: [] },
    ), { status: 200 }))));
    page();
    expect(await screen.findByText(/読めない: 壊れている/)).toBeTruthy();
    expect(screen.getByRole("group", { name: "艦の操作" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "メッセージを送る" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 2, name: "dev" })).toBeNull();
  });

  it("読み込み中は状態の枠を先に出し、設定リンクは状態の下に置く", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string) => Promise.resolve(new Response(JSON.stringify(
      String(u).includes("/overview")
        ? { generated_at: 0, waits: [], ships: [{ name: "dev", error: "x", state: null }] }
        : { fields: [] },
    ), { status: 200 }))));
    page();
    expect(screen.getByRole("status").textContent).toContain("読み込み中");
    await waitFor(() => expect(screen.queryByText(/読み込み中/)).toBeNull());
    const link = screen.getByRole("link", { name: "設定" });
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/読めない: x/).compareDocumentPosition(link) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("状態が取れなかったらエラーを出す", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("down")));
    page();
    expect((await screen.findByRole("alert")).textContent).toContain("読めなかった");
  });

  it("止まっている艦では down を、動いている艦では up を目立たせない", async () => {
    setToken(true);
    const fetchFor = (alive: boolean) => vi.fn().mockImplementation((u: string) => Promise.resolve(new Response(JSON.stringify(
      String(u).includes("/overview")
        ? { generated_at: 0, waits: [], ships: [{ name: "dev", error: null, state: {
          seats: [{ name: "pm", shift: "persistent", alive, pid: 1, status: null, waiting_for: null, last: null,
            active: [], reviewing: [], unread: 0, flags: [], tokens: 0, log: "" }],
          deadline: "", hub: "pm", hub_last: null, tokens: 0, orphans: [], owner_unread: 0,
          counts: {}, board: [], prs: [], anomalies: [], multi_repo: false, team: "dev", report: null, memory: [] } }] }
        : { fields: [] },
    ), { status: 200 })));
    vi.stubGlobal("fetch", fetchFor(false));
    const { unmount } = page();
    expect(await screen.findByText("いま止まっている")).toBeTruthy();
    expect(screen.getByRole("button", { name: "down" }).className).toMatch(/quiet/);
    expect(screen.getByRole("button", { name: "up" }).className).not.toMatch(/quiet/);
    unmount();
    vi.stubGlobal("fetch", fetchFor(true));
    page();
    expect(await screen.findByText("いま動いている")).toBeTruthy();
    expect(screen.getByRole("button", { name: "up" }).className).toMatch(/quiet/);
    expect(screen.getByRole("button", { name: "down" }).className).not.toMatch(/quiet/);
  });

  it("files が取れなければ一覧を出さない", async () => {
    setToken(false);
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response("{}", { status: 500 }))));
    page();
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByLabelText("役割と方針のファイル")).toBeNull();
  });

  it("4 つの操作を出す", async () => {
    setToken(true);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    for (const n of ["up", "down", "extend", "halt"]) {
      expect(screen.getByRole("button", { name: n })).toBeTruthy();
    }
  });

  it.each([
    ["halt", "halt", {}, "緊急停止する"],
    ["down", "down", {}, "終業を指示する"],
  ])("%s は確認画面を経てから POST する (alert は使わない)", async (label, op, params, confirm) => {
    setToken(true);
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato", "x"], exit_code: 1, timed_out: false, truncated: false, output: "<b>断られた</b>",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(ops(fetchMock)).toHaveLength(0);
    const region = screen.getByRole("region");
    fireEvent.click(screen.getByRole("button", { name: confirm }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    const [url, init] = ops(fetchMock)[0];
    expect(String(url)).toBe(`/api/v1/ops/${op}`);
    expect(JSON.parse(init.body)).toEqual({ ship: "dev", params, confirmed: true });
    expect(await screen.findByText("exit code 1")).toBeTruthy();
    expect(region.querySelector("pre")?.textContent).toBe("<b>断られた</b>");
    expect(alert).not.toHaveBeenCalled();
  });

  it("up は稼働時間と席を、extend は時間を params で送る", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato"], exit_code: 0, timed_out: false, truncated: false, output: "ok",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    fireEvent.change(screen.getByLabelText(/稼働時間/), { target: { value: "3h" } });
    fireEvent.change(screen.getByLabelText(/一緒に起こす席/), { target: { value: "impl,review" } });
    fireEvent.click(screen.getByRole("button", { name: "up" }));
    fireEvent.click(screen.getByRole("button", { name: "起動する" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    expect(JSON.parse(ops(fetchMock)[0][1].body).params).toEqual({ for: "3h", seats: "impl,review" });
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    fireEvent.change(screen.getByLabelText(/延ばす時間/), { target: { value: "1h" } });
    fireEvent.click(screen.getByRole("button", { name: "extend" }));
    fireEvent.click(screen.getByRole("button", { name: "延ばす" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(2));
    expect(String(ops(fetchMock)[1][0])).toBe("/api/v1/ops/extend");
    expect(JSON.parse(ops(fetchMock)[1][1].body).params).toEqual({ span: "1h" });
  });

  it("up と extend の入力はそれぞれ名前のあるまとまりで囲み、入力に名前がある", async () => {
    setToken(true);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    const up = screen.getByRole("group", { name: "艦を起こす" });
    expect(within(up).getByRole("textbox", { name: "稼働時間" })).toBeTruthy();
    expect(within(up).getByRole("textbox", { name: "一緒に起こす席" })).toBeTruthy();
    expect(within(up).getByRole("button", { name: "up" })).toBeTruthy();
    const ext = screen.getByRole("group", { name: "稼働時間を延ばす" });
    expect(within(ext).getByRole("textbox", { name: "延ばす時間" })).toBeTruthy();
    expect(within(ext).getByRole("button", { name: "extend" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "本文 (席に送る)" })).toBeTruthy();
  });

  it("send は席と本文をそのまま params で送り、出力は text で描く", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      argv: ["yamato", "send"], exit_code: 0, timed_out: false, truncated: false, output: "<i>送った</i>",
    }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    fireEvent.change(screen.getByLabelText(/^席/), { target: { value: "pm" } });
    fireEvent.change(screen.getByLabelText(/本文/), { target: { value: "--from x\n二行目" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(ops(fetchMock)).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "送る" }));
    await waitFor(() => expect(ops(fetchMock)).toHaveLength(1));
    expect(String(ops(fetchMock)[0][0])).toBe("/api/v1/ops/send");
    expect(JSON.parse(ops(fetchMock)[0][1].body)).toEqual({
      ship: "dev", params: { seat: "pm", message: "--from x\n二行目" }, confirmed: true });
    expect((await screen.findByLabelText("出力")).textContent).toBe("<i>送った</i>");
  });

  it("send は席と本文が揃うまで押せない", async () => {
    setToken(true);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    const send = screen.getByRole("button", { name: "send" }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^席/), { target: { value: "pm" } });
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/本文/), { target: { value: "  " } });
    expect(send.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/本文/), { target: { value: "hi" } });
    expect(send.disabled).toBe(false);
  });

  it("席は艦の roles から select にする", async () => {
    setToken(true);
    const cfg = { fields: [{ key: "roles", set: true, value: { pm: {}, impl: { count: 2 } } }] };
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify(cfg), { status: 200 }))));
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    const select = await screen.findByRole("combobox", { name: /^席/ });
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["(選ぶ)", "pm", "impl-1", "impl-2"]);
    fireEvent.change(select, { target: { value: "impl-2" } });
    expect((select as HTMLSelectElement).value).toBe("impl-2");
  });

  it("席が取れないときは自由入力に戻す", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ error: { message: "壊れている" } }), { status: 500 })));
    vi.stubGlobal("fetch", fetchMock);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByRole("textbox", { name: /^席/ })).toBeTruthy();
  });

  it("seatNames は読めない roles で null を返す", () => {
    const mk = (value: unknown, set = true) => ({ fields: [{ key: "roles", set, value }] }) as never;
    expect(seatNames({ fields: [] } as never)).toBeNull();
    expect(seatNames(mk(undefined, false))).toBeNull();
    expect(seatNames(mk({ a: { count: 0 } }))).toBeNull();
    expect(seatNames(mk({ a: { count: "x" } }))).toBeNull();
    expect(seatNames(mk({}))).toBeNull();
  });

  it("やめるを押せば呼ばない", async () => {
    setToken(true);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    page();
    await screen.findByRole("group", { name: "艦の操作" });
    fireEvent.click(screen.getByRole("button", { name: "halt" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(ops(fetchMock)).toHaveLength(0);
    expect(screen.queryByRole("region")).toBeNull();
  });
});
