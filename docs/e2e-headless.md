# P1-3 `shift: headless` と `run-headless` の実機確認 — 報告

- 実施: 2026-09-26 12:02 / Claude Code 2.1.283 / macOS / 席は `sonnet`
- 実行者: fleet の driver (task-p1-headless)。owner 役は driver が CLI で演じた
- 位置づけ: design-p1 §4 (§10 の 3 番) の実装の確認。**実機は 1 シフトだけ**回した。時間切れ・失敗の判定・連続シフトなどの残りの分岐は、偽の claude (`tests/fake_claude.py` + `tests/fake_claude_lib/print_mode.py`) を使った単体テスト (`tests/test_headless.py`) で確かめた

## まとめ

| 確かめたこと | 結果 |
|---|---|
| headless の席が `run-headless` から `claude -p` で 1 シフト働く | ✅ inbox の依頼と担当の項目を読み、T-001 の経緯に要約を書き、handoff.md を上書きして `seat-stop` し、自然に終わった (13 秒) |
| 起動レシピ (design-p1 §4.2) | ✅ `--session-id` を先に roster に書き、stream-json の最初に `system/hook_response [SessionStart]` が流れた。`--bare` なし、`< /dev/null` |
| 終了後の記録 | ✅ `usage.jsonl` (`source: result`、`total_cost_usd`、`num_turns`、最後の `rate_limit_event`)、roster (`outcome: 正常`、`endReason: seat-stop`)、events (`shift_start` / `shift_end`) |
| 終了報告 | ✅ `report_to: owner` に yamato の定型文。席の出力は入っていない |
| async の Stop hook (wait-deadline) が `-p` の終了を止めないか | ✅ 止めない。`seat-stop` の 3 秒後に終了し、hook のプロセスも残らなかった |
| transcript から数えた使用量が `result` と一致するか (時間切れのときの数え方) | ✅ 完全に一致 (message.id で重複を除いて 4 messages、in=8 out=824 cache_write=9068 cache_read=53763) |
| 片付け | ✅ ship を `down --force`、watchdog の終了を確認。`claude agents --json --all` に e2eh の席は 0 件。作った transcript は削除した |

## 手順

```bash
# 使い捨ての repo (notes.md だけ) を作って trust する (pty のスクリプトで trust ダイアログの「Yes」を選んだ。既定は「No, exit」なので下矢印が要る)
# 艦を作り、席を headless の researcher 1 つにする (hub も researcher、報告先は owner。pm / impl は起こさない)
yamato ship create e2eh --path <dir>/ship --workspace <dir>/repo
#   team.yaml: hub: researcher / roles.researcher: {model: sonnet, shift: headless, max_duration: 15m, report_to: owner}
#   roles/researcher.md: board mine で担当を見る、結果は board set --note、終える前に handoff.md と seat-stop
yamato board add <ship> "notes.md を 3 行で要約する" assignee=researcher state=active --by owner
yamato send <ship> researcher "T-001 をお願いします。workspace の notes.md を読み、3 行の要約を T-001 の経緯に書いてください。"
yamato up <ship> --for 30m     # hub (researcher) を起こす = run-headless を切り離して起動
```

`up` の出力:
```
艦 e2eh を起動: deadline 09-26 12:32:31 (稼働 30m00s, 猶予 20m00s)
  captain 席 researcher: headless のシフトを起動した (run-headless)
```

## 結果

席の作業ログ (`seats/researcher/log/2026-09-26.md`):
```
- 12:02:31 シフト開始 #1 (headless) session=ca5d479a-7f11-481f-bff0-7ce025be2a9a
- 12:02:32 SessionStart (startup) session=ca5d479a-7f11-481f-bff0-7ce025be2a9a
- 12:02:41 seat-stop: 終業を受け付けた (headless)
- 12:02:44 シフト終了 #1 (headless, 正常, exit 0)
- 12:02:44 使用量 shift#1 (result): in=8 out=824 cache_write=9068 cache_read=53763 (計 63663, 4 messages, turns=4, cost=$0.0553)
- 12:02:44 headless: 終了報告を owner に送った (inbox #1)
```

stream-json の流れ (`seats/researcher/headless/shift-1.jsonl`): `system/hook_started [SessionStart]` → `system/hook_response [SessionStart]` → `system/init` → assistant → `rate_limit_event` (allowed) → assistant / user の往復 ×3 → `result` (`subtype: success`、`terminal_reason: completed`、`is_error: false`)。stderr は空。

T-001 の経緯 (席が `board set --note` で書いた):
```
- 09-26 12:02 researcher: 要約: 1) yamato は Claude Code の background session の上に乗る常設チーム。 2) headless の席は claude -p を 1 シフト 1 回走らせる。 3) 報告は yamato の定型文で hub に届く。
```

owner の inbox (yamato の定型文):
```
[#1 09-26 12:02 from yamato] [yamato] researcher の headless シフト #1 が終了 (T-001, 正常)。項目ファイル: <ship>/board/items/T-001.md。作業ログ: <ship>/seats/researcher/log
```

`usage.jsonl` の 1 行 (抜粋):
```json
{"seat": "researcher", "shiftNo": 1, "sessionId": "ca5d479a-…", "input_tokens": 8, "output_tokens": 824,
 "cache_creation_input_tokens": 9068, "cache_read_input_tokens": 53763, "messages": 4, "models": ["claude-sonnet-5"],
 "source": "result", "total_tokens": 63663, "shift": "headless", "role": "researcher", "outcome": "正常",
 "total_cost_usd": 0.0553, "num_turns": 4, "duration_ms": 11240,
 "rateLimit": {"status": "allowed", "resetsAt": 1790403600, "rateLimitType": "five_hour",
               "unifiedWindows": {"five_hour": {"utilization": 0.89, …}, "seven_day": {"utilization": 0.13, …}}}}
```

events.jsonl: `board_add` → `send` (researcher) → `shift_start` (`how: headless`) → `board_set` (席の要約) → `shift_end` (`reason: seat-stop`、`handoffWritten: true`) → `send` (owner、定型文)。

## 見つかったこと

1. **呼び出し元のセッションの印を `-p` に渡さない** (直した)。`-p` は起動元の環境をそのまま使う (検証 D V1)。driver のセッションから起動した対話の `claude` は `Transcript saving is off — inherited CLAUDE_CODE_CHILD_SESSION marker` と警告した。席の Bash から `send` した場合も同じ環境になるので、transcript が残らず、時間切れのときの使用量の数え方 (transcript から) と、後から `claude --resume` で開くことが効かなくなる。`claude.PRINT_CALLER_ENV` (`CLAUDE_CODE_CHILD_SESSION`、`CLAUDE_CODE_MESSAGING_SOCKET` / `_TOKEN`、`CLAUDE_CODE_BRIDGE_SESSION_ID`、`CLAUDE_CODE_SESSION_ATTENDED`、`CLAUDE_CODE_EXECPATH`、`CLAUDE_PID`) を headless の起動で外す。直したあとの実機の run では transcript が保存され、上の数え方の一致を確かめられた
2. `up` の hub が headless のとき、出力の表が `spawned` を知らず KeyError になった (単体テストで見つけて直した)
3. SIGTERM が SessionStart hook より先に届くと hook_response が出ないので、「hook が走った印なし」を失敗にするのは、`system/init` まで進んだか、自然に終わった run だけにした (単体テストで見つけた)

## 実機で確かめていないこと

- **時間切れ** (SIGTERM → exit 143、結果の行なし、transcript から数える): 検証 D V4 の結果に合わせて、偽の claude で確かめた。transcript からの数え方は、上の実機の transcript が `result` と一致することで確かめた
- **枠切れ・API エラー** (検証 D V5 の【要検証】のまま): `is_error` / `api_error_status` / `terminal_reason` での判定と「枠切れの可能性」の分類は、偽の claude (429 + `subtype: success`) で確かめた
- **実行中のシフトへの send** (inbox に積む → 終わる前に未読を確認して次のシフト)、**同じ席の 2 重起動の防止**: 偽の claude で確かめた
- **報告先が生きている bg の席のとき**: 報告は inbox に入るが、yamato は SendMessage を送れないので、idle の captain は次のターン (か SessionStart) まで気づかない。止まっていれば `send` と同じく起こす。P0 の owner からの send と同じ制約で、design-p1 §5.3 (send の再開の規則) で扱う

## 片付け

- `yamato down --force` で deadline を終わらせ、watchdog の終了を確認した (`ps` に `_watchdog` / `run-headless` なし)
- `claude agents --json --all` に e2eh / 予備調査 (probe) の席は 0 件 (`-p` は終わると一覧から消える)
- 作った transcript (`~/.claude/projects/` の e2e の repo と probe の 2 フォルダ) は削除した。使い捨ての repo の trust (`~/.claude.json` の projects) は残っている (scratchpad の中の消えるパス)
