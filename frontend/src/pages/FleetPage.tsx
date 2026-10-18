import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getFleet, type Fleet } from "../api/client";
import status from "../status.module.css";
import table from "../table.module.css";
import styles from "./FleetPage.module.css";

type Ship = Fleet["ships"][number];

function RunState({ running }: { running: boolean | null }) {
  if (running === null) return <span className={status.dim}>-</span>;
  return running ? <span className={status.ok}>稼働</span> : <span className={status.dim}>停止</span>;
}

// 長いパスは 1 行に省略し、全文は title (と文字列) に残す
function Path({ path }: { path: string }) {
  return <span className={styles.path} title={path}>{path}</span>;
}

function ShipRow({ ship }: { ship: Ship }) {
  return (
    <tr>
      <th scope="row">
        {ship.exists ? <Link to={`/ship/${encodeURIComponent(ship.name)}/config`}>{ship.name}</Link> : ship.name}
      </th>
      <td><Path path={ship.path} /></td>
      <td>{ship.registered ? "ships.json" : "$YAMATO_HOME 直下"}</td>
      <td><RunState running={ship.running} /></td>
      <td>{ship.exists ? <span className={status.ok}>ある</span> : <span className={styles.missing}>フォルダが無い</span>}</td>
      <td>{ship.team ?? "-"}</td>
      <td className={styles.error}>{ship.error ?? ""}</td>
    </tr>
  );
}

export function FleetPage() {
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
                  {fleet.ships.map((s) => <ShipRow key={s.name} ship={s} />)}
                </tbody>
              </table>
            )}
          </section>
          <section aria-label="admiral">
            <h2>admiral</h2>
            {fleet.admiral === null ? (
              <p className={status.dim}>まだ作られていない</p>
            ) : (
              <table className={table.table}>
                <thead>
                  <tr>
                    <th>フォルダ</th>
                    <th>team の名前</th>
                    <th>roles</th>
                    <th>読めなかった理由</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><Path path={fleet.admiral.path} /></td>
                    <td>{fleet.admiral.team ?? "-"}</td>
                    <td>{fleet.admiral.roles.length > 0 ? fleet.admiral.roles.join(", ") : "-"}</td>
                    <td className={styles.error}>{fleet.admiral.error ?? ""}</td>
                  </tr>
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  );
}
