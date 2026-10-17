import { Link, useParams } from "react-router-dom";

export function ShipPage() {
  const { name } = useParams();
  return (
    <>
      <h1>艦: {name}</h1>
      <p><Link to={`/ship/${encodeURIComponent(name ?? "")}/config`}>設定</Link></p>
    </>
  );
}
