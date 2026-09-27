# P0 実装の E2E — 報告

- 実施: 2026-09-26 10:47–11:33 / Claude Code 2.1.283 / macOS / 全席 `sonnet` (pm も sonnet)
- 実行者: fleet の driver (task-p0-impl)。owner の役は driver が CLI で演じた (依頼を `yamato send` で入れ、`yamato up` を打つだけ。途中で席に手を出していない)
- 場所: driver の scratchpad に、使い捨ての git repo (`calc.py` と `test_calc.py` だけ。remote なし) と艦フォルダを run ごとに作った。艦フォルダは repo の外
- 席の名前は `<艦>.<席>` (`e2e.pm`、`e4.impl` など)。既存のセッションと zellij の `fleet-main` には触れていない

## まとめ

| 完了条件 | 結果 | 根拠 |
|---|---|---|
| owner の依頼 1 件を `yamato up` から無人で通す | ✅ run 4 | pm が T-001 を作って impl に割り振る → impl が実装・commit・報告 → pm がレビューで差し戻し → impl の新しいシフトが直して報告 → pm が確認して done → 全席が引き継ぎを書いて止まる |
| 時間の上限による終業 | ✅ run 2・3・7 | idle の pm を deadline watcher (asyncRewake の Stop hook) が deadline の 0 秒後に起こし、pm は引き継ぎを書いて seat-stop した。作業中の impl には `send` の時間チェックが終業を指示した |
| 猶予後の強制停止 | ✅ `down --force` (run 7) | 生きている pm と起動直後の impl を止め、roster に「引き継ぎなしで終了」を記録。猶予切れの自動停止 (watchdog) は同じ関数で、単体テストで確認 |
| 使用量の記録 | ✅ | `usage.jsonl` にシフトごとに 1 行 (下の表) |
| セッションの片付け | ✅ | 作った 19 セッションを全て stop + rm。`claude agents --json --all` に e2e / e3 / e4 / e6 / e7 / ut の席は 0 件 |

E2E の途中で見つかった不具合は 4 件。全て直してから、次の run で確認した (下の「見つかった問題」)。

## 手順

```bash
# 1. 使い捨ての repo (git root) を作り、trust する (owner の手作業に当たる。yamato は trust を自動承認しない)
mkdir repo && cd repo && git init -b main && (calc.py / test_calc.py を置いて commit)
claude            # trust のダイアログで「Yes, I trust this folder」(E2E では pty のスクリプトで押した)

# 2. 艦を作り、pm を sonnet に、時間を短くする
yamato ship create e4 --path <dir>/ship --workspace <dir>/repo
sed -i '' -e 's/model: opus/model: sonnet/' -e 's/grace: 20m/grace: 5m/' <dir>/ship/team.yaml

# 3. 依頼を送って起動する (艦が未起動なので send は inbox に記録するだけ)
yamato send e4 pm "calc.py に、2 つの整数の最大公約数を返す関数 gcd(a, b) を足してください。test_calc.py にそのテストも足し、python3 -m unittest が通ること。"
yamato up e4 --for 20m

# 4. 見るだけ
yamato status e4; yamato board list e4 --all; cat <ship>/seats/*/log/*.md
```

未 trust の workspace で `up` した場合 (git repo `untrusted/`): 起動前に次のメッセージで exit 1 になり、セッションは作られなかった。
```
yamato: workspace が Claude Code に trust されていません: .../e2e/untrusted
  一度 `cd .../e2e/untrusted && claude` を実行して trust のダイアログで承認してから、もう一度 up してください。
```

## 各 run の結果

| run | 艦 | 内容 | 結果 |
|---|---|---|---|
| 1 | e2e | gcd を 1 件 | ❌ 不具合 1 (impl は起動したが roster に載らず、impl の seat-stop が拒否された)。それ以外 (board、SendMessage の配送、pm の確認 → done → seat-stop、使用量) は動いた |
| 2 | e2e (作り直し) | 同上 | ❌ 不具合 2 (impl が SendMessage せずに終業し、pm が報告を受け取れずに idle のまま)。この pm を残して、上限での終業の確認に使った → ✅ 11:09:27 の deadline で起こされ、handoff を書いて seat-stop |
| 3 | e3 | 同上 | ❌ 不具合 3 (pm の差し戻しが impl の遅延停止の 10 秒に届いて消えた)。これも残して上限の確認 → ✅ 11:11:43 に起こされて終業。引き継ぎに「T-001 は差し戻し中 (impl 待ち)」と残した |
| **4** | e4 | 同上 | **✅ 無人で完了** (次の節) |
| 5 | e4 (続き) | lcm / is_prime / factorial を 3 task、`--for 3m` | ✅ 止まっていた persistent の pm を `send` が resume (フル id、同じ transcript)。3 件とも done、上限の前に全席が終業 |
| 6 | e6 | 5 task、`--for 90s` → `--for 4m` | pm は残り時間を見て割り当てずに終業した (妥当)。不具合 4 (`up` で resume した pm が前のシフトの判断のまま即終業) を見つけた |
| 7 | e7 | 5 task を 1 件ずつ、`--for 3m`、その後 `down` と `down --force` | ✅ 3 件を無人で done (pm は割り当てのたびに終業し、報告のたびに resume で起きた。resume 4 回)。`down` で idle の pm は watcher に 3 秒で起こされ、作業中の impl は `send` の時間チェックで終業指示を受けて、両方とも引き継ぎを書いて止まった。`down --force` で生きている席を強制停止 |

### run 4 の流れ (無人で完了)

| 時刻 | 席 | 出来事 |
|---|---|---|
| 10:57:41 | owner | `send e4 pm ...` → `up e4` (deadline 11:17:41) |
| 10:57:45 | pm | SessionStart で未読 inbox (依頼) が注入された。T-001 を作り、`send e4 impl` → impl の新しいシフトが起動 |
| 10:57:55 | impl | ブランチ `t-001` を切り、gcd とテストを実装、commit、board に note、`send e4 pm` → 「宛先は生きている」→ SendMessage (`success:true`) → handoff → `seat-stop --delivered` |
| 10:58:15 | pm | cross-session message で起き、`git diff main...t-001` と unittest で確認。`__pycache__/*.pyc` が commit されているのを見つけて差し戻し (`send e4 impl`)。impl は止まっていたので新しいシフト #2 が起動 |
| 10:58:33 | impl | .pyc を untrack して `.gitignore` を足し、追加 commit、報告、終業 |
| 10:58:48 | pm | 確認 (`git archive t-001` を展開してテスト) → `board set T-001 state=done` (archive へ) → handoff → seat-stop |
| 10:59:10 | — | 全席 off。board: T-001 done (archive)。repo: `t-001` に 2 commit、push なし、main は元のまま |

board の項目 (archive/T-001.md) の経緯欄:
```
- 09-26 10:57 pm: 作成
- 09-26 10:58 impl: 着手
- 09-26 10:58 impl: 実装完了: gcd追加(abs正規化, gcd(0,0)=0) / テスト: 4件OK
- 09-26 10:58 impl: 差し戻し対応: __pycache__ を untrack、.gitignore 追加 (追加コミット)
- 09-26 10:58 pm: 確認: t-001 の diff を読み、unittest 4件OK、pyc 除去済み。merge 待ち
```

### 使用量 (`usage.jsonl`)

run 4 (1 件、差し戻し 1 回):

| 席 | shift | 秒 | in | out | cache 書き | cache 読み | 計 |
|---|---|---|---|---|---|---|---|
| impl | 1 | 36 | 14 | 2,243 | 20,101 | 266,336 | 288,694 |
| impl | 2 | 32 | 12 | 1,517 | 19,018 | 211,719 | 232,266 |
| pm | 1 | 85 | 24 | 2,310 | 41,348 | 346,924 | 390,606 |
| | | | | | | | **911,566** |

run 7 (4 件 + 終業と強制停止、12 シフト): 計 2,083,696 トークン。1 シフトあたり 11 万〜35 万で、ほとんどが cache 読み。`down --force` で起動直後に止めた 2 シフトは 0 (transcript に assistant の行がまだ無かった)。

## 見つかった問題と対処

1. **起動出力の id が色つきで、解析に失敗した (run 1)**。席 (pm) の Bash から `claude --bg` を呼ぶと、`backgrounded · \x1b[36mecd6cecf\x1b[39m · e2e.impl` のように id が ANSI で色づけされる (driver の shell から呼んだときは色なし)。impl は起動していたのに yamato はエラーを返し、roster に載らなかった。→ 出力から ANSI を取り除いて解析する。加えて、出力の形が変わっても、同じ名前で起動直後のセッションが 1 つだけ見つかれば、それを採用して漏らさない
2. **SendMessage の配送漏れ (run 2)**。impl が `send` と `seat-stop` を 1 つの Bash にまとめ、「宛先は生きている → SendMessage で届けよ」という出力を読む前に終業を受け付けられた。idle の pm には何も届かず、pm は報告を待ったまま止まった。→ 生きている宛先への送信を「送り手が届けるはずのもの」として記録し、宛先がまだ inbox を読んでいなければ `seat-stop` が止める (`--delivered` を付ければ通る。team.yaml の `seat_stop.require_delivery: false` で外せる)。役割プロンプトに「SendMessage は send の出力を見てから別の呼び出しで。send と seat-stop をまとめない」を足した
3. **遅延停止の間に届いたメッセージが消えた (run 3)**。seat-stop のあと 10 秒の間は席が生きているため、`send` は「生きている」と判定し、pm は SendMessage で差し戻しを送った。impl はそれを受け取ったが、直後の stop で止まり、差し戻しは宙に浮いた。→ 宛先が終業処理中 (`stopping`) なら、`send` は止まるのを待ってから新しいシフトで起こす (persistent なら resume)
4. **`up` で resume した captain が即終業した (run 6)**。resume の指示文が「inbox に新しいメッセージがある」の固定だった。そのため pm は前のシフトの結論 (時間が足りない) を引きずり、新しい 4 分の上限を見ずにまた終業した。→ `up` からの resume では「艦が起動された (deadline …)。前のシフトの時間の判断は忘れ、引き継ぎの次にやることから再開」と伝える

## 観測したこと (直していない・P1 以降)

- **Stop hook による終業指示は、実機では観測できなかった**。上限を迎えた席は、idle なら watcher で起き、作業中なら自分の `send` の時間チェックで終業を知った。ターンの終わりに Stop hook が `decision: block` を返す経路は、単体テスト (`tests/test_inject_hooks.py`) でだけ確認している
- **猶予切れの自動の強制停止 (watchdog) も実機では起きなかった**。全席が猶予の前に自分で止まったため。止める処理は `down --force` と同じ関数で、そちらは実機で確認した
- **async の deadline watcher は 20 分待っても生きていた** (run 2・3)。hook の `timeout: 86400` が効いている。席を stop すると watcher のプロセスも消えた
- **pm は SendMessage で内容を受け取ると `yamato inbox` を読まないことがある**。そのため inbox の未読が残る (run 4 の pm で 2 件)。次のシフトでは既読の報告が「未読」として注入される。害は小さいが、役割プロンプトで強めるか、SendMessage の本文から既読にする道具を考える
- **作業ツリーを共有しているので、ブランチが積み重なった** (run 7: `t-002` は `t-001` から、`t-004` は `t-003` から切られた。merge 前のため)。どこから切るかは git の流れの方針なので、役割プロンプトか P1 の `yamato worktree` で扱う
- **pm は短い上限だと、割り当てのたびに終業して resume で起き直した**。persistent の resume は 4 回とも `woke session … with its saved options` になった (コピーはできなかった)
- **`down --force` と起動の競合**: pm が `send` で impl を起動している最中に `down --force` すると、起動が force の一覧取得より後に終わった場合、その席は止まらない (run 7 では間に合った)。直後の `status` / `send` / watchdog が deadline を見て止めるので、放置はされない
- warning `no agent named 'impl' — spawning with default template` が毎回出るが、役割プロンプトは効いていた (verify-p0-b Q2 と同じ見かけだけの警告)
- `env_unset` (GH_TOKEN を外す) が daemon 経由の席に効くかは、今回も確かめていない (verify-p0-b の未検証事項のまま)

## 片付け

- 作ったセッション: run 1 の 2 件と、e2e / e3 / e4 / e6 / e7 の 17 件。全て stop + rm した。`ut` (未 trust の確認) はセッションを作っていない
- watchdog のプロセスは残っていない
- 副作用: scratchpad の repo 5 つと probe 用の dir を、`~/.claude.json` の workspace trust に追加した
