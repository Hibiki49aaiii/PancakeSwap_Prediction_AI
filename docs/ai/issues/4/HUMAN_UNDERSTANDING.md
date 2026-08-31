# Issue #4 — Human Understanding

## What

Stage 4 の連続 Shadow runtime に、campaign 全体の Evidence checkpoint を組み込む。

現在の runtime は1 cycleごとの処理・ledger・campaign gate評価までは自動だが、campaign Evidence のファイル化だけが別スクリプト手動実行になっている。今回、この最後の運用ギャップを埋める。

## Why

長期 campaign は数日間・多数roundにまたがる。単にruntimeが動くだけではなく、

- 今どこまで進んだか
- 最後にgateを満たした証跡は何か
- 後続の未完了cycleで成功証跡を失っていないか

を機械的に追跡できる必要がある。

## How

runtime core は今まで通り campaign report を返すだけにする。

CLI がその report を使い、

1. `latest` Evidence を各成功cycle後に原子的更新
2. campaign gate成立時だけ `last-success` を原子的更新
3. gate未成立時は既存 `last-success` を一切触らない

という保存規約を担当する。

standalone Evidence script も同じpayload builderを使い、Evidence定義を二重管理しない。

## Important Decisions

- campaignをCLIで再auditしない。runtimeが既に同一 `purge_rounds` で評価した report を使う。
- filesystem pathをruntime coreへ持ち込まない。
- PnLが正だから成功、という条件は追加しない。
- SQLiteはWAL modeなので、物理DBファイルSHAだけを完全性の根拠にしない。hash-chain head/event count/campaign digestを論理的な主証跡とする。
- WALを強制checkpointするような挙動変更はしない。

## Invariants

絶対に壊してはいけない条件:

- private keyを保持しない
- transaction signingをしない
- mainnet broadcastをしない
- funded executionをしない
- future/final-pool leakageを許可しない
- Shadow Ledgerはappend-only
- source integrity failureはfail closed
- CI成功や短期PnLだけでprofitabilityを主張しない

## Failure Modes

- disk full / permission failureでEvidenceを書けない
- ledger integrityが壊れている
- source route/provenance validationが失敗する
- runtime cycle自体が失敗する

これらは成功として隠さない。

campaign sample数が不足しているだけの場合は「失敗」ではなく「incomplete progress」としてlatest Evidenceに残す。

## Change Impact

将来、systemd/container/supervisorで長期runtimeを運用するときも、同じEvidence APIを使ってcampaign状態を監視できる。

Stage 6A等がStage 4証跡を参照する場合も、手動生成物ではなく継続的に保存されたlast-successを入力候補にできる。ただし、その接続自体はIssue #4のScope外。
