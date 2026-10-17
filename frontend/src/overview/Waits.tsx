import type { Wait } from "./types";

export function Waits({ waits }: { waits: Wait[] }) {
  return (
    <section className={`card ${waits.length ? "top" : "quiet"}`}>
      <h2>owner の判断待ち ({waits.length})</h2>
      {waits.length === 0 ? (
        <p className="ok">owner の判断待ちは無い</p>
      ) : (
        <ul>
          {waits.map((w) => (
            <li key={`${w.ship}/${w.id}`}>
              {w.urgent && <span className="tag urgent">急ぎ</span>}
              <b>{w.ship}</b> 判断 {w.id}: {w.title} <span className="dim">({w.age || "?"} 待ち)</span>
              {w.recommendation && (<><br /><span className="dim">推し: {w.recommendation}</span></>)}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
