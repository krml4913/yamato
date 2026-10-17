import { NavLink, Route, Routes } from "react-router-dom";
import { FleetPage } from "./pages/FleetPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ShipIndexPage } from "./pages/ShipIndexPage";
import { ShipPage } from "./pages/ShipPage";

// ナビ: 一覧 / 艦隊 / 艦ごと。中身は後続の task で埋める (枠だけ)。
export function App() {
  return (
    <>
      <nav className="nav">
        <strong>yamato</strong>
        <NavLink to="/" end>一覧</NavLink>
        <NavLink to="/fleet">艦隊</NavLink>
        <NavLink to="/ship">艦ごと</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="/ship" element={<ShipIndexPage />} />
          <Route path="/ship/:name" element={<ShipPage />} />
          <Route path="*" element={<p>ページが見つからない</p>} />
        </Routes>
      </main>
    </>
  );
}
