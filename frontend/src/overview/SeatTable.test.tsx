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
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("操作")).toBeNull();
  });

  it("生きている席に終業、止まっている persistent の席に入れ替え。それ以外は出さない", () => {
    setToken(true);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    expect(screen.getByRole("button", { name: "impl の終業を指示" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "pm に入れ替えの印" })).toBeTruthy();
    expect(screen.getAllByRole("button")).toHaveLength(2);
  });

  it("確認画面を経てから POST し、出力 (text として) と exit code を出す。alert は使わない", async () => {
    setToken(true);
    const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      argv: ["yamato", "seat-stop", "/s", "impl"], exit_code: 1, timed_out: false, truncated: false,
      output: "<b>handoff.md が更新されていません</b>",
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<SeatTable ship="dev" seats={seats} now={0} />);
    fireEvent.click(screen.getByRole("button", { name: "impl の終業を指示" }));
    expect(fetchMock).not.toHaveBeenCalled();   // 確認画面が先
    fireEvent.click(screen.getByRole("button", { name: "終業を指示する" }));
    await waitFor(() => expect(screen.getByRole("status").textContent).toBe("exit code 1"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/ops/seat-stop");
    expect(init.headers["X-Yamato-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({ ship: "dev", params: { seat: "impl" }, confirmed: true });
    expect(screen.getByLabelText("出力").textContent).toBe("<b>handoff.md が更新されていません</b>");
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
