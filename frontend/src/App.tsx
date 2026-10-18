import { NavLink, Route, Routes } from "react-router-dom";
import styles from "./App.module.css";
import { ConfigPage } from "./pages/ConfigPage";
import { FleetPage } from "./pages/FleetPage";
import { OverviewPage } from "./pages/OverviewPage";
import { RawYamlPage } from "./pages/RawYamlPage";
import { ShipFilePage } from "./pages/ShipFilePage";
import { ShipIndexPage } from "./pages/ShipIndexPage";
import { ShipPage } from "./pages/ShipPage";

// ナビ: 一覧 / 艦隊 / 艦ごと。中身は後続の task で埋める (枠だけ)。
export function App() {
  return (
    <>
      <nav className={styles.nav}>
        <strong>yamato</strong>
        <NavLink to="/" end>一覧</NavLink>
        <NavLink to="/fleet">艦隊</NavLink>
        <NavLink to="/ship">艦ごと</NavLink>
      </nav>
      <main className={styles.main}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/fleet" element={<FleetPage />} />
          <Route path="/ship" element={<ShipIndexPage />} />
          <Route path="/ship/:name/config" element={<ConfigPage />} />
          <Route path="/ship/:name/config/raw" element={<RawYamlPage />} />
          <Route path="/ship/:name" element={<ShipPage />} />
          <Route path="/ship/:name/files/:file" element={<ShipFilePage />} />
          <Route path="*" element={<p>ページが見つからない</p>} />
        </Routes>
      </main>
    </>
  );
}
