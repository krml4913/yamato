# yamato

役割の違う複数の AI エージェントが「艦 (ship)」として協調して仕事を進める、常設チームのためのシステム。
Claude Code の background session の上に薄く乗る。agent-fleet の後継。

- 艦 (ship): チーム。役割の構成は `team.yaml` で自由に定義する
- captain: 艦の司令塔 (仕事を分けて割り振り、回収する)
- admiral: 窓口。艦の出撃と帰投、全艦の一望
- owner: 人間

状態: 設計段階。設計書は [docs/design.md](docs/design.md) (冒頭 §0 が最新の決定)。

## docs
- [design.md](docs/design.md) — 設計書
- [research-claude-primitives.md](docs/research-claude-primitives.md) — Claude Code の仕組みの調査 (2.1.282)
- [spike-zellij-attach.md](docs/spike-zellij-attach.md) — zellij 表示層の検証
- [review-da-v0.md](docs/review-da-v0.md) — 設計書 v0 への devil's advocate レビュー
