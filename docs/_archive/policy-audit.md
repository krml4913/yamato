# 仕組みが方針を強制している箇所の洗い出し

- 作成: 2026-09-26 / driver (task-policy-audit)
- 対象: `docs/design.md` (v1、§0 を含む) と `docs/design-p1.md` (v0)
- 基準: owner の方針 (2026-09-26、project memory `mechanism-not-policy`)。yamato の仕組み (コード) は **道具・記録・安全網** だけを持ち、**運用の方針は強制しない**。方針は team.yaml の設定・ひな形の既定値・役割プロンプトで決め、艦ごとに変えられるようにする
- 結果の反映先: `docs/design-p1.md` v1 (§0.3「design.md から方針に移すもの」と各節の書き直し)。design.md 本文は P0 の実装中なので、ここでは書き換えない

## 1. 分類の決め方

| 分類 | 意味 | コードで強制してよいか |
|---|---|---|
| **道具** | コマンドや生成物。呼ぶかどうか、いつ呼ぶかは使う側が決める | 強制しない。呼ばれたら動くだけ |
| **記録の整合性** | 記録が壊れない・食い違わないための検査 (board の固定の項目、ロック、追記のみのログ) | 強制してよい |
| **安全網** | owner が合意した暴走止め。時間の上限と、無人の席のダイアログ止まり対策 | 強制してよい。ただし deny リストや外すべき環境変数のように**中身が PJ で変わるものは、ひな形の既定値**として持ち、艦ごとに変えられる |
| **方針** | 誰が何をいつどう進めるか | 強制しない。移し先は次の 3 つ |

方針の移し先:
- **設定**: team.yaml の項目。yamato が値を読んで動きを変える (閾値、既定の宛先、自動で走らせるかどうか)。既定値はコードではなく**ひな形**が持つ
- **ひな形の既定値**: 設定と同じだが、特に「開発艦 / 調査艦のひな形ではこう書いてある」ことを指す。deny リスト、`trust:`、decisions 表など
- **役割プロンプト**: yamato が値を読まない、エージェントの振る舞いの約束。git の流れ、worktree を誰が使うか、何を decision にするか、など

判定の問い: 「別の PJ でこれが邪魔にならないか」。邪魔になりうるなら方針。

## 2. 洗い出しの結果

「今の書き方」は要旨。節番号は元の文書のもの (design = `design.md`、P1 = `design-p1.md` v0)。

### 2.1 design.md

| # | 箇所 | 今の書き方 | 分類 | 移し先 | 理由 |
|---|---|---|---|---|---|
| D1 | design §0 B1、§7 | send は inbox に記録し、止まっていれば起こす | 道具 + 記録 | そのまま | 配送そのもの。誰が誰に送るかは制限していない |
| D2 | design §0 B2 | 席の既定は auto + 無人でやらせない操作の deny リスト (チームごと) | 安全網 | deny の中身は**ひな形の既定値** (`team.yaml` の `permissions.deny`)。auto と PermissionRequest の全 deny はコード側の安全網 | owner の合意どおり。中身は PJ で変わる |
| D3 | design §0 B2 | 外部の文章を読む役割と merge などの権限を持つ役割を分ける | 方針 | ひな形の既定値 (`trust:` の組み合わせ) | 分け方は艦の構成で決まる。調査艦のひな形が既定で分ける |
| D4 | design §0 B3 | per_task の席は終業処理のあと自分を停止。per_task 宛ての send は常に新しいシフト | 道具 | そのまま (shift の種類の定義) | shift は役割ごとに設定で選ぶ。選んだ種類の意味を決めているだけ |
| D5 | design §0 B4、§12.1 | 終了時刻はデータで持ち、hook と send が毎回確認して止める | 安全網 | そのまま。時刻 (`time_limit` / `last_call` / `grace`) は設定 | owner が合意した安全網 |
| D6 | design §0 B5、§8.1 | shift の方式を役割ごとに選ぶ | 道具 (設定) | そのまま | 既に設定になっている |
| D7 | design §0 I1 | 書き込みはロック 1 本で直列化、inbox の既読はカーソル | 記録の整合性 | そのまま | |
| D8 | design §0 I2 | チームの git 規律を明示して bg の PR 自動作成を上書き。「タスク = ブランチ」を board の項目に。merge と衝突解消の担当は team.yaml | 方針 | git 規律は**役割プロンプト** (ひな形)。`branch` / `pr` は**任意の項目** (固定の項目にしない)。担当は設定 | git の流れは PJ ごとに違う (fleet の `dev-flow-is-project` と同じ) |
| D9 | design §0 I3 | repo のないチームは `bgIsolation: none` 固定 | 道具 (技術的な制約) | そのまま | repo が無ければ worktree は作れない。方針ではなく事実 |
| D10 | design §0 I4 | captain も記録から起き直す。status に最後に動いた時刻 | 道具 + 記録 | そのまま | |
| D11 | design §0 I5 | 記録は席ごと。memory 候補は memory-inbox。未読 inbox と担当ビューに上限 | 記録 + 安全網 | 上限の数値は設定 (既定値つき) | 起動時に読む量の上限は design §2 の原則。数値は PJ で変わりうる |
| D12 | design §0 I6、§9 | 当面、人間は captain とだけ話す / 人間が判断を返す相手は PM | 方針 | **ひな形の既定値** (`talk` の既定の相手が hub) + 役割プロンプト | 人間が impl に直接話したい PJ もある。道具としては任意の席と話せるようにする |
| D13 | design §0 I7 | シフトごとの使用量を 1 行記録 | 記録 | そのまま | |
| D14 | design §3 | 協調は司令塔型。メンバー同士が勝手にタスクを取り合わない | 方針 | 役割プロンプト | send や board の assignee を「captain だけが変えられる」とコードで縛らない |
| D15 | design §4 | ファイル隔離 = bg の自動 worktree | 方針 | 設定 (役割ごとの `isolation:`、ひな形の既定値) + 道具 `yamato worktree` | owner の決定 (worktree は仕組みで割り当てない) |
| D16 | design §4.1 | 艦の設定は起動時のフラグで渡し、作業 repo と `~/.claude` に書き込まない | 道具 (yamato 自身の振る舞い) | そのまま | エージェントの運用を縛るものではない。「席が repo 本体に書かない」とは別 (D25) |
| D17 | design §5 | `decisions` 表、`hub:` | 設定 | そのまま | 既に設定 |
| D18 | design §6.2 | frontmatter は board コマンド経由でのみ変更、値をチェック | 記録の整合性 | そのまま。ただし検査するのは**固定の項目だけ**。チームの列・種類・追加の項目は team.yaml から読む | |
| D19 | design §6.2 | done になった項目は archive に移す | 方針 (いつ片付けるか) | 道具 `board archive` + 設定 `board.archive_on_done` (既定 true) | 片付けの時期は艦で違ってよい。起動時に読む量の上限は担当ビューの上限 (D11) が守る |
| D20 | design §6.3 | handoff の長さに上限 (例 40 行) | 安全網 | 数値は設定。超えたら**注入で切って警告** (書き込みを拒否しない) | 上限を超えた引き継ぎを拒否すると、終業処理が失敗して何も残らない |
| D21 | design §6.6 | memory に書き込むのは PM の週次の棚卸しだけ | 方針 | 設定 (`memory.curate_every` / `curate_at`、反映役 `memory.applier` 既定 hub) + 役割プロンプト | owner の決定: 案は各役割の headless シフト、反映は captain。ただしコードは反映役を検査しない |
| D22 | design §6.7 | 秘密情報は記録に書かない | 方針 (約束) | 役割プロンプト (共通の節) | コードでは検査できない。GH_TOKEN を環境から外すのは安全網 (D26) |
| D23 | design §8.2 | 起動時に注入するものの一覧。PM は加えてカンバンと日報 | 方針 | 設定 (役割ごとの `inject:`、ひな形の既定値) | 何を読ませるかは役割による。合計の上限は安全網として残す |
| D24 | design §8.3 | Stop hook は per_task が引き継ぎなしで終わろうとしたときの安全網 | 安全網 | 設定で外せる (`handoff_guard:` 既定 on) | 記録から再開する前提を守る網。ただ短い headless の仕事には重い |
| D25 | (verify-p0-b のひな形) | 席が作業 repo の `.claude/**` を書き換えない | 安全網 | **ひな形の deny の既定値** | owner の決定: repo 本体への書き込み禁止もコードで強制しない |
| D26 | design §0 B2 + verify-p0-b | `env -u GH_TOKEN` で起動 | 安全網 | ひな形の既定値 (`env_unset:`) | gh を使わせたい役割もある。既定で外し、艦ごとに変えられる |
| D27 | design §10 | 生きている席にだけ attach する | 道具 (技術的な制約) | そのまま | 止まった席に attach すると古いシフトが蘇る (spike) |
| D28 | design §11 | admiral はチームの中身に踏み込まない | 方針 | admiral の skill / プロンプト | owner の決定: 移行期間は fleet の leader が CLI を叩いて兼ねる。CLI で admiral の send を縛らない |
| D29 | design §12 | 1 日の回り方 | 例 | そのまま (例として読む) | 手順の例であって仕組みではない |
| D30 | design §12.1 | 最終受付のあと「PM は新しい大きな割り当てをやめる」 | 方針 | 役割プロンプト (通知の文面は道具) | 「大きな割り当て」は機械で判定できない。design-p1 §9 も拒否しない |
| D31 | design §14 | 予算の上限は掛けない | 設定 | 役割ごとの `max_budget_usd` (既定なし) | 既に design-p1 §4.4 で設定にしている |
| D32 | design §15 | 人間宛て通知の経路 | 設定 | `notify.via: [slack, mac, windows]` | owner の決定 |

### 2.2 design-p1.md (v0)

| # | 箇所 | 今の書き方 | 分類 | 移し先 | 理由 |
|---|---|---|---|---|---|
| P1 | P1 §1.1 | decider は開いたときに解決して固定。止まる側は `blocked_on` の片方向 | 記録の整合性 | そのまま | 宛先が途中で変わる・向きが食い違うのを防ぐ |
| P2 | P1 §1.2 decide close | decider 本人か、`--by owner` のときは hub の席だけが閉じられる。それ以外は拒否 | 方針 | **記録にする**: 閉じた席 (`closed_by`) と代わりに決めた人 (`on_behalf_of`) を必ず書き、decider 以外が閉じたら events に残して日報の「異常」に出す。拒否はしない。誰が閉じてよいかは役割プロンプト | 代筆の役を captain 以外に持たせる艦もある。整合性に必要なのは「誰が閉じたか」が残ることだけ |
| P3 | P1 §1.2 decide open | 人間宛ては 1 件ずつ通知しない (`notify.decisions: digest` 既定) | 設定 | そのまま (既定値はひな形) | 既に設定 |
| P4 | P1 §1.2 close の 4 | 止まっていたタスクを戻し、担当に send | 道具 | そのまま | 呼ばれたときの後処理 |
| P5 | P1 §1.4 | 何を decision にするかは role prompt と decisions の `when` | 方針 → 既に役割プロンプト | そのまま | |
| P6 | P1 §1.5 | owner の入口は `yamato talk <ship>` 1 本 (captain) | 方針 | `yamato talk <ship> [<seat>]`。既定の相手は hub (ひな形) | D12 と同じ |
| P7 | P1 §2.2 | captain は日報の「一言」「明日」だけ書き、他の節は直さない | 方針 | 役割プロンプト | 事実の節は `report daily` が機械的に作る (道具)。直すかどうかは約束 |
| P8 | P1 §2.2 | captain の `shift end` がその日の最後のシフトなら `report daily` を呼ぶ | 方針 (いつ作るか) | 設定 `report.daily: on_down` (既定) / `off` | 日報の要らない艦もある (短い調査艦など) |
| P9 | P1 §2.2 | captain がいなくても `ship down` と強制停止が facts-only の日報を作る | 安全網 | 設定で外せる (`report.daily: off` なら作らない) | owner に必ず何か届く網。日報を使わない艦では不要 |
| P10 | P1 §2.4 | 通知は `notify.command` 1 本 | 設定 | `notify.via: [slack, mac, windows]` (複数可) + 任意の `command` | owner の決定。agent-fleet の `src/fleet/notify.py` を移植する |
| P11 | P1 §3.3 | 棚卸しの反映は captain が `memory apply` | 方針 | 道具 `memory apply` は誰が呼んでも動く。呼ぶ役は設定 `memory.applier` (既定 hub) で、注入と役割プロンプトに使うだけ | owner の決定は「反映は captain」。それを既定値で表し、コードで呼び出し元を検査しない |
| P12 | P1 §3.3 | knowledge.md は captain 自身が書く | 方針 | 役割プロンプト | |
| P13 | P1 §3.4 | 7 日 / 30 件で captain が curate を呼ぶ | 方針 | 設定 (既に `memory:` で変えられる) + 役割プロンプト | 既定値はひな形へ |
| P14 | P1 §3.5 | memory.md が上限を超える案は `memory apply` が拒否 | 安全網 | そのまま。上限の数値は設定 | 起動時に読む量の上限を守る網。拒否しても案は残るので失うものがない (D20 と違う) |
| P15 | P1 §4.2 | ラッパーの終了報告の宛先は captain | 方針 (宛先) | 設定 `report_to:` (既定 hub)。**報告を yamato の定型文にすることは安全網**として残す | 送り手に返したい艦もある。定型文は外部の文章を会話に入れない網 (P1 §7.2) |
| P16 | P1 §4.2 | 起動に `env -u GH_TOKEN`、`--permission-prompts none` | 安全網 | `env_unset` はひな形の既定値 (D26)。`--permission-prompts none` はコード側 (無人の席のダイアログ止まり対策) | |
| P17 | P1 §4.3 | 同じ席で同時に 2 シフトは走らせない | 記録の整合性 | そのまま | 席の記録 (handoff、roster) が壊れる |
| P18 | P1 §5.2 | captain の空白 30 分、`last_active` 20 分で赤 | 道具 (表示) | 閾値は設定 | 表示と記録だけで、止めない |
| P19 | P1 §5.2 | 外部スケジューラで見張る案 | ― | 使わない (owner の決定) | |
| P20 | P1 §5.3 | 止まった persistent の席を resume せず新しいシフトにする条件 (印・300k・1 時間・日付) | 方針 | 設定 (役割ごとの `rotate:`、既定値はひな形) | 「日付が変わったら新しく」は運用の好み |
| P21 | P1 §5.4 | Stop hook が 300k / compaction / 8h で「入れ替われ」を返す | 方針 | 設定 (役割ごとの `rotate:`、各条件を off にできる) | 入れ替えの基準は役割と PJ で違う |
| P22 | P1 §5.5 | 同じ本文を 2 回続けて送ろうとしたら send が拒否 | 方針 | **拒否しない**。記録と警告だけ (events と日報の異常)。閾値は設定 | 同じ文面を送り直したいこと (念押し、再依頼) はある。SendMessage 側の重複破棄もある |
| P23 | P1 §5.5 | 10 分に 6 通で警告 | 道具 (記録と警告) | 閾値は設定 | 止めない |
| P24 | P1 §6.1、§6.2 | admiral が艦に送ってよいのは定型文だけ | 方針 | admiral の skill / プロンプト | D28 と同じ。CLI では縛らない |
| P25 | P1 §6.2 | `ship up` は captain だけを起こす | 道具 (既定の動作) | そのまま。`--seats` で他の席も起こせるようにする | 起こす席は艦で違ってよい |
| P26 | P1 §6.2 | `ship create` が workspace の trust を確かめ、無ければ止まる | 道具 (技術的な制約) | そのまま | bg の席は trust を対話で通せない (検証 B) |
| P27 | P1 §7.1 | Haiku の席は使わない (sonnet 以上) | 道具 (技術的な制約) | `ship create` の警告 | auto が使えない (検証 B)。拒否ではなく警告 |
| P28 | P1 §7.2 | `trust: external` の役割は Web 可・Bash は yamato の決まったコマンドだけ・send 不可・board の構造を変えられない | 安全網 | **ひな形の既定値** (`trust:` はプロファイル名。中身の allow / deny / tools はひな形の `profiles:` に書く) | 外部の文章に乗っ取られても何もできない席を作る網。ただし何を許すかは艦で変えてよい |
| P29 | P1 §7.2 | editor は外を読まない | 方針 | ひな形の既定値 (`trust: clean`) | |
| P30 | P1 §7.1、§7.2、Q6 | `publish` の decider は owner に固定し、ひな形で変えられない | 方針 | **decisions 表で艦ごとに決める** (ひな形の既定は owner) | owner の決定。bmweb にとりあえず投稿させて owner が携帯で見る運用もありうる |
| P31 | P1 §7.3 | 差し戻しは 2 回まで | 方針 | 役割プロンプト (editor) | |
| P32 | P1 §8.1 | タスク = ブランチ、ブランチ名 `yamato/<ship>/T-042`、impl はそのブランチでだけ commit | 方針 | 役割プロンプト (ひな形)。道具 `yamato worktree add` はブランチ名を引数で受け、既定の名前だけ持つ | D8 と同じ |
| P33 | P1 §8.1 | PR は `yamato pr open` だけ、生の `gh pr create` は deny | 方針 + 安全網 | `pr open` は道具 (項目の `pr` と column を書く便利さ)。`gh pr create` の deny は**ひな形の既定値** | 生の gh で PR を作ってよい艦もある |
| P34 | P1 §8.1 | merge は `yamato pr merge` だけ、生の `gh pr merge` は全席で deny | 安全網 | ひな形の deny の既定値 (「全席で」を外す) | 無人の席の merge を止めるのは網だが、中身は艦で変えられる |
| P35 | P1 §8.1 | シフトの終わりに必ずブランチを push | 方針 | 役割プロンプト | `rm` が未 push を拒否するのは Claude Code 側の制約 (道具の事実) |
| P36 | P1 §8.2 | yamato がタスクごとに worktree を作り、席をそこで起こす (案 A) | 方針 | **道具 `yamato worktree` を提供するだけ**。誰がいつ使うかは役割プロンプト | owner の決定 |
| P37 | P1 §8.3 | `pr merge` は呼び出し元が `merger` の席か確かめる | 方針 | 記録にする (`merged_by` を項目と events に書く)。拒否しない | 誰が merge を打つかは艦で違う |
| P38 | P1 §8.3 | `pr merge` の前提: reviewer の承認・CI 緑・owner の判断が閉じている | 方針 | 設定 `git.merge_requires: [review, ci, decision]` (ひな形の既定値)。満たさないときは拒否するが、`merge_requires: []` で外せる | owner が「承認なしで入れてよい」艦を作れるように |
| P39 | P1 §8.3 | merge は 1 本ずつ (ロック) | 記録の整合性 | そのまま | 同時の merge で board と PR の状態が食い違う |
| P40 | P1 §8.3 | reviewer の承認で merge の D 項目を自動で開く | 方針 | 設定 `git.merge_decision: auto` (ひな形の既定) / `off` | |
| P41 | P1 §8.3、§8.4 | 同じファイルを触る後続は merge まで割り当てない / `touches` の重なりを警告 | 方針 / 道具 | 割り当ての規則は役割プロンプト。警告は道具 (拒否しない) | |
| P42 | P1 §9 | 最終受付の注入と、send の本文に「終了まで X 分」を足す | 安全網 (時間の上限の一部) | そのまま。割合は設定 | 拒否はしない |

## 3. P0 の実装に直接効く指摘

P0 のコードで強制してしまいそうなもの。leader から P0 driver に伝える。

1. **send で送り手と宛先の組み合わせを検査しない** (D12、D14、D28、P24)。captain 以外から人間へ、メンバー同士、admiral から中身の指示、どれもコードでは通す。記録 (inbox、events) に送り手を残すだけ
2. **board の検査は固定の項目だけ** (D8、D18)。`branch` / `pr` / `worktree` / `touches` を固定の必須項目にしない。列・種類・追加の項目は team.yaml から読む。`assignee` を変えられる席を captain に限らない
3. **deny リスト・`env_unset`・`bgIsolation` をコードに書き込まない** (D2、D15、D25、D26)。team.yaml (ひな形がそのまま写した既定値) から `.runtime/settings.json` を生成する。コード側に固定で持つのは PermissionRequest の全 deny hook と hook の配線だけ。repo のない艦の `bgIsolation: none` は技術的な制約なのでコードで決めてよい (D9)
4. **git 規律の文面を `agents.json` の生成コードに埋め込まない** (D8)。役割プロンプトはひな形の役割ファイルから読む
5. **起動時の注入の中身と上限は設定で持つ** (D11、D20、D23)。上限を超えたら切って警告を付ける。handoff が長いことを理由に `shift end` を失敗させない
6. **呼び出し元の特定 (`CLAUDE_CODE_SESSION_ID`) は記録のために使い、許可の判定に使わない**。例外は「自分の席の記録を書く」(shift end、memo) のような、席の取り違えを防ぐ整合性の検査だけ
7. **通知は `notify.via` の複数指定を前提にした口にしておく** (D32)。人間宛ての send が通知を 1 本呼ぶだけなら、P0 は口だけでよい

## 4. 残る強制 (コードに持つもの) の一覧

- 記録の整合性: board の固定の項目の検査、ロック 1 本の直列化、inbox の既読カーソル、decider の固定と `blocked_on` の片方向、閉じた判断を書き換えない、同じ席で 2 シフトを走らせない、merge を 1 本ずつ
- 安全網 (コード側): 時間の上限 (deadline・最終受付・終業・強制停止)、無人の席の PermissionRequest の全 deny と `--permission-prompts none`、headless の終了報告を yamato の定型文にする、memory / knowledge の上限を超える反映の拒否
- 安全網 (ひな形の既定値、艦ごとに変更可): deny リスト、`env_unset`、`trust:` のプロファイルの中身、`merge_requires`
- 技術的な制約 (方針ではない): repo のない艦の `bgIsolation: none`、生きている席にだけ attach、workspace の trust の確認、Haiku で auto が使えないことの警告
