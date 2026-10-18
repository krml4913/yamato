import { act, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider } from "react-router-dom";
import { ShipFilePage } from "./ShipFilePage";
import { vi } from "vitest";
import { App } from "../App";

afterEach(() => vi.unstubAllGlobals());

describe("ShipFilePage", () => {
  it("path をエンコードして取りに行き、markdown を出す", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ name: "a", file: "roles/impl.md", kind: "role", text: "# 実装\n本文", bytes: 9 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter initialEntries={["/ship/a/files/roles%2Fimpl.md"]}><App /></MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { level: 1, name: "実装" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ships/a/files/roles%2Fimpl.md");
  });

  it("404 は理由を出す", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 404, json: async () => ({ error: { code: "not_found", message: "その md は読めない" } }),
    }));
    render(<MemoryRouter initialEntries={["/ship/a/files/x.md"]}><App /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("その md は読めない");
  });
});

describe("ShipFilePage の編集", () => {
  const file = (kind: string) => ({ name: "a", file: "charter.md", kind, text: "# 憲章\n", bytes: 7 });
  const json = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body });

  afterEach(() => { document.head.innerHTML = ""; });

  it("--edit が無ければ編集ボタンは出ない", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(file("charter"))));
    render(<MemoryRouter initialEntries={["/ship/a/files/charter.md"]}><App /></MemoryRouter>);
    await screen.findByRole("heading", { level: 1, name: "憲章" });
    expect(screen.queryByRole("button", { name: "編集" })).toBeNull();
  });

  it("knowledge は --edit でも読むだけ", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(file("knowledge"))));
    render(<MemoryRouter initialEntries={["/ship/a/files/knowledge.md"]}><App /></MemoryRouter>);
    await screen.findByRole("heading", { level: 1, name: "憲章" });
    expect(screen.queryByRole("button", { name: "編集" })).toBeNull();
  });

  it("差分を見てから確定する。確定は差分を見るまで押せない", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    let committed = "";
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/edit/preview")) {
        return json({ valid: true, errors: [], changed: true, diff: "-# 憲章\n+# 新憲章\n", base_hash: "h1" });
      }
      if (url.endsWith("/edit/commit")) {
        committed = String(init?.body);
        return json({ written: true, hash: "h2" });
      }
      return json(file("charter"));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter initialEntries={["/ship/a/files/charter.md"]}><App /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "編集" }));
    expect(screen.getByRole("button", { name: "確定" })).toBeDisabled();
    const box = screen.getByRole("textbox");
    expect(box).not.toHaveClass(/nowrap/);
    fireEvent.change(box, { target: { value: "# 新憲章" } });
    fireEvent.click(screen.getByRole("button", { name: "差分を見る" }));
    expect(await screen.findByLabelText("差分")).toHaveTextContent("+# 新憲章");
    fireEvent.click(screen.getByRole("button", { name: "確定" }));
    await screen.findByRole("button", { name: "編集" });
    expect(JSON.parse(committed)).toEqual({ ship: "a", file: "charter.md", content: "# 新憲章", base_hash: "h1" });
  });

  it("検証に落ちたら確定できない / 競合は理由を出す", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    let n = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/edit/preview")) {
        n += 1;
        return n === 1
          ? json({ valid: false, errors: ["中身が空"], changed: true, diff: "", base_hash: "h" })
          : json({ valid: true, errors: [], changed: true, diff: "+x\n", base_hash: "h" });
      }
      if (url.endsWith("/edit/commit")) return json({ error: { code: "conflict", message: "変わっている" } }, false, 409);
      return json(file("charter"));
    }));
    render(<MemoryRouter initialEntries={["/ship/a/files/charter.md"]}><App /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "編集" }));
    fireEvent.click(screen.getByRole("button", { name: "差分を見る" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("中身が空");
    expect(screen.getByRole("button", { name: "確定" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "差分を見る" }));
    fireEvent.click(await screen.findByRole("button", { name: "確定" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("変わっている");
  });
});

describe("ShipFilePage の編集 (移動)", () => {
  it("編集中に別の file へ戻る/進むと編集は閉じる (knowledge では開かない)", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      json: async () => url.includes("knowledge")
        ? { name: "a", file: "knowledge.md", kind: "knowledge", text: "# 知見\n", bytes: 5 }
        : { name: "a", file: "charter.md", kind: "charter", text: "# 憲章\n", bytes: 7 },
    })));
    const router = createMemoryRouter([{ path: "/ship/:name/files/:file", element: <ShipFilePage /> }], {
      initialEntries: ["/ship/a/files/charter.md"],
    });
    render(<RouterProvider router={router} />);
    fireEvent.click(await screen.findByRole("button", { name: "編集" }));
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    await act(async () => { await router.navigate("/ship/a/files/knowledge.md"); });
    await screen.findByRole("heading", { level: 1, name: "知見" });
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "編集" })).toBeNull();
    document.head.innerHTML = "";
  });
});
