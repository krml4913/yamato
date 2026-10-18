import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { editToken, getShipConfig } from "../api/client";
import status from "../status.module.css";
import styles from "./config.module.css";
import { SavedNotice } from "../edit/SaveBar";
import { MdEditor } from "./MdEditor";

/** 生の team.yaml を textarea で直す画面 (--edit のとき)。フォームで扱えない項目の行き先。 */
export function RawYamlPage() {
  const { name = "" } = useParams();
  const [result, setResult] = useState<{ name: string; raw?: string; error?: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [round, setRound] = useState(0);

  useEffect(() => {
    let live = true;
    getShipConfig(name).then(
      (c) => live && setResult({ name, raw: c.raw }),
      (e: Error) => live && setResult({ name, error: e.message }),
    );
    return () => {
      live = false;
    };
  }, [name, round]);

  const current = result?.name === name ? result : null;
  const back = `/ship/${encodeURIComponent(name)}/config`;

  return (
    <>
      <h1>team.yaml を直す: {name}</h1>
      <p><Link to={back}>設定へ戻る</Link></p>
      {!editToken() && <p role="alert" className={styles.error}>書き込みは dashboard --edit で起動したときだけ</p>}
      {current?.error && <p role="alert" className={styles.error}>読み込めない: {current.error}</p>}
      {saved && <SavedNotice />}
      {editToken() && current?.raw !== undefined && (
        <>
          <p className={status.dim}>
            保存前に yaml の構文と team.yaml の検証を通す。エラーは行番号つき。「差分を見る」で確かめてから確定する。
          </p>
          <MdEditor
            key={`${round}`}
            ship={name}
            nowrap
            file="team.yaml"
            initial={current.raw}
            onSaved={() => {
              setSaved(true);
              setRound((n) => n + 1);
            }}
            onCancel={() => {
              window.history.back();
            }}
          />
        </>
      )}
    </>
  );
}
