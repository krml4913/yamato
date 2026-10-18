import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SeatTable } from "./SeatTable";
import type { Seat } from "./types";

const seat = (over: Partial<Seat>): Seat => ({
  name: "impl", role: "impl", shift: "per_task", alive: true, pid: 42, status: "busy", waiting_for: null,
  last: null, active: [], reviewing: [], unread: 0, flags: [], log: "", tokens: 0,
  budget: null, last_cost: null, context: null, ...over,
});

const seats = [seat({}), seat({ name: "pm", shift: "persistent", alive: false, pid: null }), seat({ name: "old", alive: false, pid: null })];

function setToken(on: boolean) {
  document.head.querySelectorAll('meta[name="yamato-token"]').forEach((m) => m.remove());
  if (on) {
    const m = document.createElement("meta");
    m.name = "yamato-token";
    m.content = "tok";
    document.head.appendChild(m);
  }
}

describe("SeatTable の操作", () => {
  afterEach(() => { vi.restoreAllMocks(); setToken(false); });

  it("--edit でなければ(トークンが無ければ)操作の入口を出さない", () => {
    setToken(false);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    expect(screen.queryByRole("button", { name: /入れ替え|終業/ })).toBeNull();
    expect(screen.queryByText("操作")).toBeNull();
  });

  it("止まっている persistent の席に入れ替え、生きている席に終業を出す (強制停止は出さない)", () => {
    setToken(true);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    expect(screen.getByRole("button", { name: "pm に入れ替えの印" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "impl に終業を頼む" })).toBeTruthy();
    expect(screen.getAllByRole("button").filter((b) => b.textContent !== "?")).toHaveLength(2);   // 「?」は凡例
  });

  it("終業は確認を経て send に頼みの文を載せるだけ。CLI の出力をそのまま見せる", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "send", "/s", "impl"], exit_code: 0, timed_out: false, truncated: false, output: "記録した",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    fireEvent.click(screen.getByRole("button", { name: "impl に終業を頼む" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "頼む" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("exit code 0"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/ops/send");
    const body = JSON.parse(init.body);
    expect(body.params.seat).toBe("impl");
    expect(body.params.message).toContain("seat-stop");
    expect(body.confirmed).toBe(true);
    expect(screen.getByLabelText("出力").textContent).toBe("記録した");
  });

  it("確認画面を経てから POST し、出力 (text として) と exit code を出す。alert は使わない", async () => {
    setToken(true);
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "rotate", "/s", "pm"], exit_code: 1, timed_out: false, truncated: false,
      output: "<b>生きている席には立てられない</b>",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    fireEvent.click(screen.getByRole("button", { name: "pm に入れ替えの印" }));
    expect(fetchMock).not.toHaveBeenCalled();   // 確認画面が先
    fireEvent.click(screen.getByRole("button", { name: "印を立てる" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("exit code 1"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/ops/rotate");
    expect(init.headers["X-Yamato-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({ ship: "dev", params: { seat: "pm" }, confirmed: true });
    expect(screen.getByLabelText("出力").textContent).toBe("<b>生きている席には立てられない</b>");
    expect(screen.getByLabelText("出力").querySelector("b")).toBeNull();
    expect(alert).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "閉じる" }));
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("やめるを押せば POST しない。サーバーのエラーはそのまま出す", async () => {
    setToken(true);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "forbidden", message: "トークンが合わない" } }), { status: 403 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    fireEvent.click(screen.getByRole("button", { name: "pm に入れ替えの印" }));
    fireEvent.click(screen.getByRole("button", { name: "やめる" }));
    expect(fetchMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "pm に入れ替えの印" }));
    fireEvent.click(screen.getByRole("button", { name: "印を立てる" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("トークンが合わない"));
  });
});
