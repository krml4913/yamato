import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
