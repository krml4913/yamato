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
| `ts` | 数値 | epoch 秒 (usage.jsonl・inbox.jsonl と同じ) |
| `kind` | 文字列 | 出来事の種類 (§1.2) |
| `seat` | 文字列 / null | 出来事が関わる席 (下の表)。`owner` もありうる |
| `item` | 文字列 / null | 関わる board の項目 (`T-001`) や判断 (`D-001`) |
| `by` | 文字列 / null | した人 (送り手、`--by`)。分からなければ null |
| `summary` | 文字列 | 人が読む 1 行の要約。改行は空白に畳み、200 文字で切る |
| `data` | object (省略可) | kind ごとの詳細。日報や監視が機械的に使う値はここ |

キーは 7 つで固定。足したい値は `data` に入れる。

### 1.2 kind

yamato が書くのは次の kind。定数は `src/yamato/events.py`。

| kind | 書くところ | seat | item | by | data |
|---|---|---|---|---|---|
| `board_add` | `board add` | 担当 (assignee) | 項目 | `--by` | `fields` (指定した値) |
| `board_set` | `board set` | 変更後の担当 | 項目 | `--by` | `changes` (`{key: [前, 後]}`)、`note` (`--note`) |
| `board_archive` | `board archive` | 担当 | 項目 | ― | ― |
| `send` | `send` (inbox に記録した時点。起動前・上限後も書く) | 宛先 | ― | 送り手 | `n` (inbox の番号)、`chars` |
| `shift_start` | roster のシフト開始 (new / resume) | 席 | ― | ― | `shiftNo`、`how`、`sessionId` |
| `shift_end` | roster のシフト終了 | 席 | ― | ― | `shiftNo`、`reason` (`seat-stop` / `exited` / `down-force` / `grace-exceeded`)、`handoffWritten`、`note` (「引き継ぎなしで終了」など) |
| `force_stop` | `down --force`・猶予超えの強制停止 (このあと `shift_end` も出る) | 席 | ― | ― | `reason`、`shiftNo`、`sessionId` |
| `permission_denied` | PermissionRequest の deny hook (`source: dialog`)、PermissionDenied hook (`source: auto`、classifier の拒否) | 席 | ― | ― | `source`、`tool`、`reason` (auto のみ) |
| `notify_failed` | 通知 (`notify.via`、design-p1 §2.4) が 1 方式失敗したとき。方式ごとに 1 行。OS が違うための「送らない」は書かない。定数は `notify.NOTIFY_FAILED` | ― | ― | ― | `via`、`level`、`reason` (webhook の URL は入れない) |
| `decision_open` / `decision_close` | P1-2 の `decide open/close` が書く (口だけ用意) | decider を想定 | 判断の id | 開いた / 閉じた席 | P1-2 で決める |

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
