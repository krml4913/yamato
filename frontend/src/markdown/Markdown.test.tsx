import { render, screen } from "@testing-library/react";
import { Markdown } from "./Markdown";

describe("Markdown", () => {
  it("見出し・箇条書き・コード・強調を出す", () => {
    const { container } = render(
      <Markdown text={"# 題\n\n- 一つ目 **太字** と `code`\n- 二つ目\n  - 入れ子\n\n```\nx < y\n```\n"} />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "題" })).toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(3);
    expect(container.querySelector("strong")?.textContent).toBe("太字");
    expect(container.querySelector("pre code")?.textContent).toBe("x < y");
  });

  it("生の HTML は要素にならず文字として出る", () => {
    const { container } = render(<Markdown text={'<script>alert(1)</script>\n\n<img src=x onerror="alert(1)">'} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("リンクは http(s) と mailto だけ。javascript: は文字のまま", () => {
    const { container } = render(<Markdown text={"[ok](https://example.com) [bad](javascript:alert(1))"} />);
    const links = container.querySelectorAll("a");
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute("href")).toBe("https://example.com");
    expect(links[0].getAttribute("rel")).toContain("noopener");
    expect(container.textContent).toContain("[bad](javascript:alert(1))");
  });

  it("表を出す", () => {
    const { container } = render(<Markdown text={"| a | b |\n|---|---|\n| 1 | 2 |\n"} />);
    expect(container.querySelectorAll("th")).toHaveLength(2);
    expect(container.querySelectorAll("td")).toHaveLength(2);
  });
});
