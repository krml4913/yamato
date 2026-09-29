# yamato Windows W5: 実機の確認 — 手順書

- 対象: owner の Windows 機 (ネイティブの Claude Code + Git for Windows の Git Bash + Windows ネイティブの Python)。**艦の中では走らせない** (本物の claude を使う)
- 前提: W1〜W4 (T-029 / T-041 / T-042 / T-044) が main に入っていること。W0 の手順書は `verify-win-plan.md`、道具は `tools/verify_win/` (vw.py)
- 結果は下の「結果」の表に埋め、見たままの出力を貼る。途中で止まったものは「未確認」でいい
- 使うのは使い捨ての艦 1 つだけ (`~/yamato-verify-w5/`)。既存の艦には触らない
- 打つのは Git Bash。1 項目ずつ「打つもの → 見るもの → 判定」

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
- 落ちたら: `events.jsonl` の `shift_ended` の有無、`sh` が PATH に無いエラーがないか

### 2b. 締切の強制停止 (watchdog)

1. `$Y up <ship> --for 2m`。席が handoff を書かずに放っておくよう頼む (または何も頼まない)
2. 締切 + 猶予のあとまで待つ (`team.yaml` の grace を見る)

- 見るもの: watchdog (`_watchdog`) が席を強制停止し、`shift_ended --forced` が記録される。`claude agents --json` に pid が残らない
- 判定: 猶予を過ぎて 1 分以内に止まる

### 2c. headless の `down --force`

1. headless の席 (researcher など、`how: headless` の席) に `$Y send <ship> researcher "60 秒かかる調べものをして"` で仕事を振る
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
- 補足 (任意): `python -c "from yamato import procs; ..."` で `procs.hard_kill` / `procs.soft_stop` を、新しいコンソール (`creationflags=CREATE_NEW_PROCESS_GROUP`) で起こした子に打つ

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

| # | 確認 | 判定 (✅/🟡/❌/❓) | 一言 |
|---|---|---|---|
| 0 | unittest 全通し / 所要時間 / skip の一覧 | | |
| 2a | seat-stop → 席が止まる | | |
| 2b | 締切の強制停止 (watchdog) | | |
| 2c | headless `down --force` (a / b / c のどれか) | | |
| 2d | `yamato talk` | | |
| 3 | `taskkill /T` が孫まで止めるか | | |
| 4 | team.yaml への Edit の deny (Edit / PowerShell) | | |
| 5 | 引用つき ship に allow が当たるか | | |
| 6 | Remote Control の見え方 | | |

貼ったもの (見たままの出力) は、この表の下に項目ごとに足す。
