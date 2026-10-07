# yamato 引き継ぎ (2026-09-26 時点)

- 対象: 初めて yamato に触る人とエージェント (新しい fleet の leader、yamato の開発艦の pm / impl)
- 時点: main の `f736f7d` (PR #27 まで)。P0・P1 が入り、次は dogfooding (design.md §16 の末尾)
- この文書は入口と要約だけ。事実の正本は各 docs とコード。食い違えばそちらが正しい (この文書を直す)

## 1. yamato とは

役割の違う複数の AI エージェントを「艦」として常設し、backlog を毎日消化させるための薄い層。
実体は Claude Code の background session (`claude --bg`) と `claude -p` の上に乗る Python の CLI (`./yamato`) と、艦フォルダの記録 (markdown・jsonl)。
常駐のデーモンは持たない。agent-fleet (leader → driver の 1 段) の後継で、yamato が fleet の役目を果たせるようになったら fleet を引退させる (design.md §1)。

| 用語 | 意味 |
|---|---|
| ship (艦) | チーム。艦フォルダ (既定 `~/yamato/<name>/`) と `team.yaml` で定義する |
| captain (艦長) | 艦の司令塔。`team.yaml` の `hub:` の役割 (開発艦は `pm`、調査艦は `editor`) |
| admiral (提督) | 窓口。艦の出撃・帰投・構成の変更・全艦の一望・判断の代筆。どの艦にも属さない常駐の Claude のセッション (`_admiral/`、`yamato admiral`、D-011。docs/admiral.md) |
| owner | 人間 |
| 席 (seat) | 役割を担う名前付きセッション `<ship>.<seat>`。`count: n` の役割は `<role>-1..n` |
| シフト | 席のセッション 1 回分。起動 → 記録を読む → 働く → 引き継ぎを書く → 終わる。`shift:` は `per_task` / `persistent` / `headless` |

## 2. 読む順番

| 順 | 文書 | 何のために |
|---|---|---|
| 1 | この文書 | 全体の地図 |
| 2 | [README](../README.md) | 使い方・コマンドの表・team.yaml の項目・テストの流し方 |
| 3 | [design.md](design.md) の §0 と §2.1 | **§0 が最新の決定** (本文と食い違えば §0)。§2.1 が仕組みと方針の分け方 |
| 4 | [design-p1.md](design-p1.md) | P1 (判断・日報・memory・headless・入れ替え・admiral・worktree/pr・調査艦) の詳細。§11 が要検証の判定の一覧。**design.md §0 が前提で、食い違えば §0** |
| 5 | [_archive/policy-audit.md](_archive/policy-audit.md) | 何を仕組みに残し何を方針に移したか。§4 がコードに残る強制の一覧 (結論は design.md §2.1 とこの文書 §4 に反映済み) |
| 6 | [verify-p0-a](verify/verify-p0-a.md) / [b](verify/verify-p0-b.md) / [c](verify/verify-p0-c.md)、[verify-p1-d](verify/verify-p1-d.md) | Claude Code の挙動の実機検証 (起動レシピの根拠)。§5 の要点の出典 |
| 7 | [e2e-p0](e2e/e2e-p0.md) / [e2e-p1](e2e/e2e-p1.md) / [e2e-headless](e2e/e2e-headless.md) | 本物の claude で通した記録と、観測したが直していないこと。E2E の手順もここ |
| 8 | [events.md](events.md) | `events.jsonl` の行の形式 (日報・監視が読む) |
| 9 | [admiral.md](admiral.md) | admiral を務めるときの約束とコマンド |
| - | [_archive/](_archive/README.md) | 初期の検討の記録 (research-claude-primitives.md、review-da-v0.md、spike-zellij-attach.md)。背景 (Claude Code の仕組みの調査、v0 への DA レビュー、zellij の検証)。必要なときだけ |

実装の地図: Claude Code とのやり取りは `src/yamato/claude.py` 1 か所に閉じ込めてある (design.md §14)。CLI の入口は `src/yamato/cli.py`。ひな形は `src/yamato/templates/{dev,research}/`。

## 3. できていること

入口のコマンドとモジュール。使い方の詳細は README の表。

| 機能 | 入口 | モジュール | PR |
|---|---|---|---|
| 艦フォルダをひな形から作る (trust の確認つき) | `ship create` | `ship.py` | #4, #18 |
| `.runtime/` の生成 (team.json・agents.json・席ごとの settings) | `up` | `runtime.py`, `team.py` | #4 |
| 席の起動・resume・終業・強制停止・状態 | `up` / `down` / `status` / `seat-stop` | `seat.py`, `claude.py` | #4, #19, #26, #27 |
| 送信と起こし方 (記録 → 送り手が SendMessage / 止まっていれば起動・resume) | `send` / `inbox` | `seat.py`, `inbox.py` | #4, #19, #24 |
| board (固定の項目を検査) | `board add/set/show/list/mine/note` | `board.py` | #4 |
| 作業ログ・引き継ぎ | `log`、`seat-stop` が handoff.md を確認 | `seat.py` | #4 |
| 時間の上限 (deadline・終業・猶予・強制停止・最終受付) | `up --for` / `extend` / `halt` | `deadline.py`, `hooks.py` | #4, #16, #19 |
| 起動時の注入 (上限つき、hook 2 本) | `hook session-start` / `session-start-knowledge` | `inject.py`, `hooks.py` | #4, #26 |
| 使用量の記録 (シフトごとに 1 行) | `usage.jsonl` | `usage.py` | #4 |
| events.jsonl と lastActive | (各コマンドが書く) | `events.py`, `roster.py` | #7, #17 |
| 判断 (decision) と decisions/log.md | `decide open/close/list/categories` | `decide.py` | #12 |
| captain の日報と安全網 | `report daily/send`、`report.daily: on_down` | `report.py` | #13, #24 |
| owner への通知 (slack / mac / windows / command) | `notify.via` | `notify.py` | #11 |
| headless のシフト (`claude -p` 1 回) | `shift: headless`、`run-headless` | `headless.py` | #14 |
| admiral の CLI | `ships` / `extend` / `halt` / `talk` / `up --seats` | `admiral.py` | #16, #27 |
| 入れ替え・空回り・孤児・captain の空白・最終受付・`send --cwd` | `rotate:`、`watch:`、`seat-stop --rotate` | `rotate.py`, `monitor.py` | #19 |
| memory の棚卸し | `memo` / `memory status/curate/apply/migrate` | `memory.py` | #20, #27 |
| worktree と PR | `worktree add/path/list/rm`、`pr open/merge` | `worktree.py`, `pr.py` | #8, #17 |
| 調査艦のひな形と trust のプロファイル | `ship create --template research`、`profiles:` | `templates/research/`, `runtime.py` | #18 |
| zellij の表示層 | `view layout/attach` | `view/` | #2, #10 |
| 出撃のバナー | `up` / `ship create` | `banner.py` | #21 |

PR の一覧 (すべて merge 済み):

| PR | 中身 | PR | 中身 |
|---|---|---|---|
| #1 | docs: P1 設計 v0 | #15 | docs: design-p1 の名前を P0 に揃える (v3) |
| #2 | zellij の seat-attach と layout (P2 先行) | #16 | P1-6 admiral の CLI |
| #3 | docs: policy-audit と design-p1 v1 | #17 | worktree / pr を events に出す |
| #4 | **P0 の実装** (記録・起動・send・終わり・上限) | #18 | P1-10 調査艦のひな形と trust のプロファイル |
| #5 | docs: 検証 D の結果 | #19 | P1-5/7 入れ替え・空回り・孤児・最終受付・`send --cwd` |
| #6 | docs: 検証 D を design-p1 に反映 (v2) | #20 | P1-8 memory の棚卸し |
| #7 | P1-1 events.jsonl と lastActive | #21 | 出撃のバナー |
| #8 | P1-9 worktree と pr open/merge | #22 | P1 の E2E |
| #9 | docs: design.md v2 | #23 | unit test の高速化 (45 秒 → 9 秒) |
| #10 | `yamato view` を CLI に組み込む | #24 | E2E の残り C・D・E (inbox の見張り・env_unset・日報の更新) |
| #11 | P1-4a 通知 | #25 | docs: 検証 C の結果 |
| #12 | P1-2 判断 | #26 | 検証 C の反映 (注入 2 本・起動失敗の検知・詰まり) |
| #13 | P1-4b 日報 | #27 | #26 のレビューの残り |
| #14 | P1-3 headless | | |

まだ無いもの: 会話ログを保存する SessionEnd hook (design.md §15)、使用量と監査ログの集計 (P3)。`git.merge_decision: auto` は D-021 (案 b) で設定ごと外した (README)。

## 4. 設計の原則

**mechanism-not-policy** (owner の方針、design.md §2.1): yamato のコードが持つのは道具・記録の整合性・安全網だけ。誰が何をいつどう進めるか (git の流れ、worktree を誰が使うか、何を判断にするか、captain の振る舞い) は、team.yaml の設定・ひな形の既定値・役割プロンプト (`roles/<role>.md`) に置き、艦ごとに変える。判定の問いは「別の PJ でこれが邪魔にならないか」。

コードで強制しているもの (policy-audit §4 と、そのあと足したもの):
- 記録の整合性: board の固定の項目の検査、ロック 1 本の直列化、inbox の既読カーソル、decider の固定と `blocked_on`、閉じた判断を書き換えない、同じ席で 2 シフトを走らせない、merge を艦で 1 本ずつ
- 安全網: 時間の上限 (deadline・最終受付・終業・強制停止)、無人の席の PermissionRequest の全 deny、headless の終了報告を定型文にする、`inject.limits.memory` / `knowledge` を超える反映の拒否、注入の上限
- そのあと足したもの: `send: false` のプロファイルの席からの `send` を断る (#18)、Stop hook の watcher が席の外からの未読で idle の席を起こす (#24)、`env_unset` を席の settings の `env` に空文字で書く (#24)、起動・resume のあとに pid を確かめて失敗を返す (#26)、`seat_stop.require_handoff` / `require_delivery` の確認 (P0、設定で外せる)
- ひな形の既定値 (艦ごとに変えてよい): deny リスト、`env_unset`、`trust:` のプロファイルの中身、`merge_requires`、`decisions` の表、`remoteControlAtStartup: false`
- 技術的な制約: repo のない艦の `bgIsolation: none`、生きている席にだけ attach、workspace の trust の確認、Haiku で auto が使えないことの警告

## 5. Claude Code について分かっている事実

Claude Code 2.1.283 での観測。research preview なので変わりうる (design.md §14)。

| 事実 | 出典 |
|---|---|
| 起動レシピ: `claude --bg --name <ship>.<seat> --agent <role> --agents '<json>' --model … --setting-sources project,local --settings <席の settings> --add-dir <ship> -- "<prompt>"`。`--add-dir` は後ろを食うので `--` が要る。settings は 1 本にまとめる | [verify-p0-b](verify/verify-p0-b.md) の起動レシピ、design.md §4.1 |
| `--setting-sources project,local` でユーザー設定 (plugin hooks・CLAUDE.md など) を席に持ち込まない。作業 repo の設定は効く | verify-p0-b Q3 |
| resume は **pid が消えるのを待ってから、フルの sessionId で** `--resume <id> --bg`。短い id や stop 直後だとフラグ抜きのコピーになる (`started a copy`) | verify-p0-a Q2、verify-p0-b Q2 |
| 生死は pid で見る。`state` は席の発言の意味づけのラベルで、詰まりは `status: waiting` + `waitingFor` と idle の `blocked` | [verify-p0-c](verify/verify-p0-c.md) Q5 |
| `claude --bg` は worker が起動前に落ちても exit 0。起動のあと `claude agents --json` で確かめる | verify-p0-c Q5 |
| 無人の席: auto + deny リスト。`ask` ルールは auto でも止まる。`--permission-prompts none` は `--bg` に効かないので PermissionRequest hook で全 deny。Haiku は auto を使えない | verify-p0-b Q1 |
| auto では `Bash(...)` の allow で絞っても他の Bash が止まらない。外を読む役割は **dontAsk** + allow + `tools` で組む | [verify-p1-d](verify/verify-p1-d.md) V7 |
| idle の席への SendMessage は `crossSessionInbound: "accept"` が要る。席が自分を止めるのは遅延 stop (`seat-stop`) | verify-p0-a Q1・Q3 |
| SessionStart hook の注入は **hook 1 本あたり 10,000 文字** (文字数、合算ではない)。超えると約 2KB のプレビューに化ける | verify-p0-c Q1 |
| bg の席は daemon の環境で動き、起動側の `env -u` は効かない。席の Bash に効かせるのは settings の `env`。ただし席のプロセスの環境には daemon の値が残る | verify-p0-c Q2、[e2e-p1](e2e/e2e-p1.md) の D と追記 |
| idle の席は最後のターンから約 60 分で止められる。attach 中と **Remote Control に繋がった席は止まらない** (4 時間 48 分生存) | verify-p0-c Q3 |
| 席ごとの Remote Control は settings の `remoteControlAtStartup: false` で外し `--remote-control` で足せる | verify-p1-d V10 |
| auto モードの分類器は `yamato pr merge` を「レビューなしの merge」として揺れて止める (同じ席で通る日と止まる日がある)。merge を打つ役の settings の allow に `Bash(<yamato> pr merge*)` を足すと通る (ひな形は `profiles.merger`、reviewer が使う) | D-026 (2026-09-27 に yamato-dev で観測) |
| `-p` は `--bare` を付けない (サブスクで未ログイン)、`< /dev/null` を付ける。SIGTERM では結果 JSON が出ない (使用量は transcript から)。SessionEnd hook の待ちは 1.5 秒 | verify-p1-d V1・V4 |
| `-p` に呼び出し元のセッションの環境変数を渡すと transcript が保存されない (`claude.PRINT_CALLER_ENV` を外す) | [e2e-headless](e2e/e2e-headless.md) の見つかったこと 1 |
| linked worktree の trust は main repo から引き継がれる。trust は git root ごとで、bg の席は対話で trust できない | verify-p1-d V6、verify-p0-b |
| 艦フォルダが repo の中にあると、自動 worktree に記録が書かれて元に残らない (`bgIsolation: none` で直る) | verify-p0-b Q4 |
| `PushNotification` は `-p` からは送られない | verify-p1-d V11 |

## 6. 決まっている名前 (design.md §15)

P0 の実装の名前に寄せた (leader の決定、2026-09-26)。例外は memory 本体だけ。
- 席の終業は `seat-stop` (`--rotate` も)。`shift end` は使わない
- 艦の操作は `up` / `down` / `status` / `extend` / `halt`。`ship` は `create` だけ
- deny リストは team.yaml の最上位 `deny:`。settings は席ごとの `.runtime/settings-<seat>.json`
- memory 本体は `roles/<role>/memory.md` (同じ役割の席で共有。P0 の `seats/<seat>/memory.md` からは `memory migrate`)。注入の部品名は `memory`
- `owner` は予約名で役割に書けない
- 引き継ぎの安全網は `seat-stop` が確かめる (`seat_stop.require_handoff`)。Stop hook の `handoff_guard` は作らない

## 7. 残っている課題・未確認

- **V5 枠切れ**: サブスクの枠に当たったときの bg の席と `-p` の振る舞いは未確認 (404 の代用観察で判定の規則だけ作った。design-p1 §11)
- **V11**: bg の席 + Remote Control からの `PushNotification` は未確認
- **席のプロセスの GH_TOKEN**: 席の Bash では空になるが、席の claude のプロセスの環境には daemon の値が残り、同じユーザーの `ps eww` で読める。完全に渡さないなら daemon を `GH_TOKEN` の無い環境で起動する (owner の手元の運用。README、e2e-p1 の追記)
- **e2e-p1 の「観測したこと」**: pm が SendMessage で受け取ると `yamato inbox` を読まず未読が残る (P0 からの既知)、team.yaml の変更は `up` まで効かない、`ship create` がひな形の count のまま `seats/` を作る、止める席が無い `halt` / `down` は events に残らない、など
- **実機で見ていない経路** (単体テストだけ): Stop hook の block による終業の指示、watchdog による猶予切れの自動停止 (e2e-p0)、headless の時間切れ・実行中の send (e2e-headless)
- **design.md §15 の未決**: zellij で全席を開くか見たい席だけか、会話ログの SessionEnd hook、fleet からの移行手順 (引退の時期と手順)
- **P2 / P3 の候補** (design.md §16): P2 は表示 (`yamato view` は #10 で CLI に入った。design.md §13 の「組み込みは未」は古い)、P3 は使用量と監査ログの集計
- docs の古い箇所: README の worktree の節の「`send --cwd` はまだ無い」は #19 で入ったので古い (README の `send` の行が正しい)

## 8. 開発のやり方

- 言語は Python 3.11+、pip install は不要 (PyYAML は `vendor/`)。docs は日本語。repo (krml4913/yamato) は private
- テスト: `python3 -m unittest discover` が正 (約 450 件、目安 10 秒)。`--durations 10` で遅い上位を見る。`python3 -m tests.parallel` はモジュールごとに並列。**速く保つ**: 実時間で待たず、定数を `mock.patch.object` で縮める。偽の claude (`tests/fake_claude.py`) は `tests/helpers.py` の `patch_fast` で同じプロセスで動く。git のテストは一時 repo を setUpClass で 1 回作ってコピーする (README「テストの実行」)
- E2E: 本物の claude で確かめることは unit test ではなく E2E でやる。手順は [e2e-p0](e2e/e2e-p0.md) の「手順」(使い捨ての repo を trust → `ship create --path` → `send` → `up --for 20m` → 見るだけ)。`YAMATO_HOME` を scratchpad に向ける、gh は `$YAMATO_GH` で偽物に差し替える ([e2e-p1](e2e/e2e-p1.md) の手順)。終わったら作ったセッションを stop + rm し、`claude agents --json --all` で 0 件を確かめる
- PR の流れ (今): fleet の driver が worktree で作業して PR を出す → fleet の leader がレビューして merge。設計の根幹に触る変更は事前に相談する。CI は無い (`.github/` なし) ので、テストは手元で流す

## 9. dogfooding の始め方

yamato の開発を yamato の開発艦にやらせる (design.md §16)。以下は手順の案で、まだ一度も通していない。

1. 艦を作る: `./yamato ship create <name> --template dev --workspace ~/dev/yamato` (艦フォルダは既定の `~/yamato/<name>/`。repo の外に置く)。艦の名前は `task` を避ける (fleet のブランチ `yamato/task/<task>` と、艦のブランチ `yamato/<ship>/<item>` が重なる)
2. 艦フォルダを整える: `team.yaml` (model・`time_limit`・`notify.via`)、`charter.md`。`knowledge.md` には [dogfood-knowledge.md](dogfood-knowledge.md) を写す (yamato の repo には CLAUDE.md が無く、席はユーザー設定の CLAUDE.md も読まないので、knowledge.md が約束の置き場になる)
3. trust: `~/dev/yamato` (git root) が trust されていなければ、owner が `cd ~/dev/yamato && claude` で承認する。`ship create` が警告し、`up` は止まる
4. 最初の依頼を送ってから起こす: `./yamato send <name> pm "<依頼。完了条件つき>"` → `./yamato up <name> --for 3h`。依頼は board の task 1 件に収まる小さいものから (例: e2e-p1 の「観測したこと」の 1 つ)
5. merge: ひな形の既定は `merge: reviewer` (D-010)。reviewer が承認して merge の判断を閉じ、自分で `pr merge` を打つ (`roles/reviewer.md`。`profiles.merger` の allow で分類器を通す、D-026)。設計の根幹に触る PR だけ owner に上げる (`scope_change`)。pm は割り振りだけでレビューも merge もしない。owner と話して要件を詰めるのは planner (`yamato talk <name> planner`)。艦ごとに変えられる (owner が決める艦の流れは design-p1 §8.3)

admiral (docs/admiral.md): `yamato admiral` で attach する常駐の Claude のセッション (`_admiral/`、D-011)。`up` / `ships` / `status` / `extend` / `down` / `halt` と、赤い席・判断待ちを owner に伝えるだけ。艦の中身 (方針・順番・レビューの指摘) は中継しない。owner が captain と話すときは owner の端末で `yamato talk <name>`。

気をつけること:
- **worktree の衝突**: fleet の worktree (`agent-fleet/fleet-state/projects/yamato/worktrees/`) も艦の worktree (`~/yamato/<name>/worktrees/`) も、同じ `~/dev/yamato` の worktree になる。同じブランチは 1 か所でしか checkout できない。fleet の task と艦の task に同じファイルを触らせない (後から merge した方が衝突する)
- **本物の GitHub に PR が立つ**: `pr open` / `pr merge` は krml4913/yamato に本物の PR を作り merge する。`merge_requires` の `ci` はチェックの無い repo では通ったとみなす (README)。席は `GH_TOKEN` が空になるので、gh に保存した認証 (keyring) が要る (e2e-p1 の追記の時点では、このマシンは keyring で認証済み)
- **動いている yamato 自身**: 席の hook と `{{yamato}}` は、`ship create` を打った checkout の `yamato` (`util.YAMATO_BIN`) を呼ぶ。GitHub で merge しても `~/dev/yamato` を pull するまで反映されず、稼働中に pull すると席の挙動が途中で変わる。**pull は艦が止まっているときに**する (推測にもとづく注意。実機では見ていない)
- **稼働時間の上限**: `--for` で必ず上限を付ける。pm は Remote Control に繋がるので idle でも 1 時間で止まらない (verify-p0-c Q3)。止めるのは deadline と `down` / `halt`
- **E2E は艦の中で走らせない**: 本物の claude のセッションを作り、使用量を使う。E2E が要る task は owner の判断にする (推測にもとづく提案)
- **使用量**: 席の数とシフトの頻度に比例してサブスクの枠を使う (design.md §14)。`yamato ships` の今日の使用量を見る
