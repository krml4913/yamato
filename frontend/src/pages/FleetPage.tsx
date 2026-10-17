import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getFleet, type Fleet } from "../api/client";
import styles from "./FleetPage.module.css";

type Ship = Fleet["ships"][number];

function ShipRow({ ship }: { ship: Ship }) {
  return (
    <tr>
      <th scope="row">
        {ship.exists ? <Link to={`/ship/${encodeURIComponent(ship.name)}/config`}>{ship.name}</Link> : ship.name}
      </th>
      <td className={styles.path}>{ship.path}</td>
      <td>{ship.registered ? "ships.json" : "$YAMATO_HOME 直下"}</td>
      <td>{ship.exists ? <span className="ok">ある</span> : <span className={styles.missing}>フォルダが無い</span>}</td>
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
          <p className="dim">{fleet.home}</p>
          <section>
            <h2>艦</h2>
            {fleet.ships.length === 0 ? (
              <p className="dim">艦がない</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>名前</th>
                    <th>フォルダ</th>
                    <th>出どころ</th>
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
              <p className="dim">まだ作られていない</p>
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
