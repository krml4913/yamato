import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";

describe("App", () => {
  it("ナビの 3 項目を出す", () => {
    render(<MemoryRouter><App /></MemoryRouter>);
    for (const t of ["一覧", "艦隊", "艦ごと"]) {
      expect(screen.getAllByText(t).length).toBeGreaterThan(0);
    }
  });

  it("/ship/:name で艦名を出す", () => {
    render(<MemoryRouter initialEntries={["/ship/foo"]}><App /></MemoryRouter>);
    expect(screen.getByText("艦: foo")).toBeInTheDocument();
  });
});
