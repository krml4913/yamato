import { useEffect, useState } from "react";
import { getOverview, type Overview } from "../api/client";
import { fmtDateTime } from "../overview/format";
import { Legend } from "../overview/Legend";
import { ShipCard } from "../overview/ShipCard";
import { isRunning, stoppedSummary } from "../overview/summary";
import { Waits } from "../overview/Waits";
import status from "../status.module.css";
import styles from "../overview/overview.module.css";

// 一覧を取り直す間隔 (ミリ秒)
export const REFRESH_MS = 60_000;

export function OverviewPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const d = await getOverview();
        if (live) { setData(d); setError(null); }
      } catch (ex) {
        if (live) setError(ex instanceof Error ? ex.message : String(ex));
      }
    };
    void load();
    const t = setInterval(() => void load(), REFRESH_MS);
    return () => { live = false; clearInterval(t); };
  }, []);

  const running = data ? data.ships.filter(isRunning) : [];
  const stopped = data ? data.ships.filter((s) => !isRunning(s)) : [];

  return (
    <>
      <h1>yamato dashboard</h1>
      {error && <p className={status.red}>取得に失敗した: {error}</p>}
      {!data ? (
        !error && <p className={status.dim}>読み込み中</p>
      ) : (
        <>
          <p className={status.dim}>
            最終更新 {fmtDateTime(data.generated_at)} ({REFRESH_MS / 1000} 秒ごとに自動更新・読むだけ)
            {data.agents_error && ` / claude agents を読めなかった (生存は不明): ${data.agents_error}`}
          </p>
          <Waits waits={data.waits} />
          {data.ships.length === 0 && <p className={status.dim}>艦がありません (yamato ship create で作る)</p>}
          {data.ships.length > 0 && <Legend />}
          {running.map((s) => (
            <ShipCard key={s.name} ship={s} now={data.generated_at} waits={data.waits} />
          ))}
          {stopped.length > 0 && <h2 className={styles.groupHead}>止まっている艦 ({stopped.length})</h2>}
          {stopped.map((s) => (
            <details key={s.name} className={styles.stopped}>
              <summary><b>{s.name}</b> <span className={status.dim}>停止 / {stoppedSummary(s, data.generated_at)}</span></summary>
              <ShipCard ship={s} now={data.generated_at} waits={data.waits} />
            </details>
          ))}
        </>
      )}
    </>
  );
}
