import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import { App } from "../App";

const json = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body });
const cfg = { name: "t1", path: "/s/team.yaml", raw: "name: t1\n", files: [], pending: { changed: false, keys: [] }, fields: [] };

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.innerHTML = "";
});

function open() {
  render(<MemoryRouter initialEntries={["/ship/t1/config/raw"]}><App /></MemoryRouter>);
}

describe("RawYamlPage", () => {
  it("--edit が無ければ編集欄を出さない", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(cfg)));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("--edit");
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("行番号つきのエラーを出し、通るまで確定できない。差分を見て確定する", async () => {
    document.head.innerHTML = '<meta name="yamato-token" content="tok">';
    const bodies: { url: string; body: Record<string, unknown> }[] = [];
    let valid = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("/edit/")) {
        const body = JSON.parse(String(init?.body));
        bodies.push({ url, body });
        if (url.endsWith("/commit")) return json({ written: true, hash: "h2" });
        return valid
          ? json({ valid: true, errors: [], changed: true, diff: "-name: t1\n+name: t2\n", base_hash: "h1" })
          : json({ valid: false, errors: ["行 1: yaml を読めない: bad"], changed: true, diff: "", base_hash: "h1" });
      }
      return json(cfg);
    }));
    open();
    const box = await screen.findByRole("textbox");
    fireEvent.change(box, { target: { value: "name: [t2\n" } });
    fireEvent.click(screen.getByRole("button", { name: "差分を見る" }));
    expect(await screen.findByText("行 1: yaml を読めない: bad")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "確定" })).toBeDisabled();

    valid = true;
    fireEvent.change(box, { target: { value: "name: t2\n" } });
    fireEvent.click(screen.getByRole("button", { name: "差分を見る" }));
    expect(await screen.findByLabelText("差分")).toHaveTextContent("+name: t2");
    fireEvent.click(screen.getByRole("button", { name: "確定" }));
    expect(await screen.findByRole("status")).toHaveTextContent("保存した");
    const commit = bodies.find((b) => b.url.endsWith("/commit"))!;
    expect(commit.body).toMatchObject({ ship: "t1", file: "team.yaml", content: "name: t2\n", base_hash: "h1" });
  });
});
