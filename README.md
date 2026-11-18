# 清川水质资料湖

站点上报具名水质读数，资料员核对更正并封账，盘点员按区取得固定明细和核账。交班员按件号转交原件，夜班断开源库后恢复指定截点，复检员按结果号领取该次明细和成功回执。

## 运行

需要 Python 3.11 和标准库。以下命令在项目根目录执行。控制台物化项目时提供 `check_project.py`。

```sh
python3 -B check_project.py
python3 -B demo.py
python3 -B demo.py --verify-handoff
```

完整 unittest 套件也可直接运行 `python3 -B -m unittest discover -v`。演示读取 `samples/day.json` 并使用测试临时目录。它签发 A@10 和 B@12 的盘点，关闭源库后恢复目标 12 和 13，再通过原编号核对后续源更正前的结果。

## Python API

| 导入 | 调用与结果 |
| --- | --- |
| `ledger.transactions.open_source` | `open_source(path, partitions)` 初始化或重开源库。传入同一分区集合可保留原状态，配置冲突报告 `ValueError`。 |
| `intake.accept.apply` | `apply(source, request_id, changes)` 接纳有序变化并返回封账提交号。空序列登记正式空提交。 |
| `inventory.capture.capture` | `capture(source, partition, cut)` 返回 `InventorySnapshot`，含 `snapshot_id`、`partition`、`cut`、`rows` 和 `totals`。 |
| `inventory.store.fetch_snapshot` | `fetch_snapshot(source, snapshot_id)` 按原号返回已保存盘点。 |
| `transfer.publish.issue` | `issue(source, target, snapshot_ids)` 原子签发原件与成功回执，返回件号。 |
| `transfer.publish.package` | `package(source, package_id)` 返回签发时保存的原字节。 |
| `replica.store.open_replica` | `open_replica(path)` 初始化或重开隔离库。 |
| `replica.load.restore` | `restore(replica, payload)` 核对并发布，返回带 `result_id`、`target`、`rows`、`totals` 与 `receipt` 的 `RestoredReport`。 |
| `replica.reports.report` | `report(replica, result_id)` 按结果号读取已发布值。 |
| `intake.accept.merge_sample` | `merge_sample(source, request_id, retired_id, kept_id, retired_before, kept_before, reading)` 人工封账一次样本号合并，返回提交号。 |
| `transfer.publish.issue_identity` | `issue_identity(source, target, snapshot_ids)` 签发带身份证据的 V2 固定件，返回件号。 |
| `replica.reports.lookup_sample` | `lookup_sample(replica, result_id, record_id)` 按结果截点查号，返回有效主号行或 `None`。 |

调用方负责关闭连接。分区及业务标识是非空、可编码为 UTF-8 的字符串，提交号、盘点号、件号和结果号是从 1 开始的正整数。一次转区的输入示例为 `{"record_id":"x","before":{"partition":"A","reading":17},"after":{"partition":"B","reading":19}}`。新增的 `before` 为 `None`，撤销的 `after` 为 `None`。读数使用 Python `int`，包括零。同一批次后续变化看到前序变化形成的新账面。请求重投保留变化顺序，字典键的呈现顺序不影响请求身份。

格式错误为 `InvalidInputError`，请求内容冲突为 `RequestConflictError`，前态矛盾为 `PriorMismatchError`。三者均属于 `ValueError`。未封账截点、盘点组合错误和原件内容不合约均属于 `ValueError`。未知分区或未知有效盘点号、件号、结果号为 `KeyError`。SQLite 写入或提交错误沿 SQLite 异常返回。发布事务失败时可继续读取已有结果。

## 盘点与交接

盘点包括截至封账号的全部生效变化，区内明细按记录号排序。各区汇总由明细计算，一个有效行计一行，读数和按整数微克每升精确相加。选中的空区保留 `(0, 0)` 汇总。报告的 `totals` 是按分区排序的 `(partition, PartitionTotals)` 序列。

V1 原件是 UTF-8 JSON，顶层字段为 `version`、`package_id`、`target`、`origins` 和 `commits`。原盘点携带 `snapshot_id`、`partition`、`cut`、`rows`、`totals`。提交块携带 `number`、`change_count` 和有序 `changes`。读数和读数和字段采用十进制文本，读取时也接受 V1 JSON 整数。原盘点的 `cut` 与有序提交块共同给出各选区的覆盖。提交在某个分区生效的条件是其编号晚于该区原盘点；一笔转区的两侧按各自分区的条件处理。隔离端核对适用侧前态，最后汇总同一目标截点的明细。回执记录输入件号、原字节的 SHA-256 摘要及该次结果号。

## 样本号合并与离线查号（V2）

复检员确认两个在账主号代表同一实物时，`merge_sample` 占用一个正式提交：核对两条完整旧像，撤销被并号原行，在保留号原分区写入核定读数，并在同一事务写入身份关系、行变化与请求回执。被并号自该提交起沿身份链指向保留号；同号、以已被并号为操作数抛 `ValueError`，旧像不符抛 `PriorMismatchError`，异内容或跨操作种类复用请求号抛 `RequestConflictError`。合并请求与 `apply` 共用请求号空间，旧像映射的键顺序不影响请求身份，同请求重投始终返回首次提交号。

`apply` 收到被并号的更正、转区或撤销时，先沿身份链找到当前主号，按该有效行核对分区和读数，写入也归于主号，不另行新增；被并号的 `before=None` 新增被拒绝。普通撤销不建立身份关系。

`issue_identity` 在 V1 原件布局上增加 `version: 2` 和 `identities` 身份声明块（含生效提交、被并号、保留号及两行旧像与合并后行效果），V1 与 V2 件以顶层 `version` 明确区分。早于所有原盘点截点、但仍影响本件查号的合并同样携带可独立核对的声明。`restore` 对 V2 件核对原盘点、提交覆盖、身份声明的完整与一致（缺证、合并环、矛盾去向、身份证据与行账不符均抛 `ValueError` 且不留结果），通过后把报告、回执和供该结果查号的身份索引在同一事务发布。

`lookup_sample` 仅在 V2 结果上可用：沿身份链解析到有效主号，主号已撤销或不在选区返回 `None`，未知样本号也返回 `None`；对 V1 结果查身份抛 `ValueError`，未知结果号抛 `KeyError`。已签发原件、已发布报告及查号结果固定于各自截点，源端后续合并、转区、撤销或重开均不改变。运行核验与断源重开演示：

```sh
python3 -B identity_examples.py
python3 -B identity_examples.py --demo
```
