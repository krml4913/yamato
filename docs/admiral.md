# admiral (窓口) の使い方

- 対象: admiral を務めるセッション。移行期間は fleet の leader が兼ねる (design-p1 §6.3、owner の決定 Q3)。fleet を引退させたら、この文書を owner の対話セッションに載せる
- 位置づけ: admiral は yamato の席ではない。下の CLI を打つ人 (またはセッション) のこと。owner がシェルで直接打ってもよい

## 約束 (コードでは縛らない)

- **艦の中身に踏み込まない**。board・判断・方針・役割の割り振りは captain と owner のもの。admiral が触るのは、艦の出撃と帰投と一望だけ
- **艦に送るのは、出撃と帰投に伴う定型のメッセージだけ**。「この方針で」「この順で」のような中身の指示は、owner が `yamato talk` で captain に直接言う。admiral が中継しない
- CLI は admiral からの `send` や `board` の操作を拒否しない。守るのはこの文書の約束

## コマンド

艦は名前 (`$YAMATO_HOME/ships.json` → `$YAMATO_HOME/<name>`) かパスで指す。状態は毎回艦のフォルダから読む。

| したいこと | コマンド |
|---|---|
| 艦を作る | `yamato ship create <name> --workspace <repo> [--template dev]`。trust は main repo (git root) で確かめる。通っていなければ owner に `cd <repo> && claude` で承認してもらう |
| 出撃 | `yamato up <name> [--for 3h] [--seats impl,review]`。既定は captain だけが起きる (他の席は captain が send で起こす)。`--seats` で一緒に起こす |
| 全艦を一望 | `yamato ships`。1 艦 1 行: 稼働中か・残り時間・captain の最終・赤い席の数・owner の判断待ち・今日の使用量・最新の日報 |
| 1 艦を詳しく | `yamato status <name>`。赤い席は `!!!` の行で出る (権限の確認待ち、API エラー、生きているのに `watch.stale_after` (既定 20m) より長く動いていない、per_task の席が生きているのに active の担当が無い) |
| 時間を延ばす | `yamato extend <name> 1h`。deadline を書き換えるだけ。過ぎていれば今から数える |
| 帰投 | `yamato down <name>` (席は引き継ぎを書いて止まる。猶予を過ぎたら強制停止) |
| 緊急停止 | `yamato halt <name>`。猶予なしで全席を強制停止し、日報の安全網 (事実だけの日報と通知) を通す |
| owner を席につなぐ | `yamato talk <name> [<seat>]`。既定は team.yaml の `talk_default` (省略時 captain)。止まっている席は send と同じ規則で起こしてから `claude attach`。**端末を占有する**ので、owner の端末で打ってもらう (admiral のセッションの中では打たない) |

## 流れの例

1. owner から「dev を 3 時間出して」→ `yamato up dev --for 3h`
2. 定期的に / 聞かれたら `yamato ships`。赤や判断待ちがあれば owner に伝える (中身の判断はしない)
3. owner が captain と話したい → `yamato talk dev` を owner の端末で打つよう案内する
4. 「もう少し」→ `yamato extend dev 1h`。終わり → `yamato down dev`。暴走・課金の心配 → `yamato halt dev`
