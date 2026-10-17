import { useEffect, useState } from "react";
import { getOverview, type Overview } from "../api/client";
import { fmtDateTime } from "../overview/format";
import { ShipCard } from "../overview/ShipCard";
import { Waits } from "../overview/Waits";

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

  return (
    <>
      <h1>yamato dashboard</h1>
      {error && <p className="red">取得に失敗した: {error}</p>}
      {!data ? (
        !error && <p className="dim">読み込み中</p>
      ) : (
        <>
          <p className="dim">
            最終更新 {fmtDateTime(data.generated_at)} ({REFRESH_MS / 1000} 秒ごとに自動更新・読むだけ)
            {data.agents_error && ` / claude agents を読めなかった (生存は不明): ${data.agents_error}`}
          </p>
          <Waits waits={data.waits} />
          {data.ships.length === 0 && <p className="dim">艦がありません (yamato ship create で作る)</p>}
          {data.ships.map((s) => (
            <ShipCard key={s.name} ship={s} now={data.generated_at} />
          ))}
        </>
      )}
    </>
  );
}
