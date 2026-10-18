import type { ReactNode } from "react";
import { parseBlocks, safeHref, type Block, type Inline } from "./parse";
import styles from "./markdown.module.css";

// markdown を React の要素にして出す。本文は必ず text node になる (HTML を文字列のまま挿す API は使わない)。
function inline(nodes: Inline[]): ReactNode[] {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return n.s;
      case "code": return <code key={i}>{n.s}</code>;
      case "strong": return <strong key={i}>{inline(n.c)}</strong>;
      case "em": return <em key={i}>{inline(n.c)}</em>;
      case "link": return <a key={i} href={safeHref(n.href) ?? undefined} target="_blank" rel="noopener noreferrer">{inline(n.c)}</a>;
    }
  });
}

function block(b: Block, key: number): ReactNode {
  switch (b.t) {
    case "heading": {
      const H = `h${Math.min(b.level, 6)}` as "h1";
      return <H key={key}>{inline(b.c)}</H>;
    }
    case "para": return <p key={key}>{inline(b.c)}</p>;
    case "code": return <pre key={key}><code>{b.s}</code></pre>;
    case "quote": return <blockquote key={key}>{b.c.map(block)}</blockquote>;
    case "hr": return <hr key={key} />;
    case "list": {
      const L = b.ordered ? "ol" : "ul";
      return <L key={key}>{b.items.map((it, i) => <li key={i}>{it.map(block)}</li>)}</L>;
    }
    case "table":
      return (
        <table key={key}>
          <thead><tr>{b.head.map((c, i) => <th key={i}>{inline(c)}</th>)}</tr></thead>
          <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{inline(c)}</td>)}</tr>)}</tbody>
        </table>
      );
  }
}

export function Markdown({ text }: { text: string }) {
  return <div className={styles.md}>{parseBlocks(text).map(block)}</div>;
}
