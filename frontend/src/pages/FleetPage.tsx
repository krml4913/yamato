import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getFleet, getOverview, type Fleet } from "../api/client";
import { isRunning } from "../overview/summary";
import status from "../status.module.css";
import table from "../table.module.css";
import styles from "./FleetPage.module.css";

type Ship = Fleet["ships"][number];

// 稼働状況 (true=動いている / false=止まっている / 無い=不明)。名前で引く
type Running = Map<string, boolean>;

function RunState({ running }: { running: boolean | undefined }) {
  if (running === undefined) return <span className={status.dim}>-</span>;
  return running ? <span className={status.ok}>稼働</span> : <span className={status.dim}>停止</span>;
}

function ShipRow({ ship, running }: { ship: Ship; running: boolean | undefined }) {
  return (
    <tr>
      <th scope="row">
        {ship.exists ? <Link to={`/ship/${encodeURIComponent(ship.name)}/config`}>{ship.name}</Link> : ship.name}
      </th>
      <td className={styles.path}>{ship.path}</td>
      <td>{ship.registered ? "ships.json" : "$YAMATO_HOME 直下"}</td>
      <td><RunState running={running} /></td>
      <td>{ship.exists ? <span className={status.ok}>ある</span> : <span className={styles.missing}>フォルダが無い</span>}</td>
      <td>{ship.team ?? "-"}</td>
      <td className={styles.error}>{ship.error ?? ""}</td>
    </tr>
  );
}

export function FleetPage() {
  const [running, setRunning] = useState<Running>(new Map());
  const [result, setResult] = useState<{ fleet?: Fleet; error?: string } | null>(null);

  useEffect(() => {
    let live = true;
    getFleet().then(
      (fleet) => live && setResult({ fleet }),
      (e: Error) => live && setResult({ error: e.message }),
    );
    return () => {
      live = false;
    };
  }, []);

  // 稼働は一覧と同じ読み方 (生きている席が 1 つでもあれば稼働)。取れなければ「-」のまま
  useEffect(() => {
    let live = true;
    getOverview().then(
      (d) => {
        if (!live || !Array.isArray(d.ships)) return;
        setRunning(new Map(d.ships.filter((x) => x.state).map((x) => [x.name, isRunning(x)])));
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, []);

  const fleet = result?.fleet;
  return (
    <>
      <h1>艦隊</h1>
      {result?.error && <p role="alert" className={styles.error}>読み込めない: {result.error}</p>}
      {!result && <p>読み込み中</p>}
      {fleet && (
        <>
          <p className={status.dim}>{fleet.home}</p>
          <section>
            <h2>艦</h2>
            {fleet.ships.length === 0 ? (
              <p className={status.dim}>艦がない</p>
            ) : (
              <table className={table.table}>
                <thead>
                  <tr>
                    <th>名前</th>
                    <th>フォルダ</th>
                    <th>出どころ</th>
                    <th>稼働</th>
                    <th>team.yaml</th>
                    <th>team の名前</th>
                    <th>読めなかった理由</th>
                  </tr>
                </thead>
                <tbody>
                  {fleet.ships.map((s) => <ShipRow key={s.name} ship={s} running={running.get(s.name)} />)}
                </tbody>
              </table>
            )}
          </section>
          <section aria-label="admiral">
            <h2>admiral</h2>
            {fleet.admiral === null ? (
              <p className={status.dim}>まだ作られていない</p>
            ) : (
              <>
                <p className={styles.path}>{fleet.admiral.path}</p>
                <p>team: {fleet.admiral.team ?? "-"}</p>
                <p>roles: {fleet.admiral.roles.length > 0 ? fleet.admiral.roles.join(", ") : "-"}</p>
                {fleet.admiral.error && <p className={styles.error}>{fleet.admiral.error}</p>}
              </>
            )}
          </section>
        </>
      )}
    </>
  );
}
