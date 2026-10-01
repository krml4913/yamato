# yamato Windows W5: 実機の確認 — 手順書

- 対象: owner の Windows 機 (ネイティブの Claude Code + Git for Windows の Git Bash + Windows ネイティブの Python)。**艦の中では走らせない** (本物の claude を使う)
- 前提: W1〜W4 (T-029 / T-041 / T-042 / T-044) が main に入っていること。W0 の手順書は `verify-win-plan.md`、道具は `tools/verify_win/` (vw.py)
- 結果は下の「結果」の表に埋め、見たままの出力を貼る。途中で止まったものは「未確認」でいい
- 使うのは使い捨ての艦 1 つだけ (`~/yamato-verify-w5/`)。既存の艦には触らない
- 打つのは Git Bash。1 項目ずつ「打つもの → 見るもの → 判定」
- 出力・控えのファイルは `/tmp` でなく `V=~/yamato-verify-w5` の下に置く (`> "$V/ut.txt"` のように)。Git Bash の `/tmp` は Windows の python から見えない

## 0. テストを回す (完了条件: skip は POSIX 専用だけ)

```
cd <yamato の checkout>
git pull
time python -m unittest discover 2>&1 | tail -30
python -m unittest discover -v 2>&1 | grep -c "skipped"
python -m unittest discover -v 2>&1 | grep "skipped"
```

- 見るもの: `OK` か / 失敗・エラーのテスト名 / 所要時間 (macOS は 10〜15 秒) / skip の一覧
- 判定: 全部通る。skip は理由が「POSIX only」のものだけ。10 秒を大きく超える (目安 60 秒以上) なら遅いテストを `--durations 10` で控える (直すかは別に判断)
- 今 POSIX 専用で skip する既知のもの: `test_headless` の SIGTERM 転送、`test_view` の AttachProcessTest (SIGINT で止める)。代わりの確認は下の 3・6

## 1. 準備 (使い捨ての艦)

```
export YAMATO_NO_BANNER=1
Y="python <yamato の checkout>/yamato"
mkdir -p ~/yamato-verify-w5/ws && (cd ~/yamato-verify-w5/ws && git init -q -b main && git commit -q --allow-empty -m init)
$Y ship create w5 --workspace ~/yamato-verify-w5/ws --path ~/yamato-verify-w5/ship
# 以降の <ship> は ~/yamato-verify-w5/ship
```

- team.yaml の `time_limit` は短く (例 `--for 10m`) して `up` する。workspace は空の git repo (`~/yamato-verify-w5/ws`) で、`cd` して `claude` を 1 回起動し trust を承認しておく
- 見るもの: `up` が通り、`$Y status <ship>` に pm が出る (W1 の入口)

## 2. W3: 席の停止の経路

### 2a. seat-stop (遅延 stop の `sh -c` が走る)

1. `$Y up <ship> --for 20m` → pm の席が起きる
2. pm に「何もせず seat-stop して」と `$Y talk <ship>` か `$Y send <ship> pm "..."` で頼む (handoff.md を書いて `seat-stop` を呼ぶだけの指示)
3. 数十秒待って `$Y status <ship>` と `claude agents --json` を見る

- 見るもの: 席が stopped になる (`sleep N; claude stop ...; _shift-ended` の `sh -c` が Git Bash で走った証拠)。`seats/pm/` の roster に endReason が入る。`claude agents --json` の pid が消える
- 落ちたら: `events.jsonl` の `shift_end` の有無 (イベント名は `shift_end`。`shift_ended` で grep すると 0 件)、`sh` が PATH に無いエラーがないか

### 2b. 締切の強制停止 (watchdog)

1. `$Y up <ship> --for 2m`
2. **席を作業中にしておく**。仕事の無い persistent の pm は、起きると数十秒で自分で seat-stop するので、何も頼まないと watchdog が止める相手がいない。「handoff.md は書くな、seat-stop もするな」と添えて、締切をまたぐ長い作業 (または終わらない待ち) を `$Y send <ship> pm "..."` で頼む。席は resume 直後に先の依頼で seat-stop することがあるので、`status` で生存=yes を確かめてから送る (止まっていれば send で依頼つきで起きる)。なお Bash の単発の `sleep 60` は Claude Code に止められる (「Blocked: standalone sleep」)。席が blocked のまま締切を迎えても検証にはなる
3. 締切 + 猶予のあとまで待つ (`team.yaml` の grace を見る)

- 見るもの: watchdog (`_watchdog`) が席を強制停止し、`events.jsonl` に `force_stop` (reason `grace-exceeded`) と `shift_end` (reason `grace-exceeded`、`handoffWritten: false`) が記録される。`claude agents --json` に pid が残らない
- 判定: 猶予を過ぎて 1 分以内に止まる

### 2c. headless の `down --force`

1. headless の席 (researcher など、`how: headless` の席) に仕事を振る。research の席は dontAsk で、Bash は allow にあるものしか使えない (`sleep` のループは拒否され、席が 11 秒で終わる)。**WebSearch を 15 回順に打たせるような、Bash を使わない長めの作業**にする: `$Y send <ship> researcher-1 "WebSearch を 1 回ずつ順に 15 回打て: ..."`。`status` で生存=yes を確かめてから次へ
2. 動いている間に `$Y down <ship> --force`
3. 動いている間に別の Git Bash から `tasklist | grep -i claude` を見ておき、`down --force` の後にもう一度見る

- 見るもの: **`headless.terminate` は Windows で `CTRL_BREAK_EVENT` だけを送る。別のコンソールから届かない見込み** (T-042 reviewer)。次のどれになったかを控える
  - (a) CTRL_BREAK が届いて `claude -p` の子ごと止まる
  - (b) 届かず、grace のあと `taskkill /T /F` に落ちて止まる (`procs.terminate` の経路)
  - (c) どちらでも止まらない (**要対応**。pid・`tasklist` の出力を貼る)
- あわせて: 止まった後に `claude.exe` / `node.exe` の子が残っていないか (`tasklist` の前後の差)

### 2d. `yamato talk`

- `$Y talk <ship>` (止まっていれば起こしてから attach する) → attach の画面が出て、抜けられる
- 見るもの: attach が Windows のコンソールで動く。動かなければエラー文を貼る

## 3. `taskkill /T` の単体確認 (W0 で未確認)

```
python -c "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import subprocess,sys,time; subprocess.Popen([sys.executable,\"-c\",\"import time; time.sleep(300)\"]); time.sleep(300)']); print(p.pid); time.sleep(2); subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F']); time.sleep(1)"
tasklist | grep -i python
```

- 見るもの: 孫の python も消える (親だけ残る/孫だけ残る、を控える)。CTRL_BREAK が別コンソールから届くかは 2c で見る
- 補足 (任意): `python -c "from yamato import procs; ..."` で `procs.hard_kill` / `procs.soft_stop` を、新しいコンソール (`creationflags=CREATE_NEW_PROCESS_GROUP`) で起こした子に打つ。`soft_stop` の group 既定は False (T-054) なので、CTRL_BREAK を見るなら `procs.soft_stop(pid, group=True)` と書く

## 4. W4: deny (艦の team.yaml への Edit が止まる)

- 席を起こし (`$Y up <ship>`)、pm に頼む: 「艦フォルダの `team.yaml` の末尾に `# edited` を Edit で足せ。拒否されたら回避せず、拒否の文言をそのまま報告しろ」
- 見るもの: 拒否される (W0 [9] は `File is in a directory that is denied by your permission settings.`)。`team.yaml` が変わっていない (`git diff` / `tail`)
- 追加: 席に PowerShell で同じ編集を頼んだときどうなるか (W0 では PowerShell の `Add-Content` は「allowed working directories 外」で止まった。今回も止まるか、通ってしまうか)。**通ったら要報告**

## 5. 引用つきの ship に allow が当たるか (T-041 reviewer)

- ship のパスに空白が入る場所 (`~/yamato-verify-w5/ship dir/` のように作る。Windows のユーザー名に空白がある場合はその配下でもよい) で艦を作る
- pm に `$Y board list` 相当 (`board mine <ship> pm` など、settings の allow に載っているコマンド) を打たせる。ship の引数は `'C:/Users/John Doe/...'` のように引用つきで渡る
- 見るもの: **許可の確認 (ダイアログ) なしに通るか**。無人の席なので、止まったら席が「許可待ち」で固まる。通れば ✅、確認で止まる/拒否されるなら ❌ (settings に出ている allow の文字列と、席が実際に打ったコマンドを貼る)

## 6. Remote Control の見え方 (W0 で未確認)

- `up` した pm の席が Remote Control (claude.ai/code またはアプリ) に見えるか、そこから入って話せるか
- `talk` の attach 中と、`seat-stop` 後 (止まったあと) の見え方の違い

## 結果

実行: owner の Windows 機 (Windows 10 Pro 19045、Git Bash、Python 3.13、claude 2.1.286)。素の Claude Code (windows-yamato、Remote Control) に手順を打たせ、3・8・12 は owner が見た。出典は Issue #68 の次のコメント (5931240737 が手順、結果は 5931657318 / 5931709050 / 5932113328 / 5932200511 / 5932309583 / 5932346246 / 5932608360)。最終は T-053 (8b3217f)・T-054 (f0080e6) の上。

| # | 確認 | 判定 | 一言 |
|---|---|---|---|
| 0 | unittest 全通し / 所要時間 / skip の一覧 | 🟡 | f0080e6 で 750 件、FAIL 2・skip 6、82 秒 (macOS は 10〜15 秒)。FAIL 2 件は T-055 で別に直す。skip 6 件は POSIX 専用 3 件 + Windows では作れない名前 3 件。シェルが落ちる件は直った |
| 2a | seat-stop → 席が止まる | ✅ | stopped になり `claude agents` から消え、`shift_end (seat-stop)` が出る |
| 2b | 締切の強制停止 (watchdog) | ✅ | 猶予 1 分を過ぎて `force_stop (grace-exceeded)`。手順は変えた (席を作業中にしておく) |
| 2c | headless `down --force` | ✅ (a) | `down --force` は 1.2 秒で戻り、CTRL_BREAK で claude -p が exit 0 で止まった。ラッパー・watchdog の python も消え、taskkill には落ちていない |
| 2d | `yamato talk` | ✅ | attach の画面が出て、入力欄が空のとき ← で抜けられる。席は生きたまま |
| 3 | `taskkill /T` が孫まで止めるか | ✅ | 孫の python まで止まった |
| 4 | team.yaml への Edit の deny | ✅ | Edit は拒否、team.yaml は変わらず。PowerShell ツールは無い (D-051 A) |
| 5 | 引用つき ship に allow が当たるか | ✅ | 止まっていた pm が send で起き、許可の確認で止まらず返事が来た (ユーザー名に空白がある ship) |
| 6 | Remote Control の見え方 | ✅ | w5.pm が一覧に見え、入って話せた。seat-stop のあとは一覧から消えた |

### 0 unittest の経過 (🟡)

| 時点 | 結果 |
|---|---|
| 最初 (T-043 の merge 直後) | 733 件、failures=9 errors=534 skipped=3、12.7 秒 |
| 1550b40 (T-049) | FAIL 34・ERROR 139〜140。途中でシェルごと落ちる |
| 98366ab (T-050) | 746 件 FAIL 14・ERROR 7、シェルは落ちない。test_headless の CTRL_BREAK まわりが残る |
| b9e09ad (T-052) | `test_force_stop_all_stops_an_orphaned_p` でシェルごと落ちる |
| 8b3217f (T-053) | 749 件、FAIL 2・skip 6、82 秒 (3 回同じ。PYTHONUTF8=1 でも同じ) |
| f0080e6 (T-054) | 750 件、同じ FAIL 2 |

残る FAIL 2 件 (T-055 で別に直す):
- `test_headless.HeadlessTest.test_time_limit_sigterms_and_counts_usage_from_the_transcript`: `u["messages"]` が 0 (期待 1)。止める処理は効いていて、transcript から使用量を数える部分だけが 0。fake の transcript の場所か書き切りのずれと見られる (推測)。本物の claude の 2c では `4 messages` と数えられた
- `test_memory.ResearchTemplateTest.test_editor_applies_and_the_curator_is_there`: 期待値が `str(Path)` (`\` 区切り) のまま。プロンプトは引用つきの `C:/...` (`runtime.ship_arg`)

所要が 82 秒と長い (目安 60 秒以上)。`--durations 10` は控えていない。

### 手順で見つかった直し (手順書の本文に反映済み)
- grep の語は `shift_ended` でなく `shift_end` (2a)。強制停止は `force_stop` と `shift_end` の `reason: grace-exceeded` (2b)
- 控えのファイルは `/tmp` でなく `$V` (Windows の python から `/tmp` が見えない)
- 2b: 仕事の無い pm は自分で seat-stop するので、席を作業中にしておく。Bash の単発 `sleep` は止められる
- 2c: research の席は dontAsk で `sleep` のループが拒否される。WebSearch を使う作業にする
- 3 補足: `soft_stop` の group 既定は T-054 で False。CTRL_BREAK を見るなら `group=True`

### 見つかった点 (コードは直していない。別の task の材料)
- 2c: `down --force` でも `endReason`・log・events は `grace-exceeded` (「時間切れ」) と書く (`forceStop: down-force` は roster にある)。ログの文言は Windows でも「→ SIGTERM」のまま
- 2b: deadline watcher の「終業を指示」が 7 秒あけて 2 回出る (22:17:43 / 22:17:50)
- 2d: 抜け方 (入力欄が空のとき ←。`/exit` は席を止める) を talk の前に一言出すと親切
- 準備: workspace の trust は `~/.claude.json` に `C:/` 区切りで書かれる。T-051 で `is_trusted` が引けるようにした。research の艦は owner の許可で `~/.claude.json` に直接 trust を書いた (控えは `~/yamato-verify-w5/claude.json.bak`)
- 艦の既存の席 (`main-leader` など) と `claude.exe` が `tasklist` に混ざる。2c では自分の pid を控えて除く

見たままの出力は Issue #68 の上のコメントにある (この文書には写さない)。`~/yamato-verify-w5/` は残してある (消すかは owner)。
