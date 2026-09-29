# admiral (窓口) の使い方

- 対象: admiral を務める常駐の Claude のセッション (owner の決定 D-011。design-p1 §6、design.md §11)
- 位置づけ: admiral は**どの艦にも属さない、常駐のセッション**。`yamato admiral` で zellij 経由で開く。記録は `~/yamato/_admiral/` (`$YAMATO_HOME/_admiral/`)。役割プロンプトは `roles/admiral.md` (`templates/admiral/`)

## 約束 (コードでは縛らない)

- **艦の中身に踏み込まない**。board・判断・方針・役割の割り振り・実装・レビュー・merge は各艦の captain と owner のもの。admiral が触るのは、艦の出撃と帰投と一望、構成の変更、判断の代筆だけ
- **艦に送るのは、出撃と帰投に伴う定型のメッセージだけ**。「この方針で」「この順で」のような中身の指示は、owner が `yamato talk` で captain に直接言う。admiral が中継しない
- CLI は admiral からの `send` や `board` の操作を拒否しない。守るのはこの文書と `roles/admiral.md` の約束 (mechanism-not-policy)

## 起こす・止める

| したいこと | コマンド |
|---|---|
| 開く (無ければ作って起こす) | `yamato admiral`。止まっていれば talk と同じ規則 (send → 起こす) で起こしてから、zellij の `yamato-view` セッションを開く (admiral のタブが先頭、登録済みの全艦のタブも並ぶ。zellij の中なら admiral のタブを足して移る)。`_admiral/` が無ければ `admiral` ひな形から初回に作る (登録はしない。`ships` には出ない) |
| 抜ける | **zellij の detach (Ctrl+O d)**。席は動き続ける。艦の席を覗いたあとに抜けるのと同じ。`/exit` は席を止めるので使わない |
| zellij を使わずに attach する | `yamato admiral --direct`。端末で `claude attach` を前面に出す。zellij の無い環境 (Windows のネイティブなど) の逃げ道。抜けるのは ← か Ctrl+Z |
| 引き継ぎを促して止める | `yamato admiral --stop`。引き継ぎ (handoff.md) を書いて `seat-stop` するよう admiral の inbox に伝える。これ自体はブロックしない |
| それでも止まらなければ強制停止 | `yamato admiral --stop --force`。最大 `ADMIRAL_STOP_WAIT` 秒 (モジュール定数) 待って、それでも生きていれば `down --force` / `halt` と同じ強制停止に落ちる |

**スマホからは Remote Control** (`roles.admiral.remote_control: true` / 起動時から `remoteControlAtStartup: true`) で話せる。owner の端末 (zellij のタブ) で開くのと同じセッションに、別経路でつながる。

**時間の上限は掛けない** (`time_limit: none`。design.md §0 B4 の例外、D-013)。`--for` を付けない `up` は deadline を書かず、watchdog も立てない。`up`/`down`/`extend`/`halt` は admiral 自身にも効く (`up _admiral --for 3h` なら一時的に上限も掛けられ、そのあと `--for` なしの `up` で戻れば古い deadline は消える。`admiral --stop --force` は `down --force` に落ちる) が、admiral 自身には使わない約束で、`--for` を明示しない限り deadline は書かれない。長く続いたら「入れ替え」(下) で新しいシフトに切り替える。

## 各艦へのコマンド

艦は名前 (`$YAMATO_HOME/ships.json` → `$YAMATO_HOME/<name>`) かパスで指す。状態は毎回艦のフォルダから読む (`ships.json` は名前 → パスだけ)。

| したいこと | コマンド |
|---|---|
| 艦を作る | `yamato ship create <name> --workspace <repo> [--template dev]`。trust は main repo (git root) で確かめる。通っていなければ owner に `cd <repo> && claude` で承認してもらう |
| 構成を変える | 艦フォルダの `team.yaml` / `roles/<役割>.md` / `charter.md` を Edit で直す。**変更は次の `yamato up` から効く** (今動いている席には効かない)。役割の `count` を変えると席の名前が変わる (`impl` ⇄ `impl-1, impl-2, ...`、design-drift E)。persistent の席は resume だと古いプロンプトのままなので、新しいプロンプトを効かせるには新しいシフトで起こす (`yamato rotate <ship> <seat>` で入れ替えの印を立ててから `up` / `send`) |
| 出撃 | `yamato up <name> [--for 3h] [--seats impl,review]`。既定は captain だけが起きる (他の席は captain が send で起こす)。`--seats` で一緒に起こす。艦がすでに稼働中で `--for` を付けなければ、deadline は縮めない (`max(now + time_limit, 今の deadline)`。D-015)。`--for` を明示すればその値で上書きする |
| 全艦を一望 | `yamato ships`。1 艦 1 行: 稼働中か・残り時間・captain の最終・赤い席の数・owner の判断待ち・今日の使用量・最新の日報。`_admiral/` は登録しないのでここには出ない |
| 1 艦を詳しく | `yamato status <name>`。赤い席は `!!!` の行で出る (権限の確認待ち、API エラー、生きているのに `watch.stale_after` (既定 20m) より長く動いていない、per_task の席が生きているのに active の担当が無い) |
| 出来事を流し見 | `yamato feed <name>` |
| board・判断を見る (触らない) | `yamato board list <name>` / `yamato decide list <name>` |
| 時間を延ばす | `yamato extend <name> 1h`。deadline を書き換えるだけ。過ぎていれば今から数える |
| 帰投 | `yamato down <name>` (席は引き継ぎを書いて止まる。猶予を過ぎたら強制停止) |
| 緊急停止 | `yamato halt <name>`。猶予なしで全席を強制停止し、日報の安全網 (事実だけの日報と通知) を通す |
| owner を席につなぐ | `yamato talk <name> [<seat>]`。既定は team.yaml の `talk_default` (省略時 captain)。止まっている席は send と同じ規則で起こしてから `claude attach`。**端末を占有する**ので、owner の端末で打ってもらう (admiral のセッションの中では打たない) |
| 入れ替えの印を立てる | `yamato rotate <ship> <seat>...`。止まっている persistent の席に「次のシフトは入れ替え」の印を立てる (design-drift D) |
| 判断の代筆 | owner の言葉を受けて `yamato decide close <ship> <判断の id> --choice "<決定>" --reason "<owner の言葉をそのまま>" --by owner`。設計の根幹に触る判断は owner にはっきり確かめてから閉じる |
| 表示 | `yamato view open [<艦名>...]` (zellij のタブで艦を並べて見る。あれば。艦名を省くと admiral のタブが先頭に付く) |

## 権限について (D-013)

- model は `opus`。話し相手になり、判断を代筆し、艦の構成も変えるので、判断の質を優先する
- `auto` モード。`~/yamato/**` (艦フォルダ全部・`ships.json`) は編集してよい (team 構成・roles・charter の変更をやらせる仕事のため)。ただし各艦の記録本体 (`.runtime/`・`roster.json`・`usage.jsonl`・inbox・`memory.md`/`knowledge.md` など、各艦の yamato のコマンドだけが書く場所) は deny で守り、admiral 自身も直接は書き換えない
- WebFetch / WebSearch は使ってよい (owner の判断。外を読む役割と権限のある役割を分ける B2 の分離は admiral には掛けない)
- `~/dev/yamato` (yamato 自身の repo) は**読むだけ** (Edit / Write を deny)。実装・レビュー・merge は艦の中の仕事なので、必要なら各艦の captain / impl に頼む。pull は Bash で打つが、**その checkout を使う艦が全部止まっているときだけ** (稼働中に pull すると席の挙動が途中で変わる。コードでは縛らず役割プロンプトの約束)

## 作り方 (D-013)

`_admiral/` は「席 1 つ・deadline なしの特別な艦」として、既存の seat / inject / rotate / talk / inbox の仕組みをそのまま使い回す。`ships.json` には登録せず (`register=False`)、名前が `_` 始まりなので通常の艦の名前としても拒む。`yamato admiral` の初回に `admiral` ひな形 (`templates/admiral/`) から作り、以後は既にある `team.yaml` / `roles/admiral.md` / `charter.md` を壊さない (owner の手直しを上書きしない)。

艦の席から admiral への `send` は当面作らない (中身に踏み込まない約束とぶつかりやすいので後回し)。owner と admiral 本人だけが使う。

## 入れ替え (rotate)

常駐なので、長く続いたシフトは入れ替える (`roles.admiral.rotate`。コンテキストの量・compaction・シフトの長さ・日付。design-p1 §5.3〜5.4 と同じ仕組み)。入れ替えの促しが来たら、今の会話の区切りで `yamato seat-stop ~/yamato/_admiral admiral --rotate` を実行して止まる。次のシフトはその場では起動せず、owner が次に `yamato admiral` で話しかけたときに、引き継ぎ (handoff.md) から新しいシフトとして起きる。

## 流れの例

1. owner から「dev を 3 時間出して」→ `yamato up dev --for 3h`
2. 定期的に / 聞かれたら `yamato ships`。赤や判断待ちがあれば owner に伝える (中身の判断はしない)
3. owner が captain と話したい → `yamato talk dev` を owner の端末で打つよう案内する
4. 「もう少し」→ `yamato extend dev 1h`。終わり → `yamato down dev`。暴走・課金の心配 → `yamato halt dev`
5. owner が dev の team.yaml を直したいと言う → admiral が `~/yamato/dev/team.yaml` を Edit で直し、「次の `up` から効く」と伝える
