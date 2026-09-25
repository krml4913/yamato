# Spike 報告: zellij pane を Claude Code background session の「窓」にできるか

- 実施: 2026-09-25 / zellij 0.45.1 / Claude Code 2.1.282 / macOS
- 検証環境: 使い捨て zellij セッション `spike-attach`、テスト用 Claude セッション `spike-seatA`, `spike-seat` ×3(haiku、「OK と返して待て」系)。`fleet-main` と既存の Claude セッションには触れていない。
- **総合: いける。** attach 表示・キー入力・複数 pane・detach の安全性・シフト追従 wrapper まで、すべて headless で確認できた。設計に効く落とし穴は 3 つ(下の ⚠)。

## 前提: headless zellij の罠(検証手順の話。本番設計にも関係する)
- `zellij attach --create-background` だけで作ったセッションは、クライアントが繋がっていないと既定の 50x50 になる。さらに `new-tab` で作ったタブの pane は **1x1** のまま(`list-panes -a` で ROWS=1 COLS=1)で、描画されない。
- → Python の `pty.fork` で `zellij attach spike-attach` を 200x50 の pty で繋ぐ疑似クライアントを置いたところ、正常なサイズでレイアウトされた。実運用ではユーザーの端末がクライアントになるので問題にならない。ただし「誰も見ていない間に裏でタブ/pane を作る」運用なら、ユーザーが attach した時点で初めてリサイズされる、という前提で考えること。
- 自分の pane は `ZELLIJ_SESSION_NAME=fleet-main` を継承しているので、zellij コマンドは全部 `env -u ZELLIJ -u ZELLIJ_SESSION_NAME -u ZELLIJ_PANE_ID zellij --session spike-attach ...` で実行した(fleet-main の誤操作防止)。
- 新しい cwd では `claude --bg` が `Workspace not trusted` で起動を拒否した。zellij pane で `claude` を開き、`send-keys Down` → `Enter` で trust を承認して解決した(ついでにキー送信の検証にもなった)。**seat 用の cwd は事前に trust しておく必要がある。**

## Q1. `claude attach` は zellij pane で動くか / キー入力で返信できるか — ✅ 動く
- 証拠: `zellij action new-pane --tab-id 1 --name seatA-1 -- claude attach 6f7035c5` を実行し、`dump-screen -p terminal_3` で確認した。バナー、会話(`❯ Reply with just OK…` / `⏺ OK`)、入力欄、セッション名の罫線、フッターがすべて正常に描画されていた。
- 入力: `write-chars -p terminal_3 "Reply with just PONG1"` → `send-keys -p terminal_3 Enter` で、`⏺ PONG1` が返ってきた。返信できる。
- 注意: pane をリサイズした直後は、フッター行に古い描画の残骸が重なることがあった(実害はなく、次の再描画で直る)。

## Q2. 同じセッションを 2 pane から同時に attach できるか — ✅ できる(入力欄まで共有)
- 証拠: 同じ ID を 2 pane で attach した(`claude attach 6f7035c5` の pid 84488 と 85349)。両方に同じ画面が出た。
- pane1 に `draft-in-pane1` を打つと(Enter なし)、**pane2 の入力欄にも同じ文字が出た**。続けて pane2 から ` Reply with just PONG2` と Enter を送ると、送信内容は `draft-in-pane1 Reply with just PONG2` になり、両 pane に `⏺ PONG2` が出た。
- つまり入力バッファはセッション側に 1 つだけあり、全クライアントにミラーされる。tmux の同一ウィンドウ共有と同じ挙動。「覗き見用 pane」と「操作用 pane」を分けても、下書きは混ざる。

## Q3. pane / タブ / zellij セッションを閉じたら Claude セッションはどうなるか — ✅ detach されるだけ(セッションは生きている)
各操作のあとに `claude agents --json --all` を確認した。どれも `pid: 68027` のままで生存していた。
| 操作 | attach プロセス | Claude セッション |
|---|---|---|
| `close-pane -p terminal_4` | 消えた | 生存(pid 68027) |
| `close-tab-by-id 1` | 消えた | 生存 |
| `kill -9 <claude attach の pid>` | — | 生存 |
| `zellij kill-session spike-attach` | 消えた | 生存 |
- 補足: attach 中でも JSON の `state` は `blocked` → `done` と普通に変わっていた。attach しているかどうかは JSON に出ない(Q6)。

## Q4. attach 先のセッションが終わる / 止まると `claude attach` はどうなるか — ✅ きれいに exit する
- **ターンの完了(`state: done`, `status: idle`)**: attach はそのまま残る。background session はターンが終わってもプロセスを終了しないので、「終わった」とは「入力待ちに戻った」ことを指す。
- **`claude stop <id>`**: pane に `Resume this session with: claude --resume "spike-seatA"` / `Session 6f7035c5 has exited.` と出て、**exit code 0** で終了した(`sh -c 'claude attach …; echo EXIT=$?'` で確認)。ハングもエラーもない。
- **`claude rm <id>`(attach 中)**: stop と同じく `has exited.` と出て exit 0。
- **削除済み ID に attach**: `No job matching '6f7035c5'. Run 'claude agents' to list running sessions.` と出て **exit 1**。
- ⚠ **停止済み(stopped)セッションに attach すると、プロセスが再起動する。** stop 後の 6f7035c5 に attach したら、新しい pid 83757 で復活し、会話も復元された(docs の「next time you attach or reply, the session resumes」のとおり)。→ **wrapper が古いシフトの ID に attach すると、そのシフトを蘇らせてしまう。** 「生きている(pid != null)ものにだけ attach する」フィルタは必須。

## Q5. `--name` でセッションを引けるか / シフト追従 wrapper — ✅ 動く(実測済み)
- `claude agents --json --all` の各要素には `name`(`--name` で付けたもの)、`id`、`startedAt`、`pid` があり、停止済みのものは `pid: null`, `status: null` になる。
- ⚠ **`--name` は一意ではない。** 同じ `spike-seat` で 3 つのセッションを作れた(1 つは stopped、2 つは同時に生存)。重複しても番号は付かなかった。→ 「その席の現在のシフト」は wrapper 側で決める必要がある。ここでは「同名で pid があるもののうち `startedAt` が最新」とした。
- wrapper の案(テストに使ったもの):
```bash
#!/usr/bin/env bash
# seat-attach.sh <seat-name>: attach to the seat's current live bg session; follow shifts.
seat=$1; poll=${POLL:-3}
current() {  # newest live (pid != null) background session with this name
  claude agents --json --all | jq -r --arg n "$seat" \
    '[.[] | select(.kind=="background" and .name==$n and .pid!=null)] | sort_by(.startedAt) | last | .id // empty'
}
while :; do
  id=$(current)
  if [ -z "$id" ]; then printf '\r[seat %s] no live session, waiting...' "$seat"; sleep "$poll"; continue; fi
  echo "[seat $seat] attaching $id"
  # watcher: when a newer shift appears, kill the old attach so the loop moves on
  ( while sleep "$poll"; do
      new=$(current)
      [ -n "$new" ] && [ "$new" != "$id" ] && { pkill -P $$ -f "claude attach $id"; exit; }
    done ) & watcher=$!
  claude attach "$id"          # foreground: keeps the pane's tty as stdin
  kill "$watcher" 2>/dev/null; wait "$watcher" 2>/dev/null
  sleep 1
done
```
- テスト結果(pane `seat-wrap` で `seat-attach.sh spike-seat` を実行):
  1. セッションがまだ無い状態 → `[seat spike-seat] no live session, waiting...` を表示して待つ。
  2. シフト A(288aaa21)を `--bg --name spike-seat` で起動 → 自動で attach した。
  3. `claude stop 288aaa21` → `Session 288aaa21 has exited.` の後、waiting に戻った(停止済みの A には再 attach せず、A は蘇らなかった)。
  4. シフト B(e0937b87)を同じ名前で起動 → 自動で B に attach し、`⏺ SHIFT-B` が見えた。✅
  5. おまけ: B が生きている間にシフト C(9494a566)を起動 → watcher が B の attach を kill して C に乗り換えた(`pgrep` で見た attach 先が `9494a566` に変わった)。B は止まらず生きたまま残った。
  6. wrapper の pane に `write-chars` と Enter を送ると `⏺ VIA-WRAPPER` が返った。wrapper 越しでも入力は通る。
- 実装上の注意: 最初は `claude attach "$id" </dev/tty &` のように attach をバックグラウンドで動かす形で書いた。macOS ではこれが Bun の `EINVAL: invalid argument, kqueue` で即死した(開き直した /dev/tty を kqueue が扱えない)。**attach はフォアグラウンドで pane の tty を継承させ、監視はサブシェル側に置く**のが正解。
- 残る注意: 置き換えられた古いシフトを止めるのは wrapper の役目ではない(席の管理側でやる)。また、ユーザーが attach 内で ← や Ctrl+Z を押して抜けても、wrapper は数秒後に再 attach する。見る専用の窓としてはこれで良いが、pane からシェルに戻りたいなら wrapper を Ctrl+C で止める必要がある。

## Q6. zellij pane から attach していれば ~1h の supervisor 停止は防げるか — 🟡 docs 上は防げる、実測はしていない
- docs(agent view「The supervisor process」)の記述: 停止対象は「**Finished or waiting for your next message, and unattached for about an hour**」。停止されないのは「Working, paused on a permission prompt or other dialog, **or attached**: the process keeps running」。→ attach している限り止められない、と読める。
- zellij 側で必要なのは attach プロセスが生きていることだけ。Q3 のとおり、pane が残っていてクライアント(ユーザー端末)が繋がっていなくても attach プロセスは生きている(実際、疑似クライアントを落とした後も `claude attach` は残っていた)。つまり **zellij セッションを立てっぱなしにしておけば、そこで attach している席は常駐する** ことになる。
- ただし観測はできない。`claude agents --json` には attach 状態を示すフィールドが無い(docs のフィールド一覧も `state/status/pid/waitingFor/...` のみ)。非公開 I/F の `~/.claude/jobs/<id>/state.json` のキーにも attach/client 系のものは無かった。1 時間待つ実測もしていない(指示どおり)。
- 設計上の含意: 「zellij の窓 = keep-alive」になる。副作用として、zellij で全席を開いておくと全席が常駐し、メモリを食う(ターンは消費しないので使用量コストはかからない)。逆に zellij を閉じれば 1h ルールが戻る。docs によると、メモリが逼迫したときの停止ルールは attach 済みでも適用されうる、という含みは pin と同じで残る(明記はなし)。

## 推奨(zellij view layer の設計)
1. **1 タブ = 1 チーム、1 pane = 1 席、pane の中身は `seat-attach.sh <team>.<role>`** で良い。レイアウトは KDL の layout ファイルで宣言し(`pane command="seat-attach.sh" { args "team1.pm"; }`)、`zellij --session <team-view> --layout team.kdl` で開く。後から席を足すときは `zellij action new-pane --tab-id … -- seat-attach.sh …` を使う。
2. **席の「現在のシフト」の正本は席台帳(roster.json)に置き、wrapper はそれを読む形が本命。** `--name` が一意でない、同名のセッションが同時に生きうる、停止済みに attach すると蘇る、の 3 点から、「同名で最新の生存セッション」というヒューリスティックだけに頼るのは脆い。今回の wrapper の `current()` を「roster の sessionId を読み、`claude agents --json --all` で pid があることを確かめる」に差し替えれば、それ以外はそのまま使える。
3. **生存確認(pid != null)を通さずに attach しない。** 停止済みシフトの復活事故を防ぐため(Q4 ⚠)。
4. 窓を開けておくことは keep-alive を兼ねる(Q6)。「見たい席だけ pane を開く」か「全席開く(= 全席常駐)」かは、メモリと常駐方針で選ぶ。常駐させたくない席は、pane を開かないか、`seat-attach.sh` に attach 前の確認を足す。
5. 同じ席を複数 pane で開くと入力欄が共有される(Q2)。「見る専用の窓」を作りたい場合でも、zellij 側で入力をロックするしかない(pane を `switch-mode locked` にする、など)。
6. 裏で作ったタブ/pane は、クライアントが繋がるまでサイズ 1x1 のまま。ユーザーが attach すれば自動でリサイズされる。自動化テストでは pty 疑似クライアントが必要。

## 片付け(確認済み)
- `claude stop` + `claude rm` を 288aaa21 / e0937b87 / 9494a566 に実行した(6f7035c5 は Q4 の途中で rm 済み)。その後 `claude agents --json --all` に `spike-` で始まるセッションは 0 件だった。
- `zellij kill-session spike-attach` + `delete-session` の後、`zellij list-sessions` に残っているのは `fleet-main` だけ。
- `claude attach` / `seat-attach.sh` / `client.py` のプロセスは残っていない。既存のセッション(fleet-leader, main-leader など)は触っておらず、状態も変わっていない。
- リポジトリの変更と PR は無し。副作用として、scratchpad の `cwd` ディレクトリに Claude の workspace trust を 1 件追加した(~/.claude.json)。
