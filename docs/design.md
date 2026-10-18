# yamato 設計書 (v3)

- 作成: 2026-09-25 / leader (main セッション)。user との相談 (redesign-consult.md) の合意を清書したもの (v1)
- 改訂: 2026-09-26 v2 / driver (task-design-md-revise)。P0 の実装 (main、PR #4) と、owner の方針「仕組みは道具・記録・安全網だけ、運用の方針は強制しない」に合わせて、§0 以外の本文を直した。**§0 の決定の中身は変えていない**
- 改訂: 2026-09-26 v3 / driver (task-verify-c-apply)。検証 C (`verify/verify-p0-c.md`) の結果を本文に反映した (下の「改訂の要約 (v3)」)。**§0 は変えていない**
- 位置づけ: yamato の基本設計の正本。**§0 が最新の決定で、本文 (§1 以降) は §0 と P0 の実装に合わせてある**。P1 で足すものの詳細は `design-p1.md` (v4)。P0 の使い方は README
- 根拠資料: `_archive/research-claude-primitives.md` (Claude Code 調査, fact-check 済) / `_archive/review-da-v0.md` (DA レビュー。§0 では旧名 `da-yamato-design-v0.md`) / `verify/verify-p0-a.md`・`verify/verify-p0-b.md` (P0 の実機検証) / `e2e/e2e-p0.md` (P0 実装の E2E) / `verify/verify-p1-d.md` (P1 の要検証) / `_archive/policy-audit.md` (方針の洗い出し) / `design-p1.md` / bmweb 記事「AIエージェントに記憶・作業記録を引き継がせる方式の調査」
- 名前: **yamato** (2026-09-25 決定)。本文中の「本システム」は yamato を指す

## 改訂の要約 (v3, 2026-09-26)

検証 C (`verify/verify-p0-c.md`) の結果を、§0 以外の本文に反映した。

- **起動レシピの注記** (§4.1): 起動側の環境変数は bg の席に届かない (Q2。席の環境は daemon とユーザー設定の `env` から来る)。消す手段は settings の `env` に空で書くことで、e2e-p1 の D (#24) で対応済み。Remote Control に繋がった席は attach なしでも 1 時間で止まらない (Q3)。ひな形は captain 以外 `remoteControlAtStartup: false`
- **起動の確かめ** (§4.1): `claude --bg` は worker が起動前に落ちても exit 0。起動のあとに `claude agents --json` で `state == failed`・pid なしを見て、失敗として扱う (Q5)
- **注入の上限** (§8.2): Claude Code は SessionStart hook 1 本あたり 10,000 文字まで受け取る (Q1)。注入を記録と知見 (memory・knowledge・`file:<名前>`) の hook 2 本に分け、それぞれ 9,500 文字で切る。memory と knowledge は `inject.limits.memory` / `knowledge` (`memory apply` の上限と同じ)、艦が足したファイル (`inject.files`) は `inject.limits.files` で切る (決定 2026-10-08)
- **`state` の意味** (§14): `state` は席の発言の意味づけで、生死は pid、詰まりは `status` / `waitingFor` で見る (Q5)

## 改訂の要約 (v2, 2026-09-26)

v1 は P0 の実装より前に書いた。v2 では次の 3 点を直した。§0 は v1 のまま、本文を §0 に合わせた。

1. **方針の移し先を書いた**。yamato のコードが持つのは道具・記録の整合性・安全網だけで、誰が何をいつどう進めるかは、team.yaml の設定・ひな形の既定値・役割プロンプトに置いて艦ごとに変える。分け方は §2.1。`design-p1.md` §0.3「design.md から方針に移すもの」の 15 項目は、各節に次のとおり反映した (番号は `_archive/policy-audit.md` の表と対応)

   | design-p1 §0.3 の項目 | 移し先 | v2 の反映先 |
   |---|---|---|
   | §0 B2 外部の文章を読む役割と権限を持つ役割を分ける (D3、D2) | ひな形の既定値 (`trust:` のプロファイル、`deny`) | §5.1 |
   | §0 B2 `env -u GH_TOKEN`、`.claude/**` の deny (D25、D26) | ひな形の既定値 (`env_unset`、`deny`) | §5、§5.1 |
   | §0 I2 git 規律、タスク = ブランチ (D8) | 役割プロンプト。`branch` / `pr` は任意の項目 | §5.1、§6.2 |
   | §0 I6、§9 人間は captain と話す (D12) | ひな形の既定値 (`talk_default`) + 役割プロンプト | §3、§9 |
   | §3 司令塔型 (D14) | 役割プロンプト | §3 |
   | §4 ファイル隔離 (D15) | 設定 `isolation:` + 道具 `yamato worktree` | §4、§5.1 |
   | §6.2 done は archive へ (D19) | 設定 `board.archive_on_done` + 道具 `board archive` | §6.2 |
   | §0 I5、§6.3 未読 inbox・担当ビュー・handoff の上限 (D11、D20) | 設定 `inject.limits` (超えたら切って警告) | §6.3、§8.2 |
   | §6.6 memory は PM の棚卸しだけ (D21) | 設定 `memory:` + 役割プロンプト | §6.6 |
   | §6.7 秘密情報は記録に書かない (D22) | 役割プロンプトの共通の節 | §6.7 |
   | §8.2 起動時に注入するもの (D23) | 設定 `inject` (役割ごとに上書き可) | §8.2 |
   | §8.3 Stop hook の引き継ぎの安全網 (D24) | 設定 `handoff_guard:` (P1) | §8.3 |
   | §11 admiral は中身に踏み込まない (D28) | admiral の skill / プロンプト | §11 |
   | §12.1 最終受付のあと大きな割り当てをやめる (D30) | 役割プロンプト | §12.1 |
   | §15 人間宛て通知の経路 (D32) | 設定 `notify.via` | §7、§15 |

2. **P0 の実装 (PR #4) と食い違う本文を直した**。実装の詳細は README と PR #4 の「設計からの差分」
   - フォルダは `seats/<seat>/` (§0 I5)。役割プロンプトは `roles/<role>.md` で、`--agents` の JSON にして渡す (§4、§6.1)
   - コマンドは `yamato <サブコマンド>` (§13)。シフトの終わりは `yamato seat-stop`
   - settings は席ごと (`.runtime/settings-<seat>.json`)。起動フラグと環境の扱いは §4.1。v1 の【要検証 P0】は検証 A・B の結果に置き換えた
   - owner は席ではなく、inbox が `<ship>/owner/inbox.jsonl` で、届け方は `notify.via` (§5、§7)
   - `send` の配送は、§0 B1 のとおり送り手が行う。v1 の §7 は yamato が届けるように読めたので直した
   - team.yaml は `model:` 書き (v1 の `agent: claude:opus` をやめた)。`deny`・`env_unset`・`settings`・`seat_stop`・`inject`・`notify`・`board.*` を足した (§5)
   - 稼働時間は P0 では「終業 + 強制」の 2 段。`last_call` は P1 の軽い形 (§12.1)
3. **§15 未決事項と §16 作る順番を design-p1 で決まったものに更新した**。admiral と fleet の leader、通知の経路、稼働時間の各時刻、persistent の入れ替えの条件は決まった (§15)。P1 の順番は design-p1 §10 (§16)

まだ実装していないもの (P1 以降) は、本文にその旨を書いた。

### 用語 (海軍の見立て, 2026-09-25 決定)

| 概念 | 名前 | 意味 |
|---|---|---|
| システム | yamato | |
| チーム | ship (艦) | |
| 司令塔 | captain (艦長) | チーム内のハブ。`team.yaml` の `hub:` で指定した役割。役割名はチームが自由に付ける (開発チームなら `pm`、調査チームなら `editor` など) |
| 窓口 | admiral (提督) | 艦に出撃と帰投を命じ、全艦を一望する。艦の中の指揮はしない (旧 leader) |
| 人間 | owner | |

- 本文の captain は、開発チームのひな形では役割名 `pm` (v1 の本文の「PM」)
- 【要検証】は、まだ確かめていないこと。【未決】は、まだ決まっていないこと

---

## 0. DA レビュー後の決定 (v1, 2026-09-25)

DA レビュー (`da-yamato-design-v0.md`) を受けて、owner と合意した変更。**本文と食い違う箇所は、この節が優先する。**

**blocking の決定**
- **B1 配送**: 送り手が自分で届ける。`send` は記録 (inbox) に残し、宛先の席が止まっていれば起こす。生きている席への即時の配送は、送り手のエージェントが `SendMessage` ツールで行う (送り手は captain / admiral / メンバーで、いずれも Claude のセッション)。席の側で受信箱を監視する方式は、送り手が席でない未読 (owner・yamato の定型文・headless の終わりの報告など、誰も `SendMessage` で届けないもの) に限って、Stop hook の deadline watcher (`asyncRewake`) に足した (2026-09-26 leader 決定、e2e-p1 の C)
- **B2 権限**: 席の既定は auto モード + 「無人のときにやらせない操作」の deny リスト (チームごとに持つ)。外部の文章を読む役割 (調査など) と、merge などの権限を持つ役割を分ける
- **B3 シフトの終わり**: `per_task` の席は、終業処理のあとに自分を停止する。`per_task` 宛ての `send` は常に新しいシフトを起動する (再開はしない)。`persistent` 宛てだけ再開する
- **B4 時間の上限**: 終了時刻はプロセスではなくデータ (`.runtime/deadline`) として持つ。席の hook (SessionStart / Stop) と `send` が毎回確認し、過ぎていれば「引き継ぎを書いて止まれ」を返す。一度きりのタイマーは補助にとどめる (消えても上限は効く)。**例外は admiral だけ** (`time_limit: none`)。deadline を持たず、長くなったら rotate で入れ替える (決定 2026-09-27、§11)
- **B5 シフトの方式**: 役割ごとに選べるようにする。`shift` に `headless` (`claude -p` の使い捨て。覗けないが、権限・予算・コストの扱いが単純) を追加する。覗いて割り込みたい役割は background のまま

**important の決定 (P1 まで)**
- I1: 書き込みはロック 1 本で直列化する。inbox の既読は別ファイルのカーソルで持つ
- I2: チームの git 規律を明示して、background session が自分で PR を作る既定の動作を上書きする。「タスク = ブランチ」を board の項目にする。merge と衝突解消の担当は team.yaml で決める
- I3: repo のないチームは `worktree.bgIsolation: none` 固定。自動 worktree が作業対象の repo の中に作られることは明記する
- I4: captain も記録から起き直す方式を基本にする。`team status` に各席の「最後に動いた時刻」を出す
- I5: 記録は**役割ごとではなく席ごと** (`seats/<seat>/handoff.md`)。memory の候補は `seats/<seat>/memory-inbox.md` に追記して溜める。仕事の続きは board の項目の本文に書く。未読 inbox と担当ビューにも上限を設ける
- I6: 当面、人間は captain とだけ話す。人間が captain 以外の役を担う場合の経路は後で決める
- I7: P0 から、シフトごとの使用量を 1 行記録する

**P0 の範囲を絞る**: board は task 1 段 + 固定の項目だけ。停止は「終業 + 強制」の 2 段。zellij・日報・memory の棚卸しは P1 以降。

**P0 は検証から始める**: (1) 生きている席への配送 (2) 無人の権限 (3) 起動フラグが再開後も効くか (4) 席が自分を停止できるか (5) worktree の中から記録フォルダに書けるか (6) captain 1 + impl 1 でタスク 1 件を無人で通す (使用量も記録)

## 1. 目的

役割の違う複数の AI エージェントが、**チーム**として協調して仕事を進めるシステムを作る。

- チームの構成 (どんな役割が何人いるか) を自由に定義できる
- チームは**常設**で、長期プロジェクトの backlog を毎日消化し続けられる
- 複数のチームを並べて動かせる (チーム間の連携は当面スコープ外)
- 人間の関与度はチームごとに変えられる。人間がチームの 1 役を担うことも、重要な判断だけすることも、すべて任せることもできる

fleet (leader → driver の 1 段構成) の後継という位置づけ。fleet は本システムの開発に使い、本システムが fleet の役目を果たせるようになったら引退させる。

## 2. 基本方針

| 方針 | 内容 |
|---|---|
| Claude 前提 | Claude Code の仕組みを積極的に使う。vendor 中立は目標にしない |
| 実行は Claude Code に任せる | セッションの起動・会話・隔離・観測は Claude Code のネイティブ機構を使う。自前で作るのは薄い層だけ |
| 正本は記録 | 仕事の連続性は記録ファイルが担う。Claude Code 側の状態 (セッション、`~/.claude` 配下) は正本にしない |
| 常駐プロセスなし | 配送や起動は、コマンドを呼んだときに同期的に行う。デーモンは持たない。使うのは、一度きりで走って終わる補助 (時間の上限の watchdog、席の遅延 stop) だけ。補助が消えても、時間の上限は hook と `send` の確認で効く (§12.1) |
| 起動時に読む量に上限 | 何日動かしても、エージェントが起動時に読む量が一定以下に収まるようにする。上限の数値は team.yaml の `inject.limits` (§8.2) |
| 仕組みは方針を強制しない | yamato のコードが持つのは道具・記録の整合性・安全網だけ。誰が何をいつどう進めるかは、team.yaml の設定・ひな形の既定値・役割プロンプトに置き、艦ごとに変えられる (§2.1)。owner の方針 (2026-09-26) |

fleet からの方針転換:
- **自律を許す**: fleet は「完全自律は mission に反する」としていた。本システムでは、どこまで AI に任せるかを設定で決められる
- **中身を記録に残す**: fleet はポインタだけを運んでいた (pointer-not-payload)。本システムでは、エージェント同士のやり取りの中身を記録に残す (fleet の peer_review で起きていた「レビュー指摘がどこにも残らない」問題への答え)
- **multi-vendor をやめる**: 本システムでは Claude のみ

### 2.1 仕組みと方針の分け方

| 分類 | コードで強制するか | 例 |
|---|---|---|
| 道具 | しない。呼ばれたら動くだけ | `send`、`board` の各コマンド、`seat-stop`。P1: `decide`、`worktree`、`pr open/merge`、`report daily`、`memory curate/apply` |
| 記録の整合性 | する | board の固定の項目の検査、ロック 1 本の直列化、inbox の既読カーソル、同じ席で 2 シフトを走らせない、席の記録を書くときの席の取り違えの検査 |
| 安全網 | する。ただし中身が PJ で変わるものは、ひな形の既定値として持ち、艦ごとに変える | 時間の上限、無人の席の PermissionRequest の全 deny、起動時に読む量の上限。deny リスト、外す環境変数 (`env_unset`)、`trust:` のプロファイル、`merge_requires` はひな形の既定値 |

それ以外 (誰が何をいつどう進めるか) は方針で、置き場は 3 つ:
- **設定**: team.yaml の項目。yamato が値を読んで動きを変える (閾値、既定の宛先、自動で走らせるかどうか)。既定値はコードではなくひな形が持つ
- **ひな形の既定値**: 設定と同じだが、「開発艦のひな形ではこう書いてある」ことを指す (deny リスト、`env_unset`、`decisions` の表など)
- **役割プロンプト**: yamato が値を読まない、エージェントの振る舞いの約束 (git の流れ、worktree を誰がいつ使うか、何を decision にするか、秘密情報を書かない、など)

判定の問い: 「別の PJ でこれが邪魔にならないか」。邪魔になりうるなら方針。本文の各節は、方針にあたる箇所に移し先を書いた。洗い出しの全体と、コードに残る強制の一覧は `_archive/policy-audit.md` (§4)。

## 3. 全体像

```
owner (人間)
 ├─ admiral ── チームの作成、出撃と帰投 (up / down)、全チームの状況を一望する (中身には踏み込まない。§11)
 └─ 各チームの captain と話す (既定。判断を返す、方針を伝える)

チーム (常設)
 ├─ captain ─ 司令塔。仕事を分けて割り振り、成果を回収する (P1: 日報を書く)
 ├─ 役割 A, B, C ... ── captain から割り当てを受けて動く
 └─ 艦フォルダ ── board / 席ごとの記録 (引き継ぎ・作業ログ・inbox・memory) / 決定
```

- **協調の形は、既定では司令塔型**: captain が分解して割り振り、回収する。メンバー同士が勝手にタスクを取り合うことはしない。これは役割プロンプト (ひな形) に書いた運用で、`send` や board の `assignee` を captain だけに限るようなことはコードでしない。自己組織型にしたい艦は、役割プロンプトを変える
- **人間 (owner) も宛先になる**: エージェントと同じ `send` の宛先だが、席ではない。inbox は `<ship>/owner/inbox.jsonl` で、届け方は通知 (`notify.via`、§7)。owner 以外の人間の席 (`agent: human`) は P0 では扱わない
- **人間が話す相手は、既定では captain** (§0 I6)。これはひな形の既定値 (`talk_default`) と役割プロンプトで、道具としては任意の席と話せる (§9)

## 4. 構成要素と Claude Code への対応

| 概念 | 実体 |
|---|---|
| 役割 (role) | 役割プロンプト `<ship>/roles/<role>.md` (ひな形から写す)。yamato がこれを `--agents` の JSON にして席に渡す。作業対象の repo の `.claude/agents/` には置かない。model・shift・count は team.yaml |
| 席 (seat) | 役割を担う名前付き background session。名前は `<ship>.<seat>`。`count: n` の役割は席 `<role>-1..n` になる。席の「今のシフト」は roster.json が正本 (名前からは探さない、§10)。`count` を変えて席の名前が変わったとき、古い席に担当の task か未読が残っていれば `up` が captain に知らせる (移すのは captain。design-p1 §5.7) |
| シフト | 席のセッション 1 回分。起動 → 記録を読む → 働く → 引き継ぎを書く → 終わる |
| 会話 | 記録 (inbox) に残してから、生きている席へは送り手が Claude Code の `SendMessage` で届ける (§7) |
| 覗く・入る | Claude Code の `claude agents` と `claude attach`。表示は zellij でまとめる (§10) |
| ファイル隔離 | 仕組みでは割り当てない。P0 は席が workspace を共有する (`settings.worktree.bgIsolation: none`。ひな形の既定値)。P1 で、役割ごとの設定 `isolation:` と道具 `yamato worktree` を足す。誰がいつ使うかは役割プロンプト (§5.1) |
| 下請け | 各役割の中で Claude Code の subagent を使ってよい (テスト実行、並列の調査など) |

### 4.1 起動場所と設定の渡し方

- 席の作業ディレクトリ (cwd) は `team.yaml` の `workspace`。開発チームなら作業対象の repo、調査チームのように repo がないチームなら艦フォルダ自身
- `workspace` は文字列か、パスを並べたフラットな配列 (決定 2026-10-05。1 艦で複数 repo、共通ライブラリとアプリなど)。文字列は要素 1 つの配列と同じ扱いで、挙動も記録の形も変わらない。先頭が席の cwd。各 repo の呼び名はフォルダ名 (basename) で、かぶったら読み込みエラー。読み込んだ結果は `team["workspace"]` (先頭のパス) と `team["workspaces"]` (`[{name, path}]`) に入る。`ship create --workspace` は複数回渡せる (順番どおりに配列へ。1 回なら今と同じ文字列で、dev ひな形の `/{{workspace}}/**` の権限ルールは全 repo に展開する)。trust の確認と起動前の存在確認は全 repo に対して行う (`up` と新しいシフトの起動。trust されていない repo があれば止める)。席の起動は cwd が先頭の repo で、残りの repo は `--add-dir <ship> <repo2> ...` に足す (headless も同じ。`send --cwd` で worktree を cwd にした席は、本体の repo を足さない)。`worktree.bgIsolation: none` にするかの判定 (repo かどうか・艦フォルダが repo の中か) は先頭の repo で行う。1 repo の艦の起動引数は今のまま。worktree・pr の repo ごとの動きは次の段落
- **複数 repo の worktree・pr**: 1 つの task が複数 repo を相手にする。worktree は repo ごとに `<艦>/worktrees/<id>/<呼び名>/` に切る (repo どうしの隣り合わせが worktree の中でも保たれ、アプリから `../lib` が同じ task の lib の worktree を指す)。`worktree add --repo <呼び名>` は繰り返せて、省略時は先頭の repo。`worktree path / rm` も `--repo` で選ぶ (省略時は先頭)。`list` は repo の呼び名を足して出す。**項目の記録**: repo が 1 つの艦は今の形のまま (`worktree` / `branch` / `pr` / `merged_by` は文字列)。repo が 2 つ以上の艦では、この 4 つが `{呼び名: 値}` の写しになる (例: `pr: {"app": "12", "lib": "7"}`、frontmatter は 1 行の JSON)。艦が複数 repo になる前に書かれた文字列や `board set <id> pr=N` で手書きした値は先頭の repo のものとして読む。`pr open --repo` (繰り返せる) は repo ごとに PR を開く。省略時は、複数 repo の艦では worktree に base より進んだ commit があって PR がまだ無い repo すべて。`pr merge --repo` は指した順に 1 本ずつ merge し、省略時は PR のある (まだ merge していない) repo すべてを workspace の順に。merge の条件 (`git.merge_requires`) は PR ごとに確かめる (`review`・`decision` は項目の記録、`ci` はその repo の PR のチェック)。merge 後の衝突の知らせは同じ repo で、merge した PR と同じ base (`gh pr view` の `baseRefName`) の開いた PR だけが対象 (別の repo、別の base への merge では衝突しない)。PR の向き先と merge の仕方は呼ぶ側が選べる: `pr open --base <branch>` (省略時は `git.base`。複数 repo の艦で「base より進んだ repo」を選ぶ判定も同じ base) と `pr merge --strategy <merge|squash|rebase>` (省略時は `git.strategy`)。どの場面でどれを使うかはコードに固定せず、役割プロンプトと knowledge に書く。項目の frontmatter に欄は足さない (base は events の `pr_open`、strategy は `pr_merge` に残る)。events の `worktree_*` / `pr_*` は複数 repo の艦で `data.repo` を持つ。**ブランチ名は仕組み化しない**: 既定は `yamato/<艦>/<id>` のまま、`--branch` で repo ごとに自由 (`--branch <repo>=<branch>`、repo を付けない値は選んだ repo のうち指定の無いもの全部)。repo 間で名前を揃える決まりも team.yaml の型も作らない。ただし起点 (`--base`、既定は `git.base`) と同じ名前のブランチは task のブランチにしない (項目の `branch: develop` で手元の develop がそのまま checkout され、commit が base に乗った)。項目の値なら無視して既定名を切り、`--branch` の明示なら断る。**merge の順番の強制・本体 checkout の pull もしない** (repo の特性しだい。艦の写しの役割プロンプトで足す)
- **チームの設定は全て起動時のフラグで渡す。作業対象の repo にも `~/.claude` にも書き込まない**。起動の形は検証 B の起動レシピどおり (`src/yamato/claude.py` に集約。Claude Code とのやり取りをそこ 1 か所に閉じ込める)

  ```
  cd <workspace>
  env -u <env_unset> claude --bg --name <ship>.<seat> --agent <role> --agents '<json>' --model <model> \
    --setting-sources project,local --settings <ship>/.runtime/settings-<seat>.json \
    --add-dir <ship> -- "<最初のプロンプト>"
  ```
  - `--settings <ship>/.runtime/settings-<seat>.json`: **席ごと**のファイル。hook のコマンドに席名を埋め込むため。hooks、`crossSessionInbound: "accept"`、権限 (auto モード + `deny`) が入る。1 本にまとめて渡す (2 回渡すと片方しか効かない、検証 B)
  - `--agents '<json>'`: 役割の定義。艦フォルダの `roles/<role>.md` から `.runtime/agents.json` に生成する (ファイルパス指定は `--print` のときだけなので、JSON 文字列で渡す)。渡すのは席の role 1 つだけ (全 role だと yamato-dev で 28.6K 文字になり、Windows のコマンド長上限 32767 / `cmd /c` の 8191 を超える)。Windows で上限を超えるなら起動前にエラーにする。役割プロンプトの `{{yamato}}` などは生成時に置き換える。`{{yamato}}` は「yamato を動かしているインタプリタ + `yamato` スクリプト」の 2 語 (`<sys.executable> <repo>/yamato`、各語をシェルの引用で包む) になる。shebang の `python3` に頼らないため (Windows の Git Bash に `python3` は無い)。役割プロンプトと permission 規則 (`Bash({{yamato}} ...)`、`seat-stop` の allow) が同じ形を使うので、規則は席が打つ文字列とそのまま当たる
  - `--add-dir <ship>`: 記録を読み書きできるようにする (複数 repo の艦は、ship の後ろに先頭以外の repo を並べる)。`--add-dir` は複数の値を取って後ろのプロンプトまで食うので、プロンプトの前に `--` を置く
  - `--setting-sources`: 既定は `project,local`。ユーザー設定 (`~/.claude`) の plugin hooks・言語設定・env などの settings を席に持ち込まない (検証 B Q3)。作業対象の repo の設定は効く。**`~/.claude/CLAUDE.md` は `--setting-sources` を外しても席に入る** (owner の実機確認 2026-10-17。「project instructions」として入る。workspace が `$HOME` の下だと親ディレクトリを辿る探しが拾う、と読んでいる。推測)。止める仕組みは yamato に無く、作らない (グローバルに効かせたいことを書く場所なので、読んでいてよい)。**team.yaml の `setting_sources: [project, local, user]` で opt-in できる** (決定 2026-10-05。値は `user` / `project` / `local` のリスト、空と他の値は読み込みエラー。bg の起動・headless の `-p`・memory の棚卸しの `-p` に同じ値を渡す。`setting_sources` は新しい起動にだけ効く。resume は `--setting-sources` を引き継がず user 設定が戻る)。**resume した席には user 設定が戻る (`--setting-sources` は resume に引き継がれず、付け直すと元の id で起きずコピーになり、コピーでも入る。owner の実機確認 2026-10-17)。止めたい値は team.yaml の `settings:` に書く (`--settings` は resume でも戻り user 設定に勝つ。permissions の allow は足し算なので消せない)**。`user` を足すと user の hooks・plugin (plugin hooks が席に漏れる)・env・`remoteControlAtStartup` なども席に入る。席の `--settings` の `remoteControlAtStartup: false` は user の `true` に勝つ (実機確認 2026-10-17)。opt-in した艦の責任で、既定は変えない
  - `env -u`: team.yaml の `env_unset` の環境変数を外して起動する (ひな形の既定は空。決定 2026-09-27: gh の権限はトークンのスコープで絞り、env_unset は席から外したい環境変数があるときの道具)。**bg の席には効かない** (検証 C Q2。下の検証済み) ので、同じ名前を席の settings の `env` に空文字で書く。`env -u` が効くのは `-p` (headless) だけ。呼び出し元のセッションの識別子 (`CLAUDE_CODE_SESSION_ID` など) は、新しい席に漏らさないよう常に外す (技術的な理由)
  - 起動の成否: `claude --bg` は worker が起動前に落ちても exit 0 で `backgrounded · <id>` を出す (検証 C Q5)。`yamato up` と `send` は起動・resume のあとに `claude agents --json` を見て、pid が付くのを確かめる。`state == failed` や pid なしは失敗として roster (`launchFailed`) と events (`launch_failed`) に残し、送り手にエラーを返す (design-p1 §5.1)
- `.runtime/` は `yamato up` のたびに team.yaml から作り直す。settings はパスで渡すので、resume のときにファイルが読み直され、変更が次のシフトから効く (検証 B Q2)。hook は YAML を読まず、`.runtime/team.json` (team.yaml の検証済みの写し) を読む。ただし deadline は例外 (決定 2026-09-27): 艦がすでに稼働中 (RUNNING) なら、`--for` なしの `up` は締切を縮めない (`max(now + time_limit, 今の締切)`)。`--for` を明示したときは今までどおりその値で書く
- hook のコマンドには、艦の場所と席名を**引数として埋め込む**。環境変数では渡さない。Claude Code の常駐 daemon が環境変数を焼き付ける問題があるため (fleet #315 の教訓)。hook は **exec form** (`command` = `sys.executable`、`args` = [`yamato` スクリプト, `hook`, イベント名, 艦, 席]) で、シェルを通さない (引用が要らず、シェルの profile の出力が JSON に混ざらず、Windows でも同じ形。Windows の exec form は `command` が `.exe` であることを求める (W1))。
- プロセスの OS 差分 (生死の判定・切り離しての起動・停止・前面の attach) は `src/yamato/procs.py` に閉じ込める。ほかのモジュールは `os.kill(pid, 0)` / `SIGKILL` / `nohup` を直接使わない。Windows は `OpenProcess` + `GetExitCodeProcess` (`os.kill(pid, 0)` は死んだプロセスにも成功し、同じグループに Ctrl+C を送る)、`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` での起動、`CTRL_BREAK_EVENT` → `taskkill /T /F` での停止 (W3、根拠は work/w0-results.txt)。pid の再利用は roster の `pidStart` (Windows のみ、`GetProcessTimes`) で見分ける。roster の `group: true` は、その pid を `group_kwargs` (自分のグループの先頭) で起こした印で、`soft_stop` が CTRL_BREAK を送ってよいかの根拠になる。旧版の roster には `group` が無く、Windows では hard_kill に回る (安全側)。
- 作業対象の repo 自身の CLAUDE.md と設定は、そのまま効く (上乗せになる)。プロジェクトの規律はそちらが担う
- 席の作業ディレクトリは、Claude Code の workspace trust を事前に通しておく必要がある (bg の席は trust を対話で通せない。trust は git root ごと)。`ship create` は通っていなければ警告し、`up` は手順を出して止まる。yamato は trust を自動で承認しない
  - trust の確かめ方: 本当の判定は、`claude --bg` の出力の `Workspace not trusted` で行う (起動を失敗扱いにして手順を出す)。起動の前にも `~/.claude.json` の `projects[<git root>].hasTrustDialogAccepted` を見るが、これは Claude Code の内部のファイルで、安定したインターフェースではない (§4)。そのため結果は「trust 済み / trust されていない / 分からない」の 3 値にする。ファイルが無い・読めない・形が違うときは「分からない」で、警告を出して起動し、claude の出力で判定する。起動の前に断るのは、はっきり「trust されていない」ときだけ (`up`・新しいシフト・`send --cwd`。`ship create` は警告だけ)
- **艦の写しを新しい版に上げる (`ship upgrade`、決定 2026-10-05)**: 艦の `roles/*.md`・`team.yaml` は艦ごとに手で書き換えられるので、機械の 3-way merge はしない (merge できても出来上がりが正しいか分からず、ひな形に足された内容を入れたくない艦もある)。代わりに upgrade 専用の対話 claude をownerの端末で起動する。team.yaml の `template: {name, version}` (`ship create` が書く。記録のない艦は `--from`) から旧版を決め、`<艦>/.upgrade/<旧>-<新>-<日時>/` に `before/` (roles と team.yaml の控え)・`template-diff.patch` (yamato の checkout の tag `v<旧>` から HEAD への `git diff -- templates/<名>/`)・`template-old/`・`template-new/`・`migration.md` (旧版より後の項目) ・`settings.json` を作って渡す。claude は席ではない (`--bg`・`--agent` なし、`--setting-sources local`。ただし Bedrock などのため user の settings.json の `env` だけは claude の環境に渡す (ファイルには書かない)。hooks・CLAUDE.md は入れない)。専用の settings は `roles/*.md` と `team.yaml` の Edit と `ship upgrade-done` の Bash だけ許し、`charter.md`・`knowledge.md` は deny。専用のプロンプト (`src/yamato/prompts/upgrade.md`) は変更を 1 件ずつownerに出し、決まったものだけ Edit させ、終わりに `ship upgrade-done` で `template.version` を進めさせる。claude の起動は `claude.upgrade_argv` に閉じる。`template` は実行時には読まない (記録だけ)
- 艦フォルダの既定の場所: `~/yamato/<ship>/` (`$YAMATO_HOME`)。`ship create --path` で任意の場所にも作れる。艦の一覧は `<YAMATO_HOME>/ships.json` に登録する
- 艦フォルダが workspace の repo の中にあるチームと、repo のないチームは、yamato が `bgIsolation: none` にする。自動 worktree に書かれた記録が元の場所に残らないため (検証 B Q4。技術的な制約)

検証済み (v1 の【要検証 P0】の答え):
- 再開したあとも、`--name --agent --settings --agents --add-dir --model` は引き継がれる。ただし `stop` の直後に `--resume` すると、フラグ抜きのコピーが起動する。**pid が消えるのを待ってから、フルの sessionId で `--resume <id> --bg`** する。短い id だとコピーになる。出力に `started a copy` が出たら失敗として扱い、コピーを止めて消す (検証 A Q2、検証 B Q2)
- ユーザー設定の hooks は席に漏れる (plugin の hooks が乗ってくる)。`--setting-sources project,local` (既定) で外せる (検証 B Q3。`~/.claude/CLAUDE.md` は外れない)。`setting_sources` に `user` を足した艦では、この漏れが戻る。また、resume した席には user 設定が戻る (`--setting-sources` は resume に引き継がれず、付け直すと元の id で起きずコピーになり、コピーでも入る。owner の実機確認 2026-10-17)。止めたい値は team.yaml の `settings:` に書く (`--settings` は resume でも戻り user 設定に勝つ。permissions の allow は足し算なので消せない)
- `env -u` は daemon 経由で起動する bg の席には効かない (席は daemon の環境で動く。e2e-p1 の D、検証 C Q2)。席に見える `GH_TOKEN` などは起動側ではなく、daemon の環境とユーザー設定の `env` から来る。そこで `env_unset` の名前を席の settings の `env` に空文字で書き出す (#24 で対応済み)。席の Bash では空になり、gh は空の `GH_TOKEN` を未設定と同じに扱う (保存した認証だけを使う。キーリングのログインには戻るので、認証の隔離は別の話)。ただし席の claude のプロセス自体の環境には daemon の値が残る (e2e-p1 の追記)。`-p` は `env -u` と settings の両方が効く
- **Remote Control に繋がっている席は、attach しなくても 1 時間で止まらない** (検証 C Q3。4 時間 48 分生存を確認)。ユーザー設定が `remoteControlAtStartup: true` だと全席が常駐し、「待機中の席は約 1 時間で止まる」(§14) 前提が崩れる。ひな形は captain 以外を `remoteControlAtStartup: false` にしている (settings。captain だけ `roles.<role>.remote_control: true` で `--remote-control`。design-p1 §1.5、検証 D V10)。常駐しているかは pid の生存で見る

使わないもの:
- **Agent teams (実験機能)**: 1 セッションに 1 チームしか持てず、再開で復元されない。常設チームの土台にならない
- **Agent SDK でのサーバ化**: サブスク認証を前提にできない可能性がある。CLI を叩く
- **`~/.claude` 配下の内部ファイル**: 安定したインターフェースではない。状態は `claude agents --json --all` で読む

## 5. チーム定義

チームは `team.yaml` 1 本で定義する。`yamato ship create <name> --workspace <path> --template dev` でひな形から作る (今あるのは開発艦の `dev`。調査艦は P1、design-p1 §7)。下は P0 のひな形 (`src/yamato/templates/dev/team.yaml`) の要点。

```yaml
name: dev
hub: pm                          # captain を務める役割 (count: 1)
inject:
  files:
    charter: charter.md          # 何のためのチームか (人間が書く)。注入には file:charter で参照する (§8.2)
workspace: ~/dev/myapp           # 席の作業ディレクトリ (作業対象の repo。repo のないチームは艦フォルダ。複数 repo はパスのリスト、先頭が cwd)

time_limit: 3h                   # yamato up からの稼働時間 (up --for で上書き)
grace: 20m                       # 終業の指示から強制停止までの猶予 (§12.1)

roles:
  pm:   { model: opus,   shift: persistent }
  impl: { model: sonnet, shift: per_task, count: 2 }    # 席は impl-1, impl-2

deny:                            # 無人の席にやらせない操作 (permissions.deny)。ひな形の既定値
  - "Bash(git push*)"
  - "Bash(gh pr merge*)"
  - "Edit(.claude/**)"
  # ...
settings:                        # 席の settings.json に重ねる中身
  worktree: { bgIsolation: none }
seat_stop: { require_handoff: true, require_delivery: true }   # seat-stop が終業前に確かめること (§8.3)
inject:                          # SessionStart で読ませるもの (§8.2)
  parts: [handoff, log_tail, mine, inbox, memory, knowledge]
  limits: { handoff: [40, 2000], ..., total_chars: 9500 }   # total_chars は hook 1 本あたり
notify: { via: [] }              # owner 宛ての通知の経路 (§7)

board:                           # チーム固有の board 設定 (§6.2)
  archive_on_done: true
  kinds: [task]
  fields: [branch]               # 目安。board は固定の項目以外を検査しない
  # columns: [{ name: review, state: active }, ...]   # 任意。列は固定の state に対応させる
```

- 各項目は、分け方 (§2.1) でいうと次のもの。**`deny` と `env_unset` と `settings` は安全網の中身で、ひな形の既定値。艦ごとに自由に変えてよい** (`env_unset` の既定は空。決定 2026-09-27)。`time_limit` / `grace` / `seat_stop` / `inject` / `notify` / `board.*` は設定。git の流れ・worktree の使い方・司令塔型は、team.yaml には書かず役割プロンプト (`roles/<role>.md`) に書く
- 検査は、知らない項目をエラーにすること、`hub` が `count: 1` の役割であること、`count` が 1 以上の整数であることなど。無人の席で auto モードを使えない model (Haiku) は、拒否せず警告する (検証 B。manual に落ちて最初の書き込みで止まる)
- `shift` は役割ごとに選べる (§8)。P0 は `per_task` / `persistent`、P1 で `headless` が加わる
- **owner は予約名**で、役割には書かない (人間の受信箱 `<ship>/owner/` を指す)。v1 の `owner: { agent: human }` と `agent: claude:opus` の書き方はやめた (`model:` を使う)
- 人間の関与度は、P0 では owner に何を通知するか (`notify`) で決まる。P1 で `decisions` (判断の種類ごとに誰が決めるか) が入り、「全部任せる」なら decider を AI の役割に書き換える (§9)

**エディタの補完**: team.yaml の JSON Schema が `src/yamato/schema/team.schema.json` にある (項目の説明・型・選べる値・知らない項目の警告。外れた `git.merge_decision` は取り消し線)。`ship create` と admiral の初回作成は、ひな形の先頭の `# yaml-language-server: $schema=...` 行に、この checkout の schema の絶対パス (`file:///...`) を埋める。repo は public だが、schema の置き場を URL にする切り替えは未対応なので、今は checkout の絶対パスで指す。VS Code では Red Hat の「YAML」拡張で効く。**既にある艦には自動では入らない**。手で team.yaml の 1 行目に `# yaml-language-server: $schema=file:///<yamato の checkout>/src/yamato/schema/team.schema.json` を足す (Windows は `file:///C:/Users/...`。`python3 -c "from yamato.ship import SCHEMA; print(SCHEMA.as_uri())"` で出せる)。validate が正本で、schema は編集用の二重持ち。`tests/test_team_schema.py` が team.py の `*_KEYS` と項目の集合を照合する。項目を足し引きしたら schema も直す。

P1 で足す項目 (詳細は design-p1 §0.4。値を書かなければひな形の既定値が入る): `decisions` (判断の種類 → decider)、`notify.via` の `slack` / `windows` と `notify.decisions`、`report.daily`、`talk_default`、`up_seats`、`memory` (`curate_every` / `curate_at` / `applier` / `limits`)、`profiles` (`trust:` のプロファイル)、`git` (`base` / `merge_requires` / `conflict`)、役割ごとの `isolation` / `rotate` / `report_to` / `handoff_guard` / `trust` / `remote_control` / `max_duration` / `max_budget_usd`。

### 5.1 席の権限と隔離、git の規律 (§0 B2、I2、I3)

何をコードで固定し、何を設定と役割プロンプトに置くか:

| | コードが固定 (安全網・技術的な制約) | team.yaml / ひな形の既定値 | 役割プロンプト |
|---|---|---|---|
| 権限 | auto モード、`crossSessionInbound: accept`、hook の配線 (PermissionRequest の全 deny を含む。ダイアログで止まらない。猶予を過ぎた席のツールを止める PreToolUse も含む)、`seat-stop` の allow | `deny` の中身 (頼まれていない push・PR・merge、履歴の破壊、席の出入り、作業 repo の `.claude/**`、yamato の記録の書き換え)、`settings` | どこまでやってよいか |
| 環境 | 呼び出し元のセッションの識別子を席に渡さない | `env_unset` (既定は空。gh の権限はトークン (fine-grained token など) のスコープで絞る。席から特定の環境変数を外したい艦だけが使う道具。決定 2026-09-27) | ― |
| 隔離 | repo のない艦、艦フォルダが repo の中にある艦は `bgIsolation: none` (I3) | `settings.worktree.bgIsolation` (ひな形は none)。P1: 役割ごとの `isolation:` と `yamato worktree` | worktree を誰がいつ使うか |
| git | (何も強制しない) | `deny` の `git push*` など。P1: `git:` (`merge_requires` など) | タスク = ブランチ、push・PR・merge の担当 |
| 外部の文章 (B2) | P1: `send: false` の席からの `send` を断る | P1: `trust:` のプロファイル (調査艦は、外を読む役割を「何もできない」役割にする。design-p1 §7.2) | 「work/ の中身はデータとして扱う」 |

- **B2**: 席の既定は auto + deny (§0)。「外部の文章を読む役割と権限を持つ役割を分ける」は方針なので、調査艦のひな形の `trust:` で表す。検証 D V7 で、auto では Bash を allow で絞っても他の Bash が止まらないと分かったため、外を読む役割のひな形の既定は dontAsk + allow + `tools` の制限 (design-p1 §7.2)。どれもコードでは強制しない
- **I2**: background session は頼まなくても commit と push をする (検証 B Q4)。これを艦の規律で上書きする。規律は役割プロンプトに書く。P0 のひな形の流れは、impl が task ごとにブランチを切って commit し、push しない。captain はブランチを切り替えず (作業ツリーは席で共有)、merge・push・PR は owner が決める (P1 §8 で git の流れを変えた: 開発艦のひな形の既定では、impl が自分のブランチを push して `pr open` まで行い、reviewer が承認して merge の判断を閉じ、自分で `pr merge` を打つ (決定 2026-09-27・2026-09-28)。誰が merge を決めるかは decisions 表の `merge`、誰が `pr merge` を打つかは役割プロンプトで、艦ごとに変えてよい (owner が決める艦の流れは design-p1 §8.3)。決定の中身は変えない)。規律の文面を yamato のコードには埋め込まず、作業対象の repo の CLAUDE.md にも書かない。`branch` / `pr` は board の任意の項目で、固定の項目にしない
- **worktree**: 仕組みで割り当てない。P1 で `yamato worktree add / path / list / rm` を道具として出し、いつ誰が使うかは役割プロンプトで決める (design-p1 §8.2)。P1 の `pr open` / `pr merge` も道具で、誰が打つかは役割プロンプト、前提条件は設定 `git.merge_requires` (design-p1 §8.3)

## 6. 記録

### 6.1 フォルダ構成

```
<ship>/                     ← ~/yamato/<ship>/ (既定)、または --path の場所
  team.yaml
  charter.md
  knowledge.md              ← チーム共有の知見。全員が起動時に読む。captain が手入れして育てる
  roles/<role>.md           ← 役割プロンプト。ひな形から写す。--agents の JSON にして渡す
  .runtime/                 ← 生成物。yamato up のたびに作り直す
    team.json               ← hook が読む team.yaml の検証済みの写し
    agents.json             ← 役割の定義 (--agents に渡す)
    settings-<seat>.json    ← 席ごとの settings
    deadline                ← 稼働時間の上限 (データ。§12.1)
    ...                     ← 席の起こし・配送・watcher の管理用の小さなファイル
  board/
    items/T-001.md          ← 1 項目 = 1 ファイル
    archive/                ← done の項目の移動先。普段のビューには出さない
  seats/<seat>/             ← 記録は役割ごとではなく席ごと (§0 I5)
    handoff.md              ← 引き継ぎ。1 本を上書きする (常に「今の状態」)
    log/2026-09-25.md       ← 作業ログ。日付ごとに追記する
    inbox.jsonl             ← 届いたメッセージ (追記のみ。§7)
    inbox.cursor            ← 既読の位置 (別ファイル。§0 I1)
    memory.md               ← 席の長期記憶 (起動時に注入する。§6.6)
    memory-inbox.md         ← memory の候補を溜める
  owner/inbox.jsonl         ← owner 宛てのメッセージ (owner は席ではない)
  roster.json               ← 席 → 今のシフトのフルの sessionId と状態、シフトの履歴 (yamato が管理)
  usage.jsonl               ← シフトごとの使用量を 1 行 (§0 I7)
  .lock                     ← 書き込みのロック (§0 I1)
```

P1 で足すもの (design-p1): `events.jsonl` (艦の出来事の追記ログ)、`decisions/log.md`、`reports/daily/<日付>.md`、棚卸しの案 (`memory.proposed.md`) と `memory-archive.md`、調査艦の `work/<item>/` など。

git で管理するかは利用者が決める。

### 6.2 board

**2 層に分ける**
- **仕組みが読む固定の項目** (全チーム共通): `id` / `title` / `kind` / `parent` / `assignee` / `reviewer` (任意。レビュー中の席) / `state` (open・active・blocked・done の 4 つ) / `blocked_on` / `links`
- **チーム定義の中身**: 列、種類、追加の項目、完了の条件。`column` は任意で、チームが列を定義したときは固定の `state` に対応させる

仕組みが読むのは固定の項目だけ。チーム独自の部分を読むのはエージェントと人間。`branch` / `pr` / `worktree` のような git の項目は、固定にせず任意の項目にする。

**階層**: 項目は `parent` でつなげる。P0 は task 1 段だけ (§0)。charter → goal → milestone → task → subtask の多段の使い方は、今後の課題。

**項目ファイルの例**
```markdown
---
id: T-002
kind: task
title: ログイン画面の実装
parent: null
assignee: impl-1
state: active
column: review
blocked_on: []
links: [PR#12]
priority: high
branch: t-042
---
## 経緯
- 09-25 10:02 pm: 割り当て
- 09-25 12:40 impl-1: 実装完了、review へ
```

**更新のルール**
- frontmatter の項目は **board コマンド経由でのみ**変更する (`board set T-002 state=done` など)。コマンドが検査するのは**固定の項目の整合性だけ**: `state` が 4 つのどれか、`assignee` / `reviewer` が席の名前か `owner` (`-` で消す)、`parent` / `blocked_on` が実在する項目、`id` は変えられない、`title` / `kind` は空にできない。チームの列・種類・追加の項目は検査しない (モデルが勝手に構造を壊すのを防ぐのが目的で、運用の型は縛らない)
- 本文 (経緯、メモ) は自由に書いてよい。`board set --note` で経緯に 1 行足せる
- done になった項目は、設定 `board.archive_on_done` (既定 true) で `board/archive/` に移る。false の艦は `board archive` で手で移す

**コマンドとビュー** (同じファイル群を別の角度で見せる): `board add / set / show / list / mine / archive / tree / kanban`。`list` は一覧 (既定は done 以外、`--all` で archive も含む)、`mine` は席の担当 (起動時の注入に使う。`inject.limits.mine_items` で件数に上限)。`tree` は parent を辿ってインデントで出す (done でも done 以外の子孫を持つ親は出す。parent が見つからない項目は最上段)、`kanban` は列 (`board.columns`。無ければ state) ごとに見出し + 件数 + 項目 (design-drift #4/#14、決定 2026-09-27)。どちらも decision 項目 (`D-NNN`) は対象外 (`decide list` がある)。描画は `board_view.py` に置き、captain 向けの注入の部品 `board` (opt-in、`inject.limits.board_items`) と共有する (design §8.2)。

board に入れないもの: 「なぜそうしたか」は decisions (P1)、「今日何をしたか」は席の作業ログに書く。

### 6.3 引き継ぎ (handoff.md)

席ごとに 1 本 (`seats/<seat>/handoff.md`)。シフトの終わりに**上書き**する。

```markdown
# impl-1 引き継ぎ (2026-09-25 18:00)
- 担当状況: T-002 は review 待ち (PR#12)。T-003 は未着手
- 途中の作業: なし
- 次にやること: レビュー指摘が来たら対応。来なければ T-003
- 詰まり: なし
- memory 候補: テストのモックは 30 日で期限が切れる
```

- 長さの上限は `inject.limits.handoff` (ひな形は 40 行 / 2000 文字)。**超えても書き込みは拒否しない**。`seat-stop` が注意を出し、次のシフトの注入は上限で切って「全文は `<path>` を Read せよ」と付ける。拒否すると終業処理が失敗して何も残らないため
- memory 候補は、P0 のひな形では handoff の項目に書く。P1 で `yamato memo` (memory-inbox への追記) に移す (design-p1 §3.2)

### 6.4 作業ログ

`seats/<seat>/log/<日付>.md` に追記する。監査用と、人間や captain が「何があったか」を追うときに使う。hook (シフトの開始、権限の拒否など) が自動で 1 行ずつ書き、席は `yamato log` で足す。**起動時には読ませない**。例外は、前のシフトが引き継ぎなしで終わったときで、その席の次のシフトには末尾を数行だけ注入する (`log_tail`、§8.2)。

### 6.5 decisions (P1)

- 判断待ちは board の項目 (`kind: decision`) として扱う (§9)
- 決まったら、要点を `decisions/log.md` に追記する。「なぜこうなっているか」を後から追える。追記のみで、起動時には読まない
- 詳細は design-p1 §1

### 6.6 memory

- **候補**: 席の `memory-inbox.md` に溜める (§0 I5)。起動時には読まない
- **memory 本体**: 起動時に注入する。長さの上限は `inject.limits.memory` (`[行数, 文字数]`)。置き場は、P0 の実装では `seats/<seat>/memory.md`。design-p1 は、役割の知見として `roles/<role>/memory.md` (同じ役割の席で共有) に置く形にしている。P1 の棚卸しの実装で揃える (§15)
- **棚卸し** (P1、design-p1 §3): 案を書くのは各役割の headless シフト (`memory.proposed.md`)、反映は `yamato memory apply`。**反映する役は設定 `memory.applier` (既定は hub = captain) と役割プロンプトで表し、コードは呼び出し元を検査しない**。コードが強制するのは、上限を超える案の反映を拒否することだけ (安全網。数値は `inject.limits.memory` / `knowledge`)。棚卸しの頻度も設定 (`curate_every` / `curate_at`)
- 自動で溜めて、次のプロンプトに自動で入れることはしない (腐るため)
- **自前で置く (2026-09-25 合意)**。Claude Code ネイティブの `memory: project` は使わない。理由: 保存先が作業対象の repo 側になる / 同じ repo で複数チームを動かすと同名の役割で混ざる / エージェントがいつでも書けてしまい、棚卸しで書く方針とぶつかる。注入は handoff と同じ SessionStart hook に 1 ファイル足すだけで済む
- チーム全体で共有する知見は `knowledge.md` に置く。全員が起動時に読む (`inject` の `knowledge`)。誰が手入れするかは役割プロンプトで決め、ひな形の既定は captain

### 6.7 共通ルール

- 秘密情報は記録に書かない (起動のたびにコンテキストに入る前提で扱う)。これは**役割プロンプトの共通の節**で約束する (コードでは検査できない)。席から特定の環境変数を外したいときの道具が `env_unset` (§5.1)。既定は空 (`GH_TOKEN` は外さない。gh の権限はトークンのスコープで絞る。決定 2026-09-27)
- 寿命の違う情報は別のファイルに分ける (今の状態 = 上書き / 経緯 = 追記 / 長期の知見 = 手入れ)

## 7. メッセージと起動 (`send`)

Claude Code の `SendMessage` は、待機中のセッションを起こせる。一方で、記録に残らず、止まったセッションには届かない。そこで配送コマンドを 1 本自作する。ただし**生きている席への即時の配送は、送り手が `SendMessage` で行う** (§0 B1)。yamato のコマンドは、記録して、止まっている宛先を起こすところまでを持つ。

`yamato send <ship> <seat|owner> "<msg>" [--from <seat>]` の処理:
1. 宛先の `inbox.jsonl` に追記する (ロックの下で連番を振る。まず記録に残す)。送り手 (`--from`、既定 owner) を記録するだけで、**送り手と宛先の組み合わせは検査しない**
2. 艦の状態を `.runtime/deadline` で見る。未起動なら記録だけで宛先は起こさない。終業の指示のあと (猶予中と猶予切れ) も記録だけで、送り手が席なら「終業せよ」を返す (§12.1)
3. 宛先の席の状態を、`roster.json` と `claude agents --json --all` を照らして見る (生きているかは `pid != null`)
   - **生きている** → yamato は配送しない。送り手が席なら、出力に `SendMessage` の宛先名と本文が出るので、送り手がそれを `SendMessage` ツールで届ける。届けたかどうかは、送り手の `seat-stop` が確かめる (宛先が読んでいない送信が残っていれば止める。`--delivered`、`seat_stop.require_delivery`)。送り手が席でないとき (owner が CLI で打つ) は、記録済みで、`claude attach` で直接話せる
   - **終業処理中 (`stopping`)** → 止まるのを待ってから、下の 2 つのどちらかで起こす (遅延 stop の間に届いたメッセージが、停止で消えるため。E2E で見つけた)
   - **止まっている `persistent`** → `claude --resume <フルの sessionId> --bg` で再開する (pid が消えてから。§4.1)
   - **`per_task`、または席が未起動** → 新しいシフトを `claude --bg` で起動する (§0 B3)
4. 宛先が owner のとき → `<ship>/owner/inbox.jsonl` に書き、`notify.via` に並べた経路すべてに通知する。P0 の経路は `command` (`notify.command` に件名と本文を渡して実行) と `mac`。`slack` / `windows` は P1 (agent-fleet の `notify.py` を移植する、design-p1 §2.4)。どれかが失敗しても他は送り、送れなかったことで `send` を失敗させない

- エージェントは Bash からこのコマンドを呼ぶ。呼んだ時点で記録と起動が終わるので、常駐プロセスは要らない
- 役割プロンプト (ひな形) に「`SendMessage` は `send` の出力を見てから、別のツール呼び出しで行う。`send` と `seat-stop` を 1 つの Bash にまとめない」と書いてある (E2E で、まとめて呼んだために配送が漏れた)
- チームの全ての席に `crossSessionInbound: "accept"` を設定する (設定がないと、無人の席でメッセージが黙って保留される)。コードが必ず入れる
- 起動したエージェントは、自分の inbox の未読を読む。既読の管理は別ファイルのカーソル (`inbox.cursor`) で持つ。SessionStart の注入は表示できた分だけカーソルを進め、`yamato inbox <ship> <seat>` は未読を全文で表示して既読にする
- P1 で足すもの: `shift: headless` の宛先はラッパー `run-headless` で起動 (design-p1 §4.2)、`--cwd` で worktree を cwd にして新しいシフトを起こす (design-p1 §8.2)、同じ本文の連投と高頻度は**拒否せず警告と記録だけ** (design-p1 §5.5)

## 8. シフト

### 8.1 粒度は役割ごとに選べる

| shift | 起きるとき | 終わるとき |
|---|---|---|
| `per_task` (日雇い) | 割り当てが届いたら、新しいセッションで起動 | 仕事が終わったら引き継ぎを書いて、自分を停止する |
| `persistent` (長期雇用) | セッションを使い続け、メッセージが来たら再開 | 人間が止めたとき。P1: 設定 `rotate:` の条件 (コンテキストの量、compaction、時間、日付) に当たったら、引き継ぎを書いて新しいシフトに入れ替える |
| `headless` (P1) | 割り当てが届いたら、使い捨ての `claude -p` を 1 回走らせる | プロセスが自然に終わる。覗けないが、権限・予算・コストの扱いが単純 (§0 B5)。調査など、途中で人が割り込まない仕事向け |

- 1 日 1 回のようなシフトは、人間が手で起動・終業すればよい
- **どの粒度でも、シフトの終わりに引き継ぎを書く**。そのため、粒度を変えても、席が予期せず止まっても、記録から再開できる。会話の再開 (resume) は「楽をするための最適化」であって、連続性の本体ではない (§0 I4。captain も同じ)
- **dev のひな形の impl (`per_task`) は、PR を開いたあと merge まで席に残る** (差し戻しを同じ会話で直す。「merge 済み」の知らせで終業する。コードは変えず、役割プロンプトだけの決まり。決定 2026-10-05)。席が落ちたあとの `send` は下のとおり新しいシフト
- `per_task` 宛ての `send` は常に新しいシフトを起こす。`persistent` 宛てだけ再開する (§0 B3)。P0 は、止まっていれば常に resume する。P1 で、resume せずに新しいシフトにする条件が設定 `rotate:` で加わる (design-p1 §5.3〜5.4)。**per_task の宛先が生きている (前の task の会話が持ち込まれる疑い) ときは、§0 B3 自体は変えず、配送はそのまま行って警告を 1 行出すだけにとどめる** (#11 / 決定 2026-09-27。attach 中かどうかを外から見分けられないため、design-p1 §5.2)

### 8.2 起動時に読むもの (上限つき)

SessionStart hook が、次を注入する。**何を読ませるかは設定** (`inject.parts`、役割ごとに `roles.<role>.inject` で上書き。ひな形の既定値)。

| 部品 (`parts`) | 中身 |
|---|---|
| (常に) ヘッダ | 艦名、自分の席、captain、yamato コマンドの場所、稼働時間の残り (時間の上限を過ぎていれば、終業の指示) |
| `handoff` | 自分の引き継ぎ |
| `log_tail` | 前のシフトが引き継ぎなしで終わったときだけ、作業ログの末尾 |
| `mine` | board の「自分の担当」 |
| `inbox` | 未読の inbox |
| `memory` | 役割の memory (`roles/<role>/memory.md`。design-p1 §3) |
| `knowledge` | チームの knowledge.md |
| `file:<名前>` (opt-in) | team.yaml の `inject.files.<名前>` のファイルの中身 (決定 2026-10-08。charter は `inject.files.charter` + `file:charter`、前の部品 `charter` を置き換えた)。パスは相対なら艦フォルダ基点、`~`・絶対パス可、repo の中は `@<workspace の呼び名>/<パス>`。見出しは `## <名前> (file: <パス>)`。知見の hook に、inject のリストに書いた順で載る。無い・空・読めないは `(なし: <パス>)` の 1 行 (エラーにしない。無いファイルは検査で warnings)。`inject.limits.files.<名前>` → `inject.limits.files.default` → `[60, 3000]` (`[行数, 文字数]`) を超えたら切って全文のパスを出す。ひな形は captain (hub) と planner に `file:charter` を入れる。古い書き方 (部品 `charter`・トップの `charter:`・`inject.limits.charter`) は v1.3.0 で読み込みエラーにした (v1.2.0 は 1 版だけ読み替えた) |
| `board` (P1、opt-in) | 艦全体の進み具合 (kanban 風): state ごとの件数 + blocked→active→open の項目一覧。`mine` と重なっても省かない。`inject.limits.board_items` で件数に上限、超えた分は「…ほか N 件」(design-drift #4/#14、決定 2026-09-27) |
| `fleet` (P1、opt-in) | 全艦の様子 (`yamato ships` 相当を 1 艦 1 行): 稼働中か・残り時間・captain の生死・赤い席・owner の判断待ち・今日のトークン。admiral (決定 2026-09-27) だけが使う想定。`inject.limits.fleet_items` で件数に上限、超えた分は「…ほか N 件 (`yamato ships` で見る)」 |

- 役割のプロンプトは注入ではなく、`--agents` の JSON で渡す (§4.1)
- 注入は **SessionStart hook 2 本**に分ける。記録の hook (ヘッダ・`handoff`・`log_tail`・`mine`・`inbox` と注記) と、知見の hook (`memory`・`knowledge`・`file:<名前>`。リストに書いた順)。Claude Code は hook 1 本の出力を 10,000 文字まで受け取り、超えると本文の代わりに約 2KB のプレビューを渡す (検証 C Q1。判定は hook ごとで、文字数で数える)
- 上限は `inject.limits` で持つ。`handoff`・`log_tail`・`last_report`・`memory`・`knowledge` は `[行数, 文字数]` (正の整数 2 個。整数 1 個は検査で拒否)、それ以外は正の整数。`inject.files` の各ファイルの上限は一段下の `inject.limits.files.<名前>` (と `files.default`)。(ひな形の値: handoff 40 行 / 2000 文字、担当 15 件、未読 10 通、**hook 1 本の全体 9500 文字**)。memory と knowledge は `inject.limits.memory` / `knowledge` (ひな形: memory 80 行 / 4000 文字、knowledge 120 行 / 5000 文字。`memory apply` が反映を拒否する上限と同じ。前の `memory.limits` は v1.3.0 で読み込みエラーにした) で切る。切ったところには「全文は `<path>` を Read せよ」と付ける (hook の全体で切ったときは、全文を `.runtime/` に書いてそのパスを付ける)。全体の上限は安全網として残し、個々の中身は設定に置く
- captain は、これに加えてカンバン風の `board` と日報を読む (P1、設定の `inject`。日報は前回の「一言」「判断待ち」「明日」の 3 節だけ。design-p1 §2.3、`board` は §6.2)。ほか P1 で、孤児になった項目の一覧や棚卸し案の有無も注入に載る (design-p1 §5.6、§3.4)

### 8.3 シフトの終わり

- 終業の手順は役割プロンプトに書く: 引き継ぎを Write で上書きし、作業ログに 1 行足し、`yamato seat-stop <ship> <seat>` を実行して、そのターンを一言で終える
- **`seat-stop`** (席が使う道具): 自分の席のセッションかを確かめ (`CLAUDE_CODE_SESSION_ID` を roster と照合。席の取り違えを防ぐ整合性の検査で、権限の判定には使わない)、`handoff.md` が今回のシフトで更新されているか (`seat_stop.require_handoff`)、送り手として届けるはずの送信が読まれているか (`seat_stop.require_delivery`) を確かめる。通れば roster を `stopping` にし、遅延 stop (10 秒後に `claude stop`) を仕掛ける。直接 stop すると最後のターンが transcript に残らず、resume した席が自分を止め直そうとするため (検証 A Q3)。遅延 stop は艦フォルダを cwd にして起動する (`worktree rm` で呼び出し元の worktree が消えても走る)。起動そのものに失敗したら events `restop_failed` と作業ログに残す
- 席が止まると、roster のシフトを閉じて、使用量を `usage.jsonl` に 1 行書く (transcript から数える。シフトの間の assistant のトークン数)。transcript の場所 (`~/.claude/projects/**/<sessionId>.jsonl`) は Claude Code の内部の形なので、見つからない・読めないときは 0 ではなく「分からない」(`"unknown": true`) と記録し、日報にもそう出す。`status` の「最終」も同じ場所の mtime を足しに見るだけで、読めなければ hook の `lastActive` とシフトの時刻で出す
- **Stop hook は応答のたびに動くため、引き継ぎの強制には使わない**。P0 の Stop hook は、時間の上限を過ぎたときに終業を指示するだけ (§12.1)。日雇いの席が引き継ぎを書かずに終わろうとしたときの安全網 (Stop hook) は、P1 で設定 `handoff_guard:` (既定 on) として足す。短い headless の仕事には重いので、外せるようにする (design-p1 §0.4)
- 会話ログ (transcript) は艦フォルダに退避しない (決定 2026-09-27)。Claude Code 側では 30 日で消えるが、コピーする hook は作らない。design-p1 §1.5 の代筆の追跡は、これを前提にせず `--reason` (owner の言葉をそのまま書いたもの) を根拠にする

## 9. 判断とエスカレーション (P1)

P0 にはまだ無い (`yamato decide` は P1)。詳細は design-p1 §1。

- 判断待ちは board の項目にする (`kind: decision`、id は `D-007`)。判断の種類 (`category`) は team.yaml の `decisions` の鍵で、`when` に「この場合は判断を開け」の説明を置く。何を decision にするかは、役割プロンプトと `decisions` の `when` で決める
- decider は、項目を開いたときに `decisions` から決めて項目に書く。あとで表を変えても、開いている項目の decider は変わらない。止まっているタスクの側が `blocked_on` で持ち (v1 の例の decision 側の `blocks:` は持たない)、向きを 1 つにして食い違いを防ぐ

  ```markdown
  ---
  id: D-007
  kind: decision
  title: 認証を JWT にするかセッション方式にするか
  category: design          # team.yaml の decisions の鍵
  decider: owner            # 開いた時点で category から解決して書き込む
  state: open               # open (待ち) → done (決定)
  links: [T-002]
  ---
  ## 背景 / 選択肢と推し / 決定
  ```
- decider が AI の役割なら、その席に `send` で届く。decider が人間なら、inbox に書いて通知する。人間宛ては 1 件ずつ通知せず、日報にまとめるのが既定 (`notify.decisions: digest`)
- **人間が判断を返す相手は、既定では captain** (§0 I6)。owner の入口は `yamato talk <ship> [<seat>]`。席を省くと team.yaml の `talk_default` (省略時は hub) と話す。これはひな形の既定値と役割プロンプトで表し、道具としては任意の席と話せる。captain は `decide close --by owner` で代筆し、決定を `decisions/log.md` に転記し、止まっていたタスクを再開させる
- **誰が判断を閉じてよいかは、コードで制限しない**。閉じた席 (`closed_by`) と、代わりに決めた人 (`on_behalf_of`) を必ず記録し、decider 以外が閉じたら記録に残して日報の「異常」に出す
- スマホからは Remote Control で captain と話せる。ひな形の既定値は、全席 `remoteControlAtStartup: false` で、captain だけ `remote_control: true` (design-p1 §1.5、検証 D V10)
- 「AI に上げる」と「人間に上げる」の違いは `decider` の値だけ

## 10. 表示 (zellij)

席そのものは background session として動く。zellij は、それを覗いて入るための**窓**にする。

```
zellij セッション
├─ タブ: dev
│   ├─ ペイン: pm        → その席の今のセッションに attach
│   ├─ ペイン: impl-1
│   └─ ペイン: reviewer  → 非番なら「待機中」と表示
└─ タブ: research
```

- 各ペインでは小さなスクリプト (`seat-attach`) を動かす。`roster.json` からその席の今のセッションを探して attach し、席が入れ替わったら付け直す
- レイアウトは zellij の KDL layout ファイルで宣言する。席を後から足すときは `zellij action new-pane` を使う
- zellij を閉じても、チームは動き続ける (表示と実行を分ける)
- **実装**: P2 の先行として、`bin/yamato-seat-attach` と `src/yamato/view/` (PR #2) が入っている。`bin/yamato-seat-attach --layout dev research -o ~/yamato/view.kdl` で layout を作り、`zellij --session yamato-view --new-session-with-layout ~/yamato/view.kdl` で開く。`yamato view [名前...]` (= `view open`。名前は艦か `admiral`、省くと admiral が先頭で全艦。セッションは yamato-view 1 つで、足りないタブを足して attach する) に組み込まれている。艦の登録簿 (`ships.json`) はまだ引かない
- **孤児の attach と固まり**: ペインの `view attach` は子に `claude attach` を持つ。zellij がペインを閉じる (タブの作り直し・`delete-session`・サーバの終了) と親に SIGHUP / SIGTERM が届き、以前はこれで `view attach` が即死して子の `claude attach` が親なし (ppid 1) のまま席に繋がって残った。次の `view` で同じ席に別の attach が付くと取り合いになり、追い出された側が 1 秒後に付け直すのを繰り返す (原因は実機未再現の推測を含む)。対策: `view attach` は SIGHUP / SIGTERM で子を止めて抜ける / zellij への非対話の呼び出しは 15 秒で打ち切る / attach が 10 秒未満で 3 回続けて終わったら 30 秒待って取り合いの疑いを表示する / 同じ席に別の `view attach` があれば起動時に警告する。
- **`yamato view stale [--kill]`**: ペインを失って残った孤児 (親が init の `view attach` / `claude attach`) を一覧する。**`--kill` なしでは止めない** (`yamato view` も黙って止めず、警告だけ出す)。`--kill` で SIGTERM を送る。親のいる attach と、利用者が自分の端末で動かした `claude attach` には触らない。固まった zellij セッションは `zellij delete-session --force yamato-view` で消す (席は止まらない)。手順の詳細は `src/yamato/view/README.md`

**spike `spike-zellij-attach` で確認済み (2026-09-25, zellij 0.45.1 / Claude Code 2.1.282)**
- `claude attach` は zellij のペインで正しく表示され、キー入力で返信もできる
- ペイン、タブ、zellij セッションのどれを閉じても、切断されるだけで席のセッションは生きている
- attach 先が stop / rm されると、`claude attach` は exit 0 で抜ける
- 付け直しスクリプトの試作は動いた (席 A を停止 → 待機 → 同じ名前の席 B を起動 → 自動で B に attach)

**設計上の注意 (spike と実装で判明)**
- **止まっているセッションに attach すると、そのセッションが再起動する。** 古いシフトを蘇らせないよう、pid があるもの (生きているもの) にだけ attach する。これは zellij の窓 (自動で付け直すスクリプト) の規則。人間が意図して席と話す `yamato talk` (P1) は、先に yamato が席を起こしてから attach する (design-p1 §1.5)
- **`--name` は一意にならない。** 同じ名前のセッションを複数作れる。そのため「その席の今のシフト」の正本は `roster.json` に置き、名前からは探さない
- **`claude attach` はフルの sessionId を受け付けない** (短い id を渡す)。照合はフルの id で行い、attach には短い id を使う
- 同じ席を 2 つのペインで開くと、入力欄が共有される (片方に打った下書きが、もう片方にも出る)
- attach していれば、約 1 時間で止められるルールの対象から外れる (検証 C Q3。Remote Control なしの条件で 75 分生存、attach なしの対照は 60 分で停止)。**zellij で窓を開いている席は常駐する**。ターンは消費しないが、メモリは食う
- 席の作業ディレクトリは、事前に Claude Code の workspace trust を通しておく必要がある (`ship create` が警告し、`up` が止まる。`~/.claude.json` から確かめられないときは、起動して claude の出力で判定する。§4.1)
- attach はペインのフォアグラウンドで動かす (macOS ではバックグラウンドで動かすと落ちる)

### 10.1 dashboard (決定 2026-10-08)

`yamato dashboard [--port N] [--no-open] [--edit]` は、全艦 (`admiral.all_ships()` と同じ範囲) を 1 ページに並べてブラウザで見せる。owner が「自分待ち」と各席の様子を一目で見るためのもの。

- **読むだけ** (`--edit` を付けないとき): GET だけ、艦フォルダに何も書かない (POST は 501)。`seat.status` は呼ばない (reconcile・enforce・restop_stuck が roster を書き換え席を止めるため)。roster・board・inbox・events・usage・作業ログ・日報 (`report.latest`)・transcript (`rotate.last_usage`。末尾だけ)・memory の候補と反映の記録 (`memory.status`) を読み、`claude agents --json` は 1 回の描画で 1 回
- **ローカルだけ**: `127.0.0.1` に bind。bind 先を変えるオプションは作らない。認証なし。既定 port は 8765 (`--port` を書かなければ使用中なら 8766… と 10 個まで試す。書いたのに使用中ならエラー)
- **書き込み (`--edit`)** (`src/yamato/web/edit.py`、`dashboard.Handler.do_POST`): 付けて起動したときだけ `POST /api/v1/edit/preview` と `/commit` を受ける。起動ごとに乱数トークン (`secrets.token_urlsafe`) を作り、サーバーが `index.html` を返すときに `<meta name="yamato-token">` として埋める (SPA が `X-Yamato-Token` ヘッダで返す。`frontend/src/api/client.ts` の `previewEdit` / `commitEdit`)。POST は、トークン・`Host: 127.0.0.1:<port>`・`Origin: http://127.0.0.1:<port>` が**全部**合い、`Content-Type: application/json` で本文 1 MiB 以下のときだけ通す (どれかが違えば 403 `forbidden`、Origin が無いのも拒む)。`--edit` のときは GET も Host を見る (DNS rebinding でトークン入りのページを読ませない)。`--edit` が無ければ POST は今までどおり 501、PUT・DELETE は常に 501。書き込みは 2 段: `preview` は検証して unified diff と今のファイルの sha256 (`base_hash`。ファイルが無ければ `-`) を返し、何も書かない。`commit` は検証をやり直し (通らなければ 422)、今のハッシュが `base_hash` と違えば 409 `conflict` で何も書かず、書くときは上書き前の版を `<艦>/.runtime/edit-backup/` に 1 世代だけ残し、同じ dir の一時ファイル + `os.replace` で置き換える (権限は元のまま)。書ける対象は `edit.RESOLVERS` に足した (艦, ファイル) → `Target(path, shipdir, key, validate)` の関数だけで、何も足さなければどれも 404 (対象と検証は個別の機能が決める)
- **載せるもの**: 艦は動いている艦 (生きている席がある艦) を上に、止まっている艦を下で 1 行 (最後の動き・board の件数) に畳み、開けば中身を出す。艦の枠の頭に稼働・残り時間・判断待ち・孤児・owner 宛て未読・board の active/open をバッジで並べ、0 件は出さない。節は 表 > board > PR > 前回の日報 > その他 の順に見出しを軽くする。ページの一番上に owner の判断待ち (decider が人間の open な判断 + 急ぎ・経過・推しだけ。owner 宛て inbox の未読は上部に並べず、艦の枠に件数だけ出す。`status=waiting` の席の waitingFor は席の表の status 列で見る)。艦ごとに deadline・席の表 (生存 pid・status・最終・active の項目・未読数・要対応 (`admiral.red_flags` の `Flag(重さ, 文, 詳細)`。赤 = 詰まり・API エラー、黄 = 動いていない・担当が無い。文は「何が起きた → 何をする」で、内部の言葉は title 属性。列名は赤と黄が並ぶので「要対応」、凡例は一覧のページに 1 回)・今日のトークン・作業ログの最後 (使用量の行を飛ばした最後の作業の行。無ければ「-」)。`shift: headless` の席だけ今日のトークンの下に「前回 $x / 上限 $y」: 前回 = usage.jsonl のその席の最新の headless の記録の `total_cost_usd`、無い・null は「-」、上限 = 役割の `max_budget_usd` (-p 1 回ごとの上限)、無ければ「-」。他の席は予算を出さない)・board の state 別の件数と active / blocked の項目・PR (board の `pr` 欄がある done 以外。workspace の origin が GitHub ならリンク)・直近 3 日の異常 (feed が赤くする種類と同じ集合 `feed.ABNORMAL_KINDS` を共有)。艦の枠の 2 段目: 前回の日報の一言・明日 / 孤児 (`monitor.orphans`) / memory の候補数と前回の反映からの日数・棚卸しの目安 / 席の文脈量と rotate の閾値に対する割合 (80% 以上で「rotate が近い」、取れない席は「-」)。上部は判断待ちだけのまま
- **壊れた艦**: 1 艦の読み取りで例外が出ても、その艦の `error` に理由を入れ `state` を null にして他の艦は返す
- **サーバーは JSON と dist の配信だけ**: HTML は組まない。`/api/v1/*` の JSON と `src/yamato/web/dist/` (フロントの build 出力) を返す。画面はフロントが API を読んで描く。一覧 (`/`) は 60 秒ごとに `GET /api/v1/overview` を取り直し、上の「載せるもの」(判断待ち・艦の枠・席の表・要対応の赤黄・予算・2 段目・PR・異常)。艦の名前は `/ship/<艦>` へのリンク、頭の注記は `--edit` のとき「編集できる」・それ以外は「読むだけ」、異常は直近 24 時間を赤で上に出し古いものは畳んで赤くしない、判断待ちは 1 件ずつ閉じるフォームを持つ を出す (`frontend/src/pages/OverviewPage.tsx` と `frontend/src/overview/`。取得に失敗しても前の表示を残してエラーを上に出す)。載せるものの値はすべて `GET /api/v1/overview` の応答 (`dashboard.overview`) にあり、文字列は生のまま返す (エスケープは画面の仕事)
- **dist の配信** (`src/yamato/web/static.py`): 起動時に dist の実在する通常ファイルの一覧を作り、その一覧にある URL path だけ返す (リクエストの path からファイルの path を組み立てない。`..`・シンボリックリンク・起動後に増えたファイルは 404)。MIME は拡張子で固定 (.js は text/javascript)。`/ship/` 以下の path は拡張子があっても (`/ship/<艦名>/files/charter.md` など) 画面の path として `index.html` (dist に実在するファイルはそのまま)。それ以外は知っている拡張子の無い path が `index.html`。dist が無いときは `/` などで「build されていない」ページ (503)。`/api/` 以下は dist に落とさず JSON のエラー (404)
- **フロントの土台**: repo 直下の `frontend/` (React + TypeScript + Vite + react-router、Node 24 固定、npm の lockfile を commit)。ビルド出力は `src/yamato/web/dist/` に commit し、利用者は node 不要。API の正本は `docs/api/openapi.yaml`、TS の型は `npm run gen-api` で生成して commit する。ナビは 一覧 / 艦隊 / 艦ごと。一覧・艦隊 (`/fleet`)・艦の設定画面 (`/ship/<艦名>/config`) は実装済み。艦のページ (`/ship/<艦名>`) は設定へのリンクと、`ShipConfig.files` の md (roles・charter・knowledge) へのリンクの一覧を置く。手順は README の「開発者向け」。Python 側は dist の配信と `/api/v1` の JSON だけを持つ
- **md の閲覧** (`src/yamato/web/files.py`): `files/{file}` は艦フォルダの `roles/*.md`・`charter.md`・`knowledge.md` の実在する通常ファイルだけ返す。艦は `admiral.find_ship` (config・edit・fleet と同じ範囲)、ファイルは起動のたびに作る列挙の dict から `file` (URL エンコードした相対 path。`roles%2Fimpl.md`) で引き、リクエストの値から path を組み立てない。`..`・絶対パス・シンボリックリンク・列挙に無いファイルは 404 (`not_found`)、読めなければ 500 (`unreadable`)。画面 (`/ship/<艦名>/files/<file>`) は小さな markdown パーサ (`frontend/src/markdown/`、依存なし) で React の要素にして出す。HTML を文字列で挿す API は使わず、本文の HTML は文字として出る。リンクは http(s)・mailto だけ
- **md の編集** (`src/yamato/web/md_edit.py`): `--edit` のとき、列挙にある `roles/*.md`・`charter.md` の実在する通常ファイルだけが書ける (`edit.RESOLVERS` に足す。新しいファイルは作らない)。`knowledge.md`・memory.md は対象に入れず 404 (`yamato memory apply` が反映の記録を残すため。API からも書けない)。検証は「空でない・NUL を含まない」だけ。画面は「編集」→ textarea →「差分を見る」(preview) →「確定」(commit。差分を見るまで押せない。preview 後に変わっていれば 409 で取り直す)。
- **操作の共通部品と席の操作** (`src/yamato/web/ops.py`・`seat_ops.py`): `POST /api/v1/ops/{op}` (`--edit` のときだけ。トークン・Host・Origin は edit と同じ入口で検査する)。操作は yamato の CLI を別プロセスで 1 回呼ぶだけで、操作のロジックは API にも画面にも持たない。引数は list で渡して shell を通さず (席名などは 1 つの引数)、画面が CLI を待つのは 30 秒まで (過ぎても CLI は殺さず裏で最後まで走り、`timed_out` で返す)、stdout と stderr をまとめて返し、端末の制御文字を落として 20000 文字で切る。CLI が失敗しても応答は 200 で、画面は `exit_code` と出力をそのまま見せる (打ち切ったときは `timed_out`)。出力は text として描く (HTML にしない)。どの操作ができるかは `ops.OPERATIONS` に `Operation` (艦フォルダと params から CLI の引数を返す関数) を足した分だけで、艦の操作・send などはここに足す。確認が要る操作は `confirmed: true` が無ければ実行しない (400)。画面の確認はブラウザの alert でなくページ内の確認画面 (`frontend/src/ops/OpPanel.tsx`。確認 → 実行 → 出力と exit code)。席の操作は `rotate` (止まっている persistent の席に「入れ替え」。`--by owner` で呼ぶ。生きている席・persistent でない席は CLI が断る)。`send` (席にメッセージを送る。`--from owner` で呼び、本文は `--` の後ろの 1 つの引数にして先頭が `-` でも option に化けない。入口は艦のページ `/ship/<艦名>` の「席に送る」。席欄は config API の roles から作った席 (count 1 は役割名、n は `<役割>-1..n`) の select で、取れない・読めないときは自由入力に戻す) と `decide-close` (判断を `--by owner` で閉じる。決定と理由は `--choice=値` `--reason=値` の形で渡す。入口は一覧の「owner の判断待ち」の各判断のフォーム (1 件ずつ)。決定・理由が空なら CLI が断る)。どちらも確認画面を経る。`seat-stop` は席のセッションの中から呼ぶ道具で、外から席を強制的に止める入口は作らない。生きている席の「終業を頼む」は `send` (上の入口と同じ操作) で「引き継ぎを書いて seat-stop しろ」を owner から送るだけで、席が応じて自分で止まる (なりすましも新しい CLI も無い)。入口 (席の表の「操作」列のボタン) は `--edit` で起動したとき (`index.html` にトークンがあるとき) だけ出す
- **艦の操作** (`src/yamato/web/ship_ops.py`): `up` (任意の `for`・`seats`)・`down`・`down-force` (`down --force`)・`extend` (`span`)・`halt`。どれも `yamato up` などの正規の入口をそのまま呼び、席や admiral の名前を環境変数で借りない。CLI が断れば (起動していない艦への extend など) その出力と exit code を画面に出すだけ。値が option に化けないよう先頭の `-` は断り、中身の検証は CLI に任せる。CLI は dashboard と別のセッション (`procs.spawn_detached`) で起こし、出力は艦の `.runtime/web-ops/` のファイル (新しい 20 個だけ残す) に書かせる。30 秒待って終わらなくても殺さず、`timed_out` と `log` (出力のファイル) を返して画面は「裏で続いている。結果は status で」と出す (up や halt を途中で殺すと watchdog の無い deadline や半端な停止が残る。dashboard を Ctrl-C で止めても巻き添えにならない)。入口は艦のページ `/ship/<艦名>` (`--edit` のときだけ)で、全部ページ内の確認画面を経る (`down --force` と `halt` は席が引き継ぎを書けず切れることを確認画面に書く)。
- **CI**: `.github/workflows/ci.yml`。push と PR (main・develop) で、frontend (npm ci → lint → typecheck → test → build → gen-api → `git diff --exit-code` で dist・型の鮮度) と python (unittest discover を 3.11・3.13)。secrets なし。`git.merge_requires` の `ci` はこれ
- **API のスキーマ**: web コンソールの JSON API の正本は `docs/api/openapi.yaml` (OpenAPI 3.1)。M1 は `GET /api/v1/overview` (dashboard の gather 相当)・`/api/v1/fleet` (ships.json と `_admiral`)・`/api/v1/ships/{name}/config` (team.yaml の項目ごとの値 + schema の description + 生の yaml + 「次の up で効く変更あり」)・`/api/v1/ships/{name}/files/{file}` (roles/*.md・charter.md・knowledge.md)・`/api/v1/ships/{name}/items/{id}` (board の項目 1 件。判断も task も同じ形で、本文は生の markdown。overview の Wait は軽いまま本文を持たず、画面は開いたときだけ読む。id は `[A-Z]-[0-9]+` だけ受ける)。エラーは全 path 共通で `{"error": {"code", "message", "detail"}}`。TS の型はここから生成する。Python 側は `tests/api_schema.py` の小さな検査器 (type・nullable・properties・required・additionalProperties・items・enum・const・文書内の `$ref`。jsonschema は vendor しない。知らないキーワードは黙って通さずエラー) で応答をスキーマに照らす。実装済みは `GET /api/v1/overview` と `GET /api/v1/ships/{name}/config`・`GET /api/v1/ships/{name}/files/{file}`・`GET /api/v1/ships/{name}/items/{id}` (応答は `tests/test_dashboard.py`・`tests/test_web_files.py`・`tests/test_web_items.py` が検査器で確かめる)。ほかの path は 404
- **艦隊の画面** (`/fleet`、`src/yamato/fleet.py`): 読むだけ (書き込みの入口は作らない)。`GET /api/v1/fleet` は `ships.json` の艦と `$YAMATO_HOME` 直下で team.yaml のある艦を、名前・パス・`registered` (ships.json にあるか)・`exists` (team.yaml があるか。ships.json にあってフォルダが消えた艦も落とさず `exists=false` で出す)・team.yaml の name・読めなかった理由で返す。team.yaml は検証せず読むだけなので、壊れた艦も一覧に残る。`_admiral` は `ships` に入れず別枠 `admiral` (パス・team の name・roles の名前・読めなかった理由。フォルダが無ければ null)。艦の名前は `exists` のとき設定画面へのリンクになる
- **艦の設定画面** (`/ship/<艦名>/config`、`src/yamato/shipconfig.py`): 読むだけ。艦の引き方は files・edit と同じ `admiral.find_ship` (ships.json の艦と `$YAMATO_HOME` 直下の艦で team.yaml のあるもの。fleet の `exists=true` と一致し、無ければ 404)。項目の一覧は `team.schema.json` の最上位の properties の並びと team.yaml のキーから組み立て (項目名はコードに固定しない)、schema に無いキーは末尾に説明なしで出す。各項目は値・型・description・enum。画面は項目を「艦の基本・席と役割・時間と予算・git と PR・その他」のまとまりに分け (対応表は画面側の 1 か所 `frontend/src/pages/configGroups.tsx`。schema に新しく足された項目は「その他」に入る)、頭に目次を置く。値を主役に大きく出し、説明は小さく薄くする。未設定の項目は「未設定」に schema の default か説明の「省略時 …」を添える。値が「名前 → オブジェクト」の項目 (roles・profiles など) は表に、それ以外の object は 鍵: 値の入れ子のリストにする (JSON の生の文字列は出さない)。生の yaml は折りたたむ。team.yaml の mtime が `.runtime/team.json` より新しければ `pending.changed` で「次の yamato up で効く変更がある」と出す (`keys` は検証した team.yaml と team.json で値が違う最上位の項目。まだ up していない艦は false)。team.yaml を読めない艦は `unreadable` のエラー JSON (500) で、画面はエラーを出して落ちない。**`--edit` で起動したときだけ、各項目がフォームになる** (`frontend/src/form/`・`frontend/src/pages/ConfigForm.tsx`)。入力欄の型・enum・説明は `ConfigField.schema` (その項目の `team.schema.json` の定義そのもの) から再帰で組む (項目名・値の型をフロントに固定しない)。anyOf は今の値の型で枝を選び、決まらない形 (自由形式の map・型の混ざった list など) は読むだけの表示にして「生の yaml で直す」と案内する。保存は `POST /api/v1/edit/preview` → 差分と検証結果を見る → `commit`。送るのは全文でなく**操作 (`ops`: set_value・add_key・remove_key・insert_item・remove_item)** で、サーバーが `yamlpatch` で今のファイルに当てる (コメントと並びは触った所の外で変わらない。`src/yamato/web/teamedit.py`。preview と commit のそれぞれで、ハッシュを取ったのと同じバイト列に当てる)。検証は `team.validate` と同じ (落ちれば 422 `invalid`)。`yamlpatch` が扱えない形の team.yaml (anchor など) や操作 (最後の 1 キーを消すなど) は 422 `unpatchable` で断り、画面はサーバーの案内を出す。保存したら「次の yamato up で効く」と出す (自動で up はしない)。
  - **生の team.yaml の編集** (`/ship/<艦名>/config/raw`、`--edit` のときだけ。設定画面から「生の team.yaml を直す」で入る。フォームで扱えない項目の行き先): textarea に team.yaml の全文を出し、「差分を見る」(preview。`content` で全文を送る) → 「確定」(commit)。書き込みはフォームと同じ `POST /api/v1/edit/*` の土台で、別の経路は無い。検証は yaml の構文 + `team.validate` で、エラーは `errors` の文字列の頭に `行 N:` を付ける (構文エラーはその位置、`team.validate` のエラーはメッセージが指す項目 (`roles.dev.trust` など) の行。項目を指せないエラーは行なし)。検証に通らなければ確定できず、preview 後にファイルが変わっていれば 409 `conflict` (フォームと同じ。差分を取り直す)。
- 範囲外: 判断を閉じる・返事の操作 (席の操作は上)・ローカル以外からの閲覧・ログイン型の認証 (書き込みはトークンだけ)・トークンのドル換算・1 日の費用の合計や超過の警告

### 10.2 yaml の部分置き換え (web コンソールの土台)

`src/yamato/yamlpatch.py` は、team.yaml のコメントと並びを保ったまま値を変える部品 (CLI のコマンドは無い)。PyYAML の `compose` が返すノードの位置 (`start_mark` / `end_mark`) を使い、**変えた所の文字範囲だけを元のテキストで置き換える。dump はしない**。

- 操作: `set_value` (scalar の変更)・`add_key` / `remove_key` (map)・`insert_item` / `remove_item` (list)。パスは `("roles", "pm", "model")` のようなキー (str) と添字 (int) の並び。何も変えなければバイト一致、触った所の外は一文字も変わらない
- block 形式も flow 形式 (`{a: 1}` `[a, b]`) も扱う。元が `|` `>` の scalar は block scalar のまま差し替える。引用符は元の書き方に寄せ、必要なときだけ引用符を付ける (`yes` `123` `a: b` など)
- 削除は、キー (要素) の行だけを消し、直前のコメント行は残す
- **安全網**: 組み立てた結果を `yaml.safe_load` で読み直し、元の値に操作を当てた期待値と一致しなければ `YamlPatchError`。一致しないテキストは返さない
- 扱えない形は `YamlPatchError` (呼ぶ側は生の yaml 欄に回す): anchor / alias / merge key・複数文書・scalar と collection の取り替え・最後の 1 キー / 1 要素の削除・`- - x` のような入れ子の block list・空の block collection への追加

## 11. admiral (窓口)

- 人間の窓口。仕事は**チームの作成・構成の変更、出撃と帰投、全チームの状況を一望し、owner の判断を代筆すること**
- **admiral はどの艦にも属さない、常駐の Claude のセッション**である (owner の決定 2026-09-27)。名前付きの bg セッション (`yamato.admiral`)。`yamato admiral` で、生きていれば attach、止まっていれば talk と同じ規則で起こしてから attach する。スマホからは Remote Control (`--remote-control`) で話せる
- 記録は `~/yamato/_admiral/` (`$YAMATO_HOME/_admiral/`)。**`_admiral/` は「席 1 つ・deadline なしの特別な艦」として、既存の seat / inject / rotate / talk / inbox の仕組みを丸ごと使い回す** (決定 2026-09-27)。`ships.json` には登録せず、`yamato ships` の一覧にも出ない (名前が `_` 始まり)
- **時間の上限は掛けない** (`time_limit: none`。§0 B4 の例外、決定 2026-09-27)。実装しておらず CLI を打って話すだけなので、上限を掛ける理由がない。長くなったら rotate (コンテキスト・compaction・日付) で入れ替える
- **艦の中身の仕事には踏み込まない** (task の割り振り・実装・レビュー・merge。判断や方針の中身は owner と captain / planner が直接やる)。**これは admiral の役割プロンプトの約束で、CLI は admiral からの `send` や board の操作を拒否しない** (mechanism-not-policy)。艦に送るのは出撃と帰投に伴う定型のメッセージだけ
- 権限は `opus` + `auto`。`~/yamato/**` (艦フォルダ全部・`ships.json`) は編集してよい (team.yaml・roles・charter の変更をやらせる仕事のため)。各艦の記録本体 (`.runtime/`・`roster.json`・inbox・memory.md など、各艦の yamato のコマンドだけが書く場所) は deny で守る。WebFetch / WebSearch は使ってよい (owner の判断。他の役割にある B2 の分離は admiral には掛けない)。`~/dev/yamato` (yamato 自身の repo) は読むだけ (Edit / Write を deny。pull は Bash で打つ)
- コマンドの一覧・使い方は design-p1 §6、[docs/admiral.md](admiral.md)

## 12. 1 日の回り方 (例)

手順の例であって、仕組みではない。

1. 朝: 人間がチームを起動する (`yamato up`) → captain が起きて board を棚卸しし、計画を立てて割り振る (`send`)
2. 日中: 割り当てを受けた席が起き、働き、引き継ぎを書いて終わる。captain が成果を回収し、次を割り振る
3. 判断待ちは decision 項目になり、decider に届く (P1)
4. 夕方: captain が日報を書いて owner に送る。判断待ちの一覧つき (P1。設定 `report.daily`)
5. 夜: 人間が終業する (`yamato down`) → 全ての席が引き継ぎを書いて止まる
6. 週 1 回: captain が memory 候補を棚卸しして、各役割の memory に反映する (P1。頻度は設定 `memory.curate_every`)。終わった項目は、`board.archive_on_done` が true なら done になった時点で archive に移っている

### 12.1 稼働時間の上限 (2026-09-25 合意)

暴走は**予算ではなく時間で止める**。チームごとに稼働時間の上限を設定する。

```yaml
# team.yaml
time_limit: 3h        # yamato up からの稼働時間
grace: 20m            # 終了時刻のあと、キリのいいところまで待つ時間
```

`yamato up dev --for 3h` のように、起動するときに上書きもできる。

止まり方は、P0 では 2 段 (§0)。P1 で最終受付を軽い形で足す。

| 時刻 | 何が起きるか |
|---|---|
| 最終受付 (P1) | design-p1 §9。captain の最初のターンに 1 回だけ「終了まで X 分。新しい大きな割り当てはやめよ」を注入し、以降の `send` の本文に注記を足す。拒否はしない。**注入と注記は時間の上限 (安全網) の一部でコードが出すが、captain が大きな割り当てをやめるかどうかは役割プロンプトの約束** |
| 終了時刻 (終業) | 全ての席に「キリのいいところで止めて、引き継ぎを書いて `seat-stop` せよ」を届ける (下) |
| 終了時刻 + 猶予 (強制停止) | まだ生きている席を `claude stop` で止める。止められた席は roster に「引き継ぎなしで終了」と記録する (引き継ぎが書かれていれば「強制停止 (引き継ぎは書かれていた)」)。次に起動したとき、その席は handoff に加えて作業ログの末尾も読む (`log_tail`) |

- **終了時刻はプロセスではなくデータ**で持つ (`.runtime/deadline`。§0 B4)。`yamato up` が書く。終業の指示は、席の状態に応じて次の経路で届く
  - 作業中の席: ターンの終わりに Stop hook が終業を指示する (block)。1 つのターンの中でツールを呼び続ける席には、PreToolUse hook が、ツールは通したうえで終業の指示を添える (`additionalContext`)。Stop と PreToolUse を合わせて 1 シフトあたり数回まで (`MAX_WRAPUP_NOTICES`)
  - 待機中の席: Stop hook で起動した非同期の watcher (asyncRewake) が、終了時刻に席を起こして指示する。指示の回数を使い切ったあとも、猶予が切れるまで見張る
  - 同じ watcher (1 席 1 本) が inbox も数秒おきに見て、送り手が席でない未読が増えたら「inbox に未読がある。`yamato inbox` で読め」と席を起こす (§0 B1。同じ未読では 1 回だけ。終了時刻を過ぎたら起こさない)
  - `time_limit: none` (決定 2026-09-27、admiral のみ) の艦は終了時刻を持たず、watcher は inbox だけを見張って回り続ける (上記)。これを終える経路 (`seat-stop`・強制停止) を通らずにセッションが死ぬと (手で `claude stop`・crash)、roster は `on_shift` のまま watcher だけが残り、次のシフトの watcher は pidfile を見て即 return してしまう。そこで watcher は自分が古くなったことにも気付いて抜ける: 席の `shiftNo` が変わった (次のシフトが別の watcher を立てた)・pidfile が自分以外の watcher の名前になった、のいずれかで即座に return し、さらに数十秒おき (`LIVENESS_EVERY` ポーリングごと) に `claude agents --json` で自分のセッションがまだ生きているか確かめて、消えていれば見張りをやめる (`claude` を呼べない・答えが読めないときは「不明」として見張りを続ける。誤って早く諦めない側に倒す)
  - `send`: 終業のあとは宛先を起こさず記録だけして、送り手が席なら終業を指示する
  - 新しいシフトの SessionStart の注入にも、終業の指示が載る
- 一度きりのタイマー (watchdog) は `up` と `down` が切り離して起動する。終了時刻 + 猶予に、生きている席を強制停止する補助で、消えても上限は効く (`send` / `status` と席の hook が毎回 deadline を確かめ、猶予を過ぎていれば強制停止する)。常駐のデーモンは作らない
- `watchdog` と `status` は、`stopping` のまま `STOPPING_STUCK_AFTER` (5 分) を超えて生きている席を止め直す (`restop_stuck`。遅延 stop がおそらく走らなかったとみなす)。同じ席には同じ間隔より頻繁には打たず、events `stopping_stuck` と作業ログに記録する
- 猶予を過ぎたときの席の hook (watchdog が消えていても席が止まるための経路):
  - PreToolUse hook: ツールを **deny** し (理由に「間もなく止める。一言でターンを終えよ」)、席自身の遅延 stop を仕掛ける。遅延 stop は `seat-stop` と同じ仕組み (切り離した `sh -c "sleep 5; claude stop <id>; yamato _shift-ended ... --forced"`) で、1 シフトに 1 回 (`.runtime/force-stop-<seat>.json`。persistent の席は resume しても sessionId が同じなので、シフト番号で見る。止まらずに hook を呼び続けていれば 60 秒後にもう一度)
  - Stop hook: 終業の指示 (block) ではなく、ターンを終わらせて同じ遅延 stop を仕掛ける
  - 待機中の席: 上の watcher が、猶予が切れたところで同じ遅延 stop を仕掛ける
  - 止まったあと `_shift-ended --forced` がシフトを「強制停止」として閉じ、events に `force_stop` を残す。生きている席が無くなれば日報の安全網も走らせる (watchdog がしていたこと)
  - headless の席は、ラッパー (`run-headless`) が時間切れを持って `claude -p` を止めるので、hook はツールの deny だけをして遅延 stop は仕掛けない
- PreToolUse hook はツール呼び出しのたびに走るので軽くする。`yamato` の入口が CLI を読み込む前に `yamato.pretool` を呼び、`.runtime/deadline` の JSON 1 つだけを読んで、終了時刻の前ならすぐ抜ける (標準ライブラリだけ。過ぎていれば `hooks.pre_tool_use` に渡す)
- `yamato down` を手で打てば、その時点で終業の段階から始まる。`down --force` は猶予なしで強制停止する。P1 で `ship extend` (deadline を延ばす) と `ship halt` (緊急停止) が加わる
- PC がスリープするとタイマーは遅れる。スリープ中はチームも止まっているので、実害はない
- 実機の E2E では、idle の captain を watcher が終了時刻に起こして終業させたこと、`down --force` で強制停止できることを確かめた。Stop hook の block による終業指示と、watchdog による猶予切れの自動停止は、実機では観測できず (全席が先に自分で止まった)、単体テストでだけ確認している (`e2e/e2e-p0.md`)。のちに、watchdog を殺した状態で猶予を過ぎても長いターンを続ける席を、PreToolUse hook が止めることを実機で確かめた (`e2e/e2e-time-limit.md`)

## 13. コマンド

`yamato <サブコマンド>`。P0 で実装済みのもの (使い方の手順は README):

| コマンド | 内容 |
|---|---|
| `ship create <name> --workspace <path> [--workspace <path2> ...] [--path <dir>] [--template dev]` | ひな形から艦フォルダを作り、艦の登録簿に載せる。`--workspace` を複数回渡すと複数 repo の艦 (team.yaml の workspace が配列になる)。workspace が trust されていなければ (確かめられなければ、その旨を) 警告する |
| `up <ship> [--for 3h] [--seats a,b]` / `down <ship> [--force]` | 起動 (稼働時間つき。`.runtime/` の作り直し、deadline、captain の席の起動。`--seats` と team.yaml の `up_seats` の席も一緒に起こす (和、重複と hub は除く。既定は空)) / 終業 (`--force` で即時に強制停止) |
| `status [<ship>]` | 席ごとの状態 (生存、最後に動いた時刻、権限の確認で止まっている「詰まり」)、deadline までの残り、未読 inbox |
| `send <ship> <seat\|owner> "<msg>" [--from <seat>]` | メッセージを送る (§7) |
| `inbox <ship> <seat\|owner> [--all]` | 未読を全文で表示して既読にする |
| `dashboard [--port N] [--no-open] [--edit]` | 全艦の owner の判断待ちと席の状況をローカルのブラウザで見る (§10.1) |
| `board add / set / show / list / mine / archive` | board の操作と表示 (§6.2) |
| `log <ship> <seat> "<text>"` | 席の作業ログに 1 行追記する |
| `seat-stop <ship> <seat> [--delivered]` | 席が使う。終業処理 (引き継ぎの確認と遅延 stop。§8.3) |
| `hook <event> <ship> <seat>` | Claude Code の hook から呼ばれる (session-start / session-start-knowledge / stop / wait-deadline / deny-dialog / log-denied) |

P1 で足すもの (design-p1): `decide open / close / list`、`report daily`、`memo`、`memory curate / apply / status`、`ship extend / halt`、`ships` (全艦の一覧)、`talk`、`worktree add / path / list / rm`、`pr open / merge`、`seat-stop --rotate` と `rotate <ship> <seat>... | --all` (止まっている persistent の席に外から同じ入れ替えの印を立てる、design-p1 §5.4)、`feed` (艦の出来事 events.jsonl を流し見する)。ほか、headless の席を起こすラッパー `run-headless` (内部用)。P2: `view` (実体は `bin/yamato-seat-attach`。`yamato` への組み込みは未)。

design-p1 には、別の名前で書かれている箇所がある (`ship up / down / status`、`shift end`)。実装済みの名前は上の表 (`up` / `down` / `status` / `seat-stop`)。揃え方は P1 の実装で決める (§15)。

## 14. リスク

- Claude Code の background session と agent view は research preview で、仕様が週単位で変わる。Claude Code とのやり取りを 1 か所 (`src/yamato/claude.py`) に閉じ込めて、変更をそこで吸収する
- 待機中のまま誰も attach しないで約 1 時間経つと、席のプロセスは止められる (最後のターンの終わりから約 60 分。検証 C Q3)。シフト制と記録ベースの再開で吸収する (再開か新しいシフトかの規則は P1、design-p1 §5.3)。ただし Remote Control に繋がった席は止められない (§4.1)
- 使用量は席の数とシフトの頻度に比例して、サブスクの枠を消費する。シフトごとの使用量を記録する (§0 I7)
- 自律ループの暴走。稼働時間の上限で止める (§12.1)。予算の上限は既定では掛けない (headless の役割ごとに `max_budget_usd` を書けば掛けられる。P1)
- auto モードの classifier の判定は揺れる (検証 B、検証 D V2)。無人の権限の安全は、deny リストと PermissionRequest の全 deny (と、P1 の外を読む役割の dontAsk) が本命
- SessionStart の注入は、Claude Code の hook の出力の上限 (hook 1 本あたり 10,000 文字、検証 C Q1) に収める (§8.2)
- agent view の `state` は、席の最後の発言から「人間に何を求めているか」を意味づけしたラベルで、言い回しで変わる (検証 C Q5)。生死は pid、詰まりは `status` (`waiting` + `waitingFor`) と、idle の `blocked` で見る。完了の判定は記録 (handoff・board) で行い、`state` を使わない

## 15. 未決事項と、揃えること

**v1 で未決だったもののうち、決まったもの** (v2 で本文に反映済み)
- 稼働時間の各時刻の既定値: 終業は `time_limit`、猶予は `grace` (ひな形は 3h と 20m)。最終受付は design-p1 §9 の軽い形 (§12.1)
- `persistent` の席を入れ替える条件: 設定 `rotate:` (コンテキストの量、compaction、時間、日付。どれも off にできる。design-p1 §5.3〜5.4)
- 人間宛て通知の経路: `notify.via` に `slack` / `mac` / `windows` (複数可) と、任意の `command` (§7、design-p1 §2.4)
- admiral の形: どの艦にも属さない常駐の Claude のセッション。時間の上限は掛けない (決定 2026-09-27、§11)
- 会話ログ (transcript) の艦フォルダへの退避: しない (決定 2026-09-27)。代筆 (design-p1 §1.5) の根拠は `--reason` (owner の言葉をそのまま書いたもの) を正とする。transcript をコピーする hook は作らない

**まだ決まっていないこと**
- zellij で窓を開いている席は常駐する (attach で 1h 停止を免れる)。全席を開くか、見たい席だけ開くか
- fleet からの移行手順 (fleet を引退させる時期と手順)
- **【要検証】** design-p1 §11 の未確認のうち: サブスクの枠切れのとき、bg の席と `-p` がどうなるか (V5)。bg の席 + Remote Control からの `PushNotification` (V11)

**P0 の実装と design-p1 で、名前や置き場が食い違っていたもの: 決定済み (leader, 2026-09-26)**

基本は P0 の実装の名前に寄せる。design-p1 v3 は、この決定に合わせて書き換えた。例外は memory 本体だけ。

| 項目 | 決定 | 備考 |
|---|---|---|
| 席の終業 | `yamato seat-stop` (`seat-stop --rotate` も) | design-p1 の `shift end` は使わない |
| 艦の起動・終業・状況 | `yamato up` / `down` / `status` / `extend` / `halt`。`ship` は `create` だけ | `extend` / `halt` は P1 で足す |
| deny リスト | team.yaml の最上位 `deny:` | `permissions.deny:` は使わない |
| settings ファイル | 席ごと `.runtime/settings-<seat>.json` | `settings.json` / `settings.<role>.json` は使わない |
| memory 本体 | **例外**: design-p1 の `roles/<role>/memory.md` (同じ役割の席で共有) | P0 の `seats/<seat>/memory.md` からは、memory の棚卸しの task (design-p1 §10 の 8) で移す。それまでは P0 のまま |
| 注入の部品名 | `memory` | `role_memory` は使わない。memory 本体を移したあとは `roles/<role>/memory.md` を読む |
| owner | 予約名。役割には書けない | design-p1 §7.1 の調査艦の例から `owner: { agent: human }` を外した |
| 引き継ぎの安全網 | `seat-stop` が `handoff.md` の更新を確かめる (`seat_stop.require_handoff`) | Stop hook の `handoff_guard` は作らない |

## 16. 作る順番

1. **P0 記録と起動** (完了。main、PR #4): 艦フォルダ、`team.yaml`、board コマンド、席の起動 (`claude --bg` + SessionStart hook)、`send`、シフトの終わり (`seat-stop`)、時間の上限、使用量の記録。P0 は検証から始め (verify-p0-a / b)、E2E で owner の依頼 1 件を無人で通した (`e2e/e2e-p0.md`)。zellij の表示層は P2 の先行として入っている (PR #2)
2. **P1 チームとして回す**: 順番は design-p1 §10。(1) `events.jsonl` と `last_active` (2) 判断 `decide` (3) `run-headless` と `shift: headless` (4) 日報と通知 (5) captain の入れ替えと `send` の再開の規則 (6) admiral の CLI (7) 最終受付 (8) memory の棚卸し (9) `yamato worktree` と `pr open/merge`、開発艦のひな形に git の流れ (10) 調査艦のひな形と `trust:` のプロファイル
3. **P2 表示**: zellij の表示層の `yamato` への組み込み
4. **P3 運用**: 使用量と監査ログの集計 (日報の使用量の節は P1 で先に入る)

P0 と P1 まで作った時点で、本システムの開発そのものを本システムの開発チームにやらせる (dogfooding)。
