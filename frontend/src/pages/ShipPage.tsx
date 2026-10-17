import { useParams } from "react-router-dom";

export function ShipPage() {
  const { name } = useParams();
  return <h1>艦: {name}</h1>;
}
