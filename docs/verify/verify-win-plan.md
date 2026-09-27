# yamato Windows 検証 W0: Windows + Git Bash の実機検証 — 手順書

- 対象: owner の Windows 機。ネイティブの Claude Code (`claude.exe`) と Git for Windows の Git Bash、Windows ネイティブの Python (`python.exe` / `py.exe`)
- 背景: yamato を Windows (Git Bash) で動かすための調査で、docs からは決められないことが残った (特に「hook やシェルから切り離した子プロセスが、席の `claude stop` のあとも生きるか」と「`claude agents --json` の `pid` が Windows の pid か」)。この検証の結果で、実装の task (W1〜W5) の作り方が決まる (最後の「結果の使い道」)
- yamato は**使わない** (まだ Windows で起動しない。`fcntl`)。Claude Code と Python だけで確かめる。検証の道具は `tools/verify_win/` (標準ライブラリだけ。yamato 本体は import しない)
- 打つのは owner。1 項目ずつ「打つもの → 見るもの → 判定」。打つものは Git Bash にそのまま貼り付ければ動く

## 結果の返し方

- 道具のコマンドは、見たものを **`~/yamato-verify-win/results.txt` 1 つに追記**していく。最後にこのファイルを leader に渡せば足りる
- 目で見て分かったこと (Remote Control の画面など) は `vw note <項目の番号> "<見たこと>"` で同じファイルに書く
- 下の「まとめ」の判定欄を埋めて返してもよい (results.txt と両方あればなおよい)
- 途中で止まった・分からないものは、そのまま「未確認」でよい。無理に全部やらなくてよい

## まとめ (判定欄)

判定は ✅ (動く) / 🟡 (条件つき) / ❌ (動かない) / ❓ (未確認)。自動の判定が出る項目は results.txt の「判定(自動)」を写せばよい。

| # | 問い | 判定 | 一言 |
|---|---|---|---|
| 1 | 環境: Claude Code 2.1.234 以上か、`claude.exe` の場所、python の名前、Git Bash の版、文字コード | | |
| 2 | `claude agents --json` の `pid` は Windows の pid か。`stop` / フル id の `--resume <id> --bg` (同じ id で再開するか) / `rm` | | |
| 3 | hook: shell form が Git Bash で走るか、exec form (`python.exe` + `args`) で走るか、stdin / stdout の日本語、profile の echo が混ざるか | | |
| 4 | Stop hook の `async` + `asyncRewake` で idle の席を起こせるか | | |
| 5 | **hook / 席の Bash から切り離した子が `claude stop` のあとも生きるか** (最重要) | | |
| 6 | `os.kill(pid, 0)` の実際の結果、`OpenProcess` + `GetExitCodeProcess` で生死が取れるか | | |
| 7 | `claude -p` を `CTRL_BREAK_EVENT` / `TerminateProcess` で止めたとき、何が残るか | | |
| 8 | SendMessage (`crossSessionInbound: "accept"`) が席に届くか、Remote Control で席に入れるか | | |
| 9 | permission: `//c/...` 形の絶対パスの deny、`Bash(...)` の deny が効くか。席が PowerShell ツールを使うか、そのとき deny はどうなるか | | |
| 10 | `msvcrt.locking` のファイルロックが 2 プロセスの間で効くか | | |

## 前提と、触るもの・触らないもの

- 使う場所は **`~/yamato-verify-win/` だけ** (Git Bash の `~` = `C:\Users\<you>`)。席の作業ディレクトリ・設定・ログ・子プロセスの印はすべてこの下に作る
- **既存のセッションと `~/.claude/settings.json` には触らない**。席は全部 `--setting-sources project,local --settings ~/yamato-verify-win/settings/<名前>.json` で起こすので、ユーザー設定 (hooks・plugin・`remoteControlAtStartup` など) は席に入らない
- 例外として、Claude Code 自身が次を残す (片付けで触れる): 作業ディレクトリ `~/yamato-verify-win/cwd` の trust (`~/.claude.json`)、席の transcript (`~/.claude/projects/<cwd の名前>/`)
- 席の名前は全部 `vw.` で始まる (`vw.life`・`vw.hooks`・`vw.prof`・`vw.detach`・`vw.recv`・`vw.send`・`vw.perm`)。片付けはこの名前だけを対象にする
- モデルは haiku。auto モードが要る項目 9 だけ sonnet。全部で席 7 つと `claude -p` 2〜3 回。時間は 40〜60 分ほど
- 席の中身は検証用の決まった短い指示だけ (道具の中に書いてある)。席に Bash と PowerShell を許すのは `vw.hooks` と `vw.detach` だけで、どちらも `~/yamato-verify-win/cwd` で決まったコマンドを打つだけ

## 0. 準備

### 0-1. python の名前を確かめる

```bash
type -a python py python3
python --version; py --version; python3 --version
```
- 見るもの: 本物の Python 3.11 以上がどの名前で呼べるか。`python3` が `.../WindowsApps/python3` を指していて Store の案内が出るなら、それは本物ではない (項目 1 でも記録する)
- 以下は `python` で書く。`python` が使えなければ `py` に読み替える (準備の 0-2 だけ。あとは `vw` が同じ Python を使う)

### 0-2. 道具を置く

yamato の checkout (この手順書が入った main。merge 前なら PR のブランチ) の中で:
```bash
cd <yamato の checkout>
git pull                        # merge 前なら: git fetch && git checkout yamato/task/verify-win-kit
python tools/verify_win/vw.py setup
source ~/yamato-verify-win/env.sh
```
- `setup` は `~/yamato-verify-win/` を作り、道具を `kit/` に写し、席の settings (`settings/*.json`)・的のファイル (`ship/*.yaml`)・`env.sh` を書く。何度打ってもよい (results.txt とログは消さない)
- `source .../env.sh` で `vw` というコマンドが使えるようになる (中身は `"$PY" ~/yamato-verify-win/kit/vw.py`)。**Git Bash の窓を開き直したら、もう一度 `source ~/yamato-verify-win/env.sh`**
- 道具を直した版を受け取ったら、もう一度 `python tools/verify_win/vw.py setup` (kit が新しくなる)

### 0-3. 席の作業ディレクトリを trust する

`claude --bg` は trust していないディレクトリでは起動しない。
```bash
cd ~/yamato-verify-win/cwd && claude
```
- trust のダイアログで承認したら、`/exit` で抜ける。何も頼まなくてよい
- ここだけはユーザー設定で起動する (一度だけ、trust を記録するため)

## 1. 環境

```bash
vw env
```
見るもの (results.txt の `[1]`):
- `claude --version` と `claude >= 2.1.234` (SendMessage は Windows では 2.1.234 から)
- `claude の場所` が `...\.local\bin\claude.exe` か (npm 版の `claude.cmd` だと yamato の起動の仕方が変わる)
- `python ->` / `py ->` / `python3 ->` がそれぞれ何を指すか (`WindowsApps の stub の疑い` が付くか)
- `bash --version` (Git Bash の版)、`CLAUDE_CODE_GIT_BASH_PATH` / `CLAUDE_CODE_USE_POWERSHELL_TOOL`、`git config core.autocrlf`
- `encodings`: `preferred` (日本語の Windows なら `cp932` のはず)、`stdio_before_reconfigure`、`utf8_mode`
- Git Bash の profile に `echo` の行があるか (中身は読まず、行数だけ)

判定: 版が 2.1.234 以上で、本物の Python が 1 つ以上あれば ✅。

## 2. bg の席の pid・stop・resume・rm

```bash
vw up life
vw wait life
vw agents
```
- 見るもの: `vw.life` の `pid` と、その下の 3 行
  - `pid_alive (OpenProcess)`: `alive: true` で、`image` が `...\claude.exe` なら、**`pid` は Windows の pid**
  - `tasklist`: 同じ pid が `claude.exe` として出るか
  - `ps -W (該当行)`: Git Bash の `ps -W` の見出しと該当行。`WINPID` の列に同じ番号があれば Windows の pid、`PID` の列にだけあれば MSYS の pid

```bash
vw stop life
vw resume life "再開の確認です。『RESUMED-VW』とだけ返せ。"
vw wait life
vw tx life
```
- `stop` は `claude stop <短い id>` を打ち、`claude agents` から pid が消えるまでの秒数と、元の pid のプロセスが消えたかを書く
- `resume` は **フルの sessionId** で `claude --resume <id> --bg -- "<msg>"` を打つ (pid が消えてから)。`判定(自動)` が「同じ id で再開した」なら ✅。「コピーになった」なら `新しくできた session` を見る
- `tx life` で、同じ transcript に `VW-LIFE` と `RESUMED-VW` の両方が載っていれば、会話も続いている

```bash
vw rm life
```
- `rm のあと一覧に残るか: false` なら ✅

判定: pid が Windows の pid で、stop・同じ id の resume・rm がどれも通れば ✅。

## 3. hook: shell form / exec form / 文字コード / profile の echo

```bash
vw up hooks
vw wait hooks
vw tx hooks
vw hooklog hooks
```
席 `vw.hooks` の settings (`settings/hooks.json`) には次の hook が入っている:
- SessionStart に 4 本。どれも合言葉 (`VWTOK-<種類>-<漢字>`) を additionalContext で注入する
  - `SHELL`: **shell form** (`command` の文字列だけ。Git Bash で走るはず)。UTF-8 のバイトで出す
  - `EXEC8`: **exec form** (`command` = python.exe のフルパス、`args` = [hook.py, ...])。UTF-8 のバイトで出す
  - `EXECDEF`: exec form。Python の既定の stdout のまま出す (今の yamato と同じ。cp932 になるかもしれない)
  - `EXECASC`: exec form。`\uXXXX` に逃がした JSON (どの文字コードでも壊れない)
- UserPromptSubmit と PreToolUse に exec form と shell form の記録用の hook (stdin を UTF-8 / cp932 / 既定の文字コードで読めるかを記録する)
- Stop に `async` + `asyncRewake` の hook (項目 4)

席には「合言葉を全部写せ」「`echo 日本語のツール入力テスト` を Bash で打て」「ファイル一覧を出せ (ツールは任せる)」と頼んである。

見るもの:
- `vw tx hooks` の `合言葉 VWTOK-...` の 4 行。`一致` なら、その hook が走り、日本語のまま席に届いた。`化けている` なら `見えた形` を見る。行が無ければ、その hook の出力は席に届いていない (hook が走らなかったか、JSON として読めなかった)
- `vw hooklog hooks`:
  - `hooks-ss-shell` などの tag ごとに記録があれば、その hook は走った (shell form が走ったか・exec form が走ったか)
  - `stdin utf8 で読める` と `日本語入りを既定の文字コードで読める`: Claude Code が hook に渡す stdin の文字コード。前が全部読めて後ろが読めなければ「stdin は UTF-8、Python の既定 (cp932) では読めない」
  - `hooks-ups-shell` の `meta`: shell form の hook を走らせた bash の `$-` (フラグ)・login shell か・`BASH_ENV`
  - `hook の中の job / PATH`: hook のプロセスが Job Object に入っているか (`in_job`、`limits` に `KILL_ON_JOB_CLOSE` / `BREAKAWAY_OK` があるか)。PATH に `sh`・`sleep`・`nohup` があるか
- `vw tx hooks` の `ツールの呼び出し`: 「ファイル一覧」を Bash と PowerShell のどちらで出したか (項目 9 でも見る)

続けて profile の echo が混ざるかを見る (owner の `~/.bashrc` には触らず、`BASH_ENV` で echo する小さなファイルを読ませる):
```bash
vw up prof
vw wait prof
vw tx prof
vw hooklog prof
```
- `vw.prof` の settings は `env.BASH_ENV` に `kit/profile_echo.sh` (`echo "PROFILE-ECHO 混入テスト"`) を入れ、SessionStart に shell form (`PROF`) と exec form (`PROFX`) の注入を 1 本ずつ入れてある
- `BASH_ENV ... を読んだ回数` が 1 以上なら、shell form の hook の bash は profile に当たるものを読む。そのとき `合言葉 VWTOK-PROF-月` が `一致` しなければ (または席が `PROFILE-ECHO` と答えれば)、**echo が hook の JSON に混ざって壊した**。exec form の `PROFX` はシェルを通らないので、こちらは届くはず
- 読んだ回数が 0 なら、hook の bash は非対話の profile (`BASH_ENV`) を読まない。owner の `~/.bashrc` の echo が混ざるかは、項目 1 の「echo の行」と `hooks-ss-shell` の結果で判断する

判定: exec form が走り、4 つの合言葉のうち少なくとも `EXEC8` と `EXECASC` が一致すれば、yamato の hook は exec form + UTF-8 で書ける (✅)。shell form の結果と `EXECDEF` の結果は、どこまで直す必要があるかの材料。

## 4. Stop hook の `async` + `asyncRewake` で idle の席を起こす

項目 3 の `vw.hooks` をそのまま使う (idle のままにしておく)。
```bash
vw wake "おはよう、日本語の合図"
```
- `vw.hooks` の Stop hook は、`~/yamato-verify-win/wake.txt` が伸びるまで待ち、伸びたら新しい行を stderr に出して exit 2 する (yamato の inbox の watcher と同じ作り)
- `vw wake` は wake.txt に 1 行足し、席に新しいターンが来るのを 90 秒まで待つ
- 見るもの: `判定(自動)` が「起きた」、`日本語がそのまま届いたか: true`
- 起きなかったら `vw hooklog hooks` の `hooks-stop-rewake rewake:` の行を見る (`armed` があれば watcher は動いていた。`fired` があれば hook は exit 2 したが、席が起きなかった)

判定: 起きて日本語が届けば ✅。

## 5. 切り離した子プロセスが `claude stop` のあとも生きるか (最重要)

yamato は、席の中から「少し待ってから自分を止める」処理や見張りを、切り離した子プロセスで起こす。Windows で Claude Code が hook や席のシェルを Job Object に入れて、席を止めるときに木ごと殺すなら、この作りは使えない。

```bash
vw up detach
vw wait detach
vw detach-check
```
- `vw.detach` は 2 つの経路で「90 秒間 2 秒おきに印を書き、最後に `.done` を書く」子 (`kit/child.py sleep`) を起こす
  - **hook から**: SessionStart の hook (exec form) が Python の `subprocess.Popen` で 4 通りの `creationflags` で起こす。さらに shell form の hook が Git Bash の `&` で 1 つ起こす
  - **席の Bash ツールから**: 席に `python.exe kit/hook.py spawn tool 90` を打たせ、同じ 4 通りで起こす (yamato の `seat-stop` は席が Bash で打つので、こちらが本番の経路)
- 4 通り: `A-plain` (フラグなし)、`B-detached` (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`)、`C-breakaway` (B + `CREATE_BREAKAWAY_FROM_JOB`)、`D-nowindow` (`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`)。hook の shell form の `&` が `hook-E-bashbg`
- 最初の `detach-check` で、`hook-*` と `tool-*` がそれぞれ `判定: stop 前` で並んでいることを確かめる (起動から 90 秒以内に次へ進む)。`spawn-hook.json` / `spawn-tool.json` の `errors` に何か出ていたら、そのフラグでは起こせなかった (`C-breakaway` は Job が breakaway を許さないと `OSError` になる)

**起こしてから 90 秒以内に** stop して、子が終わるのを待つ:
```bash
vw stop detach
vw detach-check --wait
```
- `--wait` は子が全部終わる (か印が止まる) まで最大 3 分待ってから書く
- 見るもの: 子ごとの `判定`
  - `生き残った`: stop のあとも印を書き続け、`.done` まで書いた
  - `stop で死んだ`: stop のころに印が止まり、プロセスも無い
  - `job`: その子が Job Object の中か (`in_job`)、Job の制限 (`KILL_ON_JOB_CLOSE` があれば、Job が閉じるときに中のプロセスは全部殺される)
- あわせて `spawn-*.json (起こした側)` の `job` が、hook のプロセス・席の Bash のプロセスの Job の状態

判定: `tool-B-detached` か `tool-C-breakaway` のどちらかが生き残れば ✅ (yamato の遅延 stop はその作りでよい)。どれも死ぬなら ❌ (遅延 stop の作り方を変える必要がある)。hook の経路と Bash の経路で違えば、それも書く。

## 6. `os.kill(pid, 0)` と `OpenProcess` + `GetExitCodeProcess`

席は使わない。
```bash
vw pid
```
- 4 つの pid で試す: 生きている子 (新しいプロセスグループ)、生きている子 (同じグループ)、終わった子 (Popen が handle をまだ持っている)、存在しない pid
- `os.kill(pid, 0)` は別の小さな Python (新しいプロセスグループ) の中で呼ぶ。Windows では 0 が `CTRL_C_EVENT` と同じ値なので、コンソールに Ctrl+C が飛ぶかもしれない。`vw` 自身は Ctrl+C を無視して続ける
- 見るもの: 行ごとの `os.kill(pid,0)` の `result` (`returned None` / `OSError: ...` / `KeyboardInterrupt` など)、`OpenProcess (前)` と `(後)` の `alive`。生きている子が `os.kill` のあとに死んでいれば、`os.kill(pid, 0)` は**相手を殺す**
- 途中で Git Bash の窓に `^C` が出たり、`vw` が止まったりしたら、そのことを `vw note 6 "..."` で書く

判定: `OpenProcess` の `alive` が 4 つとも正しければ (生きている 2 つが true、残り 2 つが false)、yamato の生死の判定はこれで作れる (✅)。`os.kill(pid, 0)` の結果は、今の yamato がどう壊れるかの記録。

## 7. `claude -p` を止めたとき

```bash
vw pterm break
vw pterm terminate
vw pterm taskkill
```
- それぞれ `claude -p` (haiku、`--output-format stream-json --verbose`) を `~/yamato-verify-win/cwd` で起こし、席に 120 秒かかる子 (`kit/child.py sleep`) を Bash で打たせ、その子が起動した 3 秒後に止める
  - `break`: `CTRL_BREAK_EVENT` (`claude -p` は新しいプロセスグループで起こしてある)
  - `terminate`: `Popen.terminate()` = `TerminateProcess`
  - `taskkill`: `taskkill /PID <pid> /T` (`/F` なし。穏当な止め方が効くか)
- 1 回 1〜2 分。止まらなければ 20 秒で強制終了する
- 見るもの:
  - `終了`: 終了コードと、止めてから終わるまでの秒数
  - `Bash の子 (sleep) は`: 席が起こした子が生きて残ったか (残っていたら道具が止める)
  - `stdout (stream-json)`: `result` の行が出たか
  - `SessionEnd hook`: 走ったか
  - transcript: 行数と `VWP-BEFORE` (止める前の発言) とツールの呼び出しが残ったか

判定: どの止め方で何が残るかを写すだけでよい (良し悪しは W3 で決める)。

## 8. SendMessage と Remote Control

```bash
vw up recv
vw wait recv
vw up send
vw wait send
vw tx send
vw tx recv
```
- `vw.recv` は `crossSessionInbound: "accept"` の settings で、`--remote-control` を付けて起こす。`vw.send` に「ListAgents で `vw.recv` を探して SendMessage で『PING-VW 日本語の便り』を送れ」と頼んである
- 見るもの: `tx send` の `ツールの呼び出し` に `SendMessage` があり、最後の文に `success: true` があるか。`tx recv` に `印 PING-VW` と `印 ACK` があるか (届いて返事をした)

Remote Control は目で見る:
- ブラウザの claude.ai/code (かスマホの Claude アプリ) のセッションの一覧に `vw.recv` が出るか。開いて「RC から: 日本語で一言返して」と送り、返事が来るか
- 見たことを書く: `vw note 8 "RC: 一覧に出た / 送れた / 返事が来た"`

判定: 届いて ACK が返れば SendMessage は ✅。RC は見たとおり。

## 9. permission: 絶対パスの deny、`Bash(...)` の deny、PowerShell ツール

```bash
vw up perm
vw wait perm
vw perm-check
```
- `vw.perm` は sonnet、`--permission-mode auto`、`--add-dir ~/yamato-verify-win/ship`。settings の deny (`settings/perm.json`):
  - `Edit(//c/Users/<you>/yamato-verify-win/ship/team.yaml)` と `Write(...)` — docs の書き方 (`C:\` を `/c/` に直して、先頭にもう 1 つ `/`)
  - `team2.yaml` は `/C:/Users/...` (Python の `as_posix()` の形)、`team3.yaml` は `/C:\Users\...` (今の yamato が `/{{ship}}` から作る形)
  - `Bash(echo DENYME*)`、`PowerShell(Write-Output PSDENY*)`
- 席には 9 つの手順を頼んである: team / team2 / team3 / other (deny なし) を Edit で書き換える、Bash で `echo DENYME-5`、PowerShell で `Write-Output DENYME-6` (Bash の deny が PowerShell に効くか)、`Write-Output PSDENY-7`、PowerShell の `Add-Content` で team.yaml に書く (Edit の deny が PowerShell の書き込みを止めるか)、日時を出す (ツールは任せる)
- 見るもの:
  - `team.yaml [//c/...]` などの 4 行: `書き換えられていない` なら deny が効いた。`other.yaml` は書き換えられるのが正しい (対照)
  - `edited-8` が team.yaml に入っていれば、PowerShell の書き込みは Edit の deny をすり抜けた
  - `PreToolUse で見えたツール (順)`: 各手順で Bash と PowerShell のどちらが使われたか。`PowerShell` が一度も出なければ、この席には PowerShell ツールが無い (項目 1 の `CLAUDE_CODE_USE_POWERSHELL_TOOL` も見る)
  - `最後の assistant の文`: 席がまとめた表 (手順ごとの通った・拒否された)

判定: `//c/...` の deny と `Bash(...)` の deny が効けば ✅。どの形が効かなかったか、PowerShell で何が通ったかを書く。

## 10. `msvcrt.locking` のファイルロック

席は使わない。15 秒ほどかかる。
```bash
vw lock
```
- 空のファイルの 1 バイト目を親が `LK_NBLCK` で取ったまま、別のプロセスから取りにいく
- 見るもの (期待): A (別プロセスの `LK_NBLCK`) は取れない、B (同じプロセスの別の handle) は取れない、C (別プロセスの `LK_LOCK`) は約 10 秒で諦める、D (親が 3 秒後に離す) は約 3 秒で取れる、E (離したあと) は取れる

判定: A と D と E が期待どおりなら ✅ (yamato の lock は `msvcrt.locking` で作れる)。

## 片付け

```bash
vw cleanup
cp ~/yamato-verify-win/results.txt ~/yamato-verify-win-results.txt
```
- `vw cleanup` は `vw.` で始まる席を全部 `claude stop` + `claude rm` し、残っている子プロセス (項目 5・7 の子、項目 4 の待ち受け) を止め、`vw.*` の席が 0 件になったことを書く
- **フォルダを消す前に results.txt をホームに写す** (これを leader に渡す)

```bash
claude agents --json --all | grep '"vw\.'     # 何も出なければよい
rm -rf ~/yamato-verify-win
```
- 任意: 席の transcript も消すなら、まず `ls -d ~/.claude/projects/*yamato-verify-win*` で対象を確かめてから `rm -rf` する。`~/.claude.json` に残る `cwd` の trust は、フォルダを消せば使われない (気になれば `claude` の `/config` などから消す。触らなくても害はない)
- `vw` のコマンドは、窓を閉じれば消える (`env.sh` を読んだシェルの中だけ)

## 困ったとき

- `vw: command not found` → `source ~/yamato-verify-win/env.sh` (窓ごとに要る)
- `vw up` の `rc` が 0 でなく `Workspace not trusted` → 0-3 をやり直す
- `vw wait` が `waiting (...)` と出る → 席が確認待ちで止まっている。`claude attach <id>` で画面を見て、何を聞かれているかを `vw note` に書き、attach を抜けて次へ進む
- `vw up` が「もう動いています」と言う → やり直すなら `vw stop <名前>` と `vw rm <名前>` のあとでもう一度
- 同じ項目をやり直したとき、results.txt には前の結果も残る (時刻で見分けられる)。そのままでよい
- Git Bash で `/` から始まる文字列を引数にすると `C:/Program Files/Git/...` に書き換わる (MSYS のパス変換)。`vw note` などの本文は `/` で始めない

## 結果の使い道

| 項目 | 結果で決まること | task |
|---|---|---|
| 1 | README の Windows の節 (どの python を使うか、claude.exe か .cmd か)、`.gitattributes` の要否 | W1 |
| 3 | hook を exec form (`python.exe` + `args`) にするか、標準入出力の UTF-8 化が要るか | W1 |
| 10 | lock を `msvcrt.locking` で作れるか (`LK_LOCK` の 10 秒で諦める振る舞いを前提に書く) | W2 |
| 2、5、6、7 | 生死の判定 (`pid_alive`)、切り離し (`spawn_detached` のフラグ)、遅延 stop の作り、headless の止め方 | W3 |
| 9 | permission 規則の `{{ship}}` の書き方 (`//c/...`)、PowerShell の deny を足すか。**deny が効くと確かめるまで、Windows で無人の席を出さない** | W4 |
| 4、8 | 今の send (SendMessage と Stop hook の watcher) がそのまま使えるか | W3・W4 |
