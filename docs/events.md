# events.jsonl と lastActive

- 作成: 2026-09-26 / driver (task-p1-events)
- 位置づけ: design-p1 §10 の 1 番 (§0.1 の `events.jsonl`、§5.1 の最後に動いた時刻) の実装の記録。日報 (design-p1 §2)・監視 (§5)・判断 (§1) はここに書いた形を前提に読み書きする

## 1. events.jsonl

艦フォルダ直下の `events.jsonl`。艦で起きた出来事を 1 行 1 件の JSON で追記する。消さない、書き換えない (切り詰めは P1 ではしない)。

### 1.1 行の形式

```json
{"ts": 1790000000.12, "kind": "board_set", "seat": "impl", "item": "T-001", "by": "impl", "summary": "state: open→active", "data": {"changes": {"state": ["open", "active"]}}}
```

| キー | 型 | 中身 |
|---|---|---|
| `ts` | 数値 | epoch 秒 (usage.jsonl・inbox.jsonl と同じ)。数値でない行は `read` が飛ばす |
| `kind` | 文字列 | 出来事の種類 (§1.2) |
| `seat` | 文字列 / null | 出来事が関わる席 (下の表)。`owner` もありうる |
| `item` | 文字列 / null | 関わる board の項目 (`T-001`) や判断 (`D-001`) |
| `by` | 文字列 / null | した人 (送り手、`--by`)。分からなければ null |
| `summary` | 文字列 | 人が読む 1 行の要約。改行は空白に畳み、200 文字で切る |
| `data` | object (省略可) | kind ごとの詳細。日報や監視が機械的に使う値はここ |

キーは 7 つで固定。足したい値は `data` に入れる。

### 1.2 kind

yamato が書くのは次の kind。定数は `src/yamato/events.py` (`notify_failed`・`report_*`・`worktree_*`・`pr_*`・`memory_*` は各モジュールに置く。表に書いた)。

| kind | 書くところ | seat | item | by | data |
|---|---|---|---|---|---|
| `board_add` | `board add` | 担当 (assignee) | 項目 | `--by` | `fields` (指定した値) |
| `board_set` | `board set` | 変更後の担当 | 項目 | `--by` | `changes` (`{key: [前, 後]}`)、`note` (`--note`) |
| `board_archive` | `board archive` | 担当 | 項目 | ― | ― |
| `board_note` | `board note` (本文への追記。frontmatter は変えない。design-p1 §7.2) | 担当 | 項目 | `--by` (無ければ呼び出した席) | `chars` |
| `send` | `send` (inbox に記録した時点。起動前・上限後も書く) | 宛先 | ― | 送り手 | `n` (inbox の番号)、`chars` |
| `shift_start` | roster のシフト開始 (new / resume / headless) | 席 | ― | ― | `shiftNo`、`how`、`sessionId` |
| `shift_end` | roster のシフト終了 | 席 | ― | ― | `shiftNo`、`reason` (`seat-stop` / `exited` / `down-force` / `grace-exceeded`。headless はほかに `max-duration` / `failed` / `wrapper-signal` (ラッパーが SIGTERM・SIGINT を受けて `-p` に転送した) / `wrapper-lost` (ラッパーが居ないのに `-p` が残っていたのを reconcile が止めた))、`handoffWritten`、`note` (「引き継ぎなしで終了」など) |
| `force_stop` | `down --force`・猶予超えの強制停止 (このあと `shift_end` も出る) | 席 | ― | ― | `reason`、`shiftNo`、`sessionId` |
| `shift_failed` | headless のシフトの異常 (design-p1 §4.2 の 4、§4.4。このあと `shift_end` も出る) | 席 | ― | ― | `shiftNo`、`sessionId`、`exitCode`、`failures` (理由の文)、`is_error`、`api_error_status`、`terminal_reason` |
| `permission_denied` | PermissionRequest の deny hook (`source: dialog`)、PermissionDenied hook (`source: auto`、classifier の拒否) | 席 | ― | ― | `source`、`tool`、`reason` (auto のみ) |
| `notify_failed` | 通知 (`notify.via`、design-p1 §2.4) が 1 方式失敗したとき。方式ごとに 1 行。OS が違うための「送らない」は書かない。定数は `notify.NOTIFY_FAILED` | ― | ― | ― | `via`、`level`、`reason` (webhook の URL は入れない) |
| `report_made` | `report daily` と日報の安全網 (design-p1 §2.2) が日報を作ったとき | ― | ― | ― | `date`、`factsOnly`、`reason` (facts-only のとき) |
| `report_sent` | `report send` と安全網が日報の要約を通知したとき。安全網は同じ日付の `report_sent` があれば送らない (通知の前に書く) | ― | ― | ― | `date`、`level` / `by: safety_net` |
| `worktree_add` | `worktree add` が worktree を**新しく作った**とき (既にあるものを返しただけのときは書かない)。定数は `worktree.WORKTREE_ADD` | 項目の担当 | 項目 | 呼び出し元 (`--by`、無ければ席、無ければ `owner`) | `path`、`branch` |
| `worktree_rm` | `worktree rm` が片付けたとき。定数は `worktree.WORKTREE_RM` | 項目の担当 | 項目 | 呼び出し元 | `path`、`branch`、`force` |
| `worktree_add_failed` / `worktree_rm_failed` | `worktree add` / `rm` が失敗したとき。`rm` が未 commit・未 push で断ったのもここ | 項目の担当 (項目が無ければ null) | 項目 | 呼び出し元 | `reason` (1 行、300 文字まで)、`rm` は `force` も |
| `pr_open` | `pr open` が PR を作ったとき (項目に PR が既にあって何もしなかったときは書かない)。定数は `pr.PR_OPEN` | 項目の担当 | 項目 | 呼び出し元 | `pr` (番号)、`url`、`branch`、`column` (review に動かしたとき)、`draft` (draft のとき) |
| `pr_open_failed` | `pr open` が失敗したとき (branch が無い、`gh` の失敗など) | 項目の担当 (項目が無ければ null) | 項目 | 呼び出し元 | `reason` |
| `pr_merge` | `pr merge` が merge したとき。既に merge されていて記録しただけのときも書く (`alreadyMerged: true`)。定数は `pr.PR_MERGE` | 項目の担当 | 項目 | 呼び出し元 | `pr`、`strategy`、`mergedBy` (= `by`。項目の `merged_by` と同じ)、`alreadyMerged` |
| `pr_merge_failed` | `pr merge` が merge しなかったとき。`git.merge_requires` を満たさず断ったときは `unmet` に理由が入る。gh の失敗もここ | 項目の担当 (項目が無ければ null) | 項目 | 呼び出し元 | `reason`、`pr`、`unmet` (断ったときだけ。理由ごとに 1 行、300 文字まで) |
| `pr_conflict` | `pr merge` のあと、他の開いている PR の衝突を見つけたとき。衝突の有無が分からなかった (UNKNOWN) ときは書かない。定数は `pr.PR_CONFLICT` | 衝突した項目の担当 | **衝突した項目** | merge した呼び出し元 | `pr` (衝突した PR)、`mergedItem`、`mergedPr` (merge した方)、`mergedBy`、`column` (`rebase` に動かしたとき)、`notified` (知らせた宛先。誰にも知らせなかったときは無い) |
| `memory_migrate` | P0 の `seats/<seat>/memory.md` を `roles/<role>/memory.md` に移したとき (`memory migrate`・注入の前) | 元の席 | ― | ― | `role`、`lines`、`kept` (元のファイルを残した場所) |
| `memory_curate` | `memory curate` の棚卸しのシフトが終わったとき (案を作った・時間切れ・異常)。定数は `memory.MEMORY_CURATE` | ― | ― | ― | `role`、`outcome` (`正常` / `時間切れ` / `異常`)、`sessionId`、案を作ったときは `plus`・`minus`・`candidates`・`over` (上限超え)、異常のときは `exitCode`・`failures` |
| `memory_apply` | `memory apply` が反映したとき (上限で断ったときは書かない)。定数は `memory.MEMORY_APPLY`。`memory status` の「前回の棚卸し」はこの行の時刻 | ― | ― | 呼び出し元 (`--by`、無ければ席、無ければ `owner`) | `role` (knowledge.md は null)、`lines`、`bytes`、`plus`、`minus`、`candidates` (処理した候補)、`archived` (外れた行)、`knowledge` (knowledge-inbox に回した数) |
| `decision_open` | `decide open` | decider | 判断の id | 開いた席 (呼び出し元) | `category`、`decider`、`blocks` (blocked にしたタスク)、`links`、`urgent`、`due`、`supersedes` |
| `decision_close` | `decide close` | decider | 判断の id | 閉じた席 (呼び出し元) | `decider`、`closed_by`、`on_behalf_of` (`--by`)、`by_decider` (false = decider 以外が閉じた。日報の「異常」の材料)、`choice`、`reason`、`was_blocking` (止めていたタスク)、`unblocked` (止まりが解けたタスク) |

- `worktree_*` / `pr_*` の `by` は呼び出し元 (`worktree.caller`: `--by`、無ければ `$CLAUDE_CODE_SESSION_ID` の席、無ければ `owner`)。記録だけで誰が打てるかは検査しない (design-p1 §0.3)。日報 (design-p1 §2) は `pr_merge` を「今日終わったもの」に、`pr_conflict` と `by_decider: false` の `decision_close` を「異常」に載せる
- deny ルールによる拒否は hook が拾わない (検証 B Q1) ので `permission_denied` には載らない
- **記録は道具** (project memory mechanism-not-policy): `emit` はどの kind も受け付け、誰が書くかを検査しない。上の表は yamato 自身が書くものの一覧で、制限ではない
- 書き込みは ship_lock (design §0 I1) を通す。書けなかったときは stderr に出すだけで、呼んだコマンドや hook は失敗させない

### 1.3 読む

```python
from yamato import events

events.read(shipdir, since=t0, until=t1)                  # since <= ts < until
events.read(shipdir, kinds=[events.FORCE_STOP, events.PERMISSION_DENIED])
events.read(shipdir, kinds=events.SEND, seat="pm")         # kind は 1 つでも可
events.read(shipdir, item="T-001")
```

ファイルの順 (= 書いた順) で返す。壊れた行 (書きかけ) は飛ばす。

## 2. lastActive (最後に動いた時刻)

- `roster.json` の `seats.<seat>.lastActive` (epoch 秒)。design-p1 §5.1 の `last_active`。roster の他のキー (`shiftStartedAt` など) に合わせて camelCase にした (design-p1 §0.1 の「P0 の名前に合わせて読み替える」)
- 更新するところ: シフト開始 (roster)、席の SessionStart / UserPromptSubmit / Stop hook。UserPromptSubmit は `yamato hook user-prompt-submit` として席の settings に足した (出力なし)。SendMessage で届いたメッセージで UserPromptSubmit が走るかは【要検証】。走らなくても、そのターンの終わりの Stop hook で更新される
- `yamato status` の `最終=` は `lastActive` と、transcript の更新時刻・シフトの開始と終了のうち最も新しいもの (hook を足す前に起動した席にも値が出るように)
