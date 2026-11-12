# FlowMask 后端业务完善分批实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有单机架构内闭合识别、风险、脱敏、任务、权限、导出和审计，优先防止错误结果及原文越界。

**Architecture:** 保留 FastAPI、Document/Segment、SQLite 和现有 Go 采集能力。统一证据与定位，逐步补充有效配置、覆盖状态、任务生命周期和事件关联；不引入消息中间件、分布式队列、企业身份平台或替换前端。

**Tech Stack:** Python 3.11+、FastAPI/Pydantic、标准库SQLite、现有Vue/Element Plus、Go与现有测试体系。

**Spec:** [后端业务全面审计](../../后端业务全面审计-2026-11-05.md)，代码基线`73aaf10`。本文保留完整分批计划；本轮按用户收窄范围实施四项P0，实际完成状态见下表和第一批交付。未覆盖项不能据计划步骤自动认定完成。


## 2026-11-05本轮执行状态

| 计划任务 | 状态 |
| --- | --- |
| 任务1 | 核心结构化定位/回写、数值字段、SQL混合值及格式边界已修复，普通路径兼容；以无歧义path作稳定标识，类型化location回写，未另加冗余segment_id |
| 任务2 | 仅交叠覆盖、泛化安全已处理；数字归一化F01、完整PEM F06未完成 |
| 任务3 | 补无位置类别的保守保护；完整引擎状态、风险聚合未实施 |
| 任务4 | 本轮F11副本降级/F12持久化已修复；F22 URL元数据仍开放，强主体模型留第三批 |
| 任务5 | 快照、会话/TTL/预算、策略预览与导出、加密替换复用已实现并验证 |
| 任务8 | 独立因果诊断后仅修测试握手，普通/race各300次通过 |
| 其他任务 | 未实施，按后续批次处理 |

验证与API迁移见[第一批交付](../../第一批P0修复交付-2026-11-05.md)。旧直接导出保留fresh语义，只有result_id导出才提供两阶段快照一致性。

## Global Constraints

- 保留公开API与旧响应字段；新增字段有默认值，旧历史数据缺失信息标unknown，不伪造补齐。
- 保留所有现有功能；已支持能力不能通过删除或Mock替代来“修好”。真实模型与演示后端明确区分。
- Python内容检测原文只在受限内存中用于识别/替换；任务恢复不得通过暗中持久化原文实现。既有原始PCAP抓包/导出功能保留并明确权限与原文风险，不能为满足口号删除旧能力。
- 优先复用现有解析器、检测器、存储、ingest与审计签名；不盲目重写架构。
- 风险等级和替换策略分开；未授权、持久化、外发有服务端最低策略。
- 用户已指定四批顺序；第一批同时补直接消费检测结果的最小防泄露边界，第三批再完善主体/事件/统计，第四批再扩展流量重组。
- 每项先写能暴露现状的失败测试，确认失败原因，再最小实现，定向与回归通过后独立Conventional Commit。提交仅包含该项文件，不推送远程。
- 文档提交不触发产品版本升级；未来代码批次按仓库版本规则更新所属组件记录。

## Review Focus

1. 点/括号/空键/CSV重名列：展示路径绝不能用作唯一回写身份（第一批任务1）。
2. 跨边界归一化、部分重叠和PEM：命中去重不能留下未处理敏感尾部（第一批任务2）。
3. 模型未执行/超时/同版本非确定输出：能力状态真实，策略重算使用不可变命中集（第一批任务3、5）。
4. 中途撤权、队列启动失败、进程被终止：每次敏感操作校验，任务不永久running且不重复写入（第一批任务4、第二批任务7、第三批任务9）。
5. 缺包/截断/无法解析/大文件：明确unknown/partial和丢弃范围，不能以无命中代替全文安全（第四批任务11～13）。

## 第一批：统一检测、风险评估与脱敏

### 任务1：结构化定位与安全回写（F03～F05、F09、F13）

文件：`server/app/parsers/base.py`、`json_parser.py`、`csv_parser.py`、`sql_parser.py`、`registry.py`；`server/app/tasks/document_export.py`、`tasks/export.py`；`api/routes_detect.py`、`api/schemas.py`。

接口：为Segment新增稳定`segment_id`与类型化`location`，保留现有path用于展示；内部`apply_masked_segments(document, replacements)`校验每个已检出片段恰好回写一次，失败抛出安全错误。格式选择兼容旧text自动识别，通过明确字段/模式引入严格解析。

- [ ] 在`server/tests/test_parsers.py`、`test_document_export.py`、`test_detect_export.py`增加失败用例：JSON点/方括号/空键/嵌套碰撞、数值手机号；CSV重复/空表头；SQL混合数字NULL和多行元组；显式csv检测；JSON→sql不得200返回原文。
- [ ] 运行上述文件，确认失败来自路径/解析/回写缺陷，记录基线。
- [ ] 实现唯一定位、数值候选、完整SQL列索引、输入×输出支持矩阵；所有安全回写失败明确4xx，未修改内容保留原结构与类型。
- [ ] 定向测试通过，再跑Python全套；检查格式往返、Unicode与SQL转义；独立提交`fix(masking): guarantee structured export replacements`。

### 任务2：归一化、重叠证据与秘密范围（F01、F02、F06）

文件：`identify/normalize.py`、`rules.py`、`scoring.py`、`masking.py`、`types.py`；测试`test_normalize_evaluate.py`、`test_identify.py`、`test_pipeline.py`。

接口：保留原文候选和归一化映射；形成`evidence_hits`与`replacement_spans`两个职责明确的集合。继续暴露旧hits计数字段，但说明计数规则。

- [ ] 测试两个空格/制表符分隔手机为2命中；分组单手机仍可识别；邮箱内含sk秘密保留高危证据；覆盖同起点/包含/交叉/相邻；多行/未闭合PEM主体不残留。
- [ ] 运行失败测试后实现有界候选归一化、确定性冲突策略和完整PEM范围；原始Document不就地修改。
- [ ] 断言替换区间不越界、不重复替换、不留敏感尾部；重跑上述与导出测试并独立提交。

### 任务3：统一证据评分与引擎执行状态（F07、F08、F10、F27的检测部分）

文件：`identify/pipeline.py`、`l2.py`、`l3.py`、`ner.py`；`api/deps.py`、`routes_detect.py`、`routes_tasks.py`、`schemas.py`。

接口：新增兼容的`engine_runs[]`、`effective_options`、`coverage`；引擎状态为disabled/skipped/executed/unavailable/timeout/error。偏移重基仅改位置，评分统一使用保留证据。三态请求开关区分跟随默认和明确false。

- [ ] 在`test_pipeline.py`、`test_l2_service.py`、`test_l3_client.py`、`test_api.py`写失败测试：显式关闭零调用；请求启用但能力缺失需明确未执行；L2断连/降级；前缀与全角不丢语义类别/分数；L3灰区外skipped。
- [ ] 分离默认配置和客户端能力构造，保留L1结果；不要以链名猜执行成功。补文档汇总最高已知风险，标明partial时不能给全来源SAFE；记录级共现按独立约定测试，不跨无关记录累加。
- [ ] 定向与全套回归通过，检查旧响应字段仍存在，再独立提交。

### 任务4：持久化/未授权的最低脱敏边界（F11、F12、F22的最小保护）

文件：`identify/masking.py`、`api/deps.py`、`routes_dynamic.py`、`routes_detect.py`、`tasks/service.py`、`traffic/streams.py`、`traffic/importer.py`。

接口：`MaskContext`区分preview/storage/export/unauthorized；最终策略由后端取安全下限，不改变真实risk_level。来源标识不得含query原值；解密与原文查看共用能力判定接口，为第三批留兼容入口。

- [ ] 在`test_dynamic.py`、`test_api.py`、`test_batch.py`、`test_traffic.py`增加失败测试：未授权SAFE/L1不返回原值；高危任务强制L1不落原值；URL query含身份证但任务/path/告警/导出无原值。
- [ ] 实现仅最小服务端保护；保留本机即时低危警示预览，不把其副本直接存库；严格说明自报operator不能作为可信主体。
- [ ] 查询隔离库与导出内容验证合成原值缺失，检测到的risk_level不被策略覆盖；回归后独立提交。不要读取/改写真实历史库。

### 任务5：检测快照与策略重算（F14、F27的版本部分）

文件：新增`server/app/identify/snapshots.py`；修改`pipeline.py`、`api/routes_detect.py`、`schemas.py`、`tasks/document_export.py`；最小接入`web/src/api.js`、`web/src/views/DetectView.vue`。

接口：`SnapshotStore.put/get`保存不可变文档/证据及版本，有效期5分钟、最多32项且总估算内容不超过32MiB（实现前按资源预算复核）；句柄随机不可猜、绑定可信会话/主体并逐次授权。不持久化原文。检测响应新增result_id；新导出支持结果句柄，旧直接导出API仍保留并明确“重新检测”语义。

- [x] `test_detect_export.py`增加规则变更/模型随机输出/选项变化/过期/跨主体/缓存超限测试；确认旧API保持可用；前端单测验证导出使用已检测快照。
- [x] 先用规则/选项指纹发现已知漂移返回409，再接不可变证据集，导出期间不再调用识别器；换策略只重建副本。失效句柄410、越权403、资源不足明确响应。
- [x] 测试模型调用次数、预览与最终导出命中集合一致、没有原文持久化；Python全套、Node单测与生产构建通过后独立提交。

第一批出口：上面所有P0复现不再残留已检出原值；不得仅因625项旧测试通过就宣布闭环。JSON/CSV/SQL、模型控制和快照专门测试必须通过。证据快照内存安全随首次上线一起完成，不能推迟到第三批。

## 第二批：规则管理、任务生命周期与异常处理

### 任务6：版本化规则与有界执行（F15、F16）

文件：`api/routes_rules.py`、`schemas.py`、`core/db.py`、`identify/rules.py`、`pipeline.py`；测试`test_rules.py`、`test_pipeline.py`。

接口：兼容既有启停PATCH，增加明确可编辑字段和expected_revision，规则集产生内容hash。每任务开始冻结规则元组和版本。对正则设可中断执行预算；规则集规模/候选长度有边界，超限返回规则级错误。

- [ ] 先测试编辑确实生效、未知字段不能200忽略、并发修订冲突409、运行中任务保持旧版本、后续任务用新版本；耗时模式在受控子进程超时后其他任务仍完成。
- [ ] 实现规则更新与迁移，保留内置规则及旧覆盖数据；坏规则显式提示，不能静默把整层称成功。
- [ ] 测试通过，记规则变更事件待第三批完整接入，独立提交。

### 任务7：单机有界任务状态机（F17、F18、F27的任务部分）

文件：`tasks/service.py`、`core/db.py`、`api/routes_tasks.py`、`schemas.py`、`main.py`；必要时新增`tasks/runner.py`；测试`test_api.py`、`test_batch.py`及新增`test_task_lifecycle.py`。

接口：保留pending/running/done/failed，增加cancel_requested/cancelled/interrupted及明确进度、error_code/retryable。取消轮询在片段边界，提交前再次校验；数据库条件更新防止终态覆盖。重试必须带重新提交来源，不保存原文；幂等键只在调用方显式提供时使用。

- [ ] 失败测试覆盖排队/执行中取消、审计事务前取消、第二个调度失败仍处理第三文件、并发队列满、重复幂等键、进程结束重启、所有选项含NER/词表持久化。
- [ ] 实现有界执行器与生命周期，启动遗留非终态转interrupted；入队失败补偿状态；批量item区分accepted与最终完成，汇总部分成功。
- [ ] 合成子进程终止验证无永久running、无重复finding/审计；完整Python回归、独立提交。

### 任务8：修正Go回压测试的时序假设（验证债务，非产品丢包结论）

文件：`internal/app/dlp_test.go:253`，复用现有blockingStore。

- [x] 先`go test ./internal/app -run TestOnlineDLPCountsDroppedWhenQueueFull -count=20`记录不稳定基线。
- [x] 增加“首条确实进入worker”的测试握手，再填满队列；不用加任意sleep掩盖调度。
- [x] 单测重复20次与`go test ./...`、`go build ./...`、`go vet ./...`通过后单独测试提交。未通过前不能声称Go整体验证全绿。

## 第三批：审计、权限、导出与统计

### 任务9：可信主体、能力判断与业务事件（F11完整模型、F19、F20、F27）

文件：`api/deps.py`、`core/config.py`、`core/db.py`、`audit/chain.py`、各敏感操作路由、`tasks/service.py`、`alerts.py`；测试`test_audit.py`、`test_dynamic.py`、`test_verify_export.py`。

接口：凭据映射到本机owner或配置能力集（detect/manage_rules/export/reveal/decrypt），operator头仅为审计标签；不引入用户注册/SSO。新增版本化审计事件，旧签名仍可验；任务携带主体快照，但敏感导出/解密时用当前权限重新判断。

- [ ] 失败测试覆盖伪造operator、撤权后导出/解密、无命中任务、失败/取消/重试、规则变更、告警处置与所有导出入口均有事件且不含原值。
- [ ] 完成鉴权依赖和事件写入：提交/终态/规则变更/策略重算/访问拒绝/导出准备，携带task/result/finding与版本；强事务保护业务操作与事件。
- [ ] 审计校验准确说明现存链；可选本地Head快照只能防部分误删，外部可信锚点另建议。旧导出验签、迁移、前端能力反馈回归通过后提交。

### 任务10：真实统计与隐私发布语义（F21、F28）

文件：`tasks/service.py`、`privacy.py`、`api/routes_stats.py`、`schemas.py`、`web/src/views/StatsView.vue`；测试`test_privacy.py`、`test_api.py`。

接口：旧total_findings继续表示记录数，增加processed_segments/sensitive_segments/hit_count/unknown_segments，task_id过滤范围统一。保留现有诊断导出，明确它不提供差分隐私保证；新增受保护聚合发布必须只返回聚合并定义预算/邻接假设。

- [ ] 测试两任务两手机号口径：tasks=2/processed_segments=2/sensitive_segments=1/hit_count=2；单任务tasks=1；部分检测unknown不变SAFE。
- [ ] 测试隐私发布包无精确分布/明细可还原项，l检查不满足的行为明确；如果不实施严格发布模式，必须收窄声明而非假称实现。
- [ ] 对比数据库事实与看板/导出；Python、前端单测和构建通过后提交。个人级隐私保证另需评审，不能把finding当个人记录。

## 第四批：批量、流量和其他高级能力

### 任务11：网络重组和完整性（F23、F24）

文件：`traffic/pcap.py`、`frames.py`、`streams.py`、`importer.py`、`api/routes_traffic.py`、`schemas.py`；测试`test_traffic.py`。

- [ ] 合成帧失败测试：同五元组新连接、重传/乱序/缺口/回绕、超1MiB敏感后缀、不足Content-Length、坏PCAP尾、混opaque、PCAPNG EPB/SPB支持差异。
- [ ] 连接世代与序列范围明确；缺口不拼连续正文。上传/任务/结果贯通coverage计数，声明协议支持矩阵；未支持HTTPS/gzip/chunked不伪装安全。
- [ ] 相同消息不同分包获得一致结果；partial/unknown和已发现风险并存；边界回归通过后提交。不擅自实现TLS解密。

### 任务12：live资源预算、停止与任务回收（F25、F31、F32）

文件：`traffic/live.py`、`api/routes_traffic.py`、`schemas.py`；测试`test_live.py`。

- [ ] 测试181和5000条待处理停止总量守恒、异步失败回报、排空超时、队列按字节受限、大文件增量读取和samples相对路径。
- [ ] 有界read与游标缓冲；停止排空或明确discarded，单独计submitted/completed/failed；路径区分waiting与invalid。重启会话interrupted，不假称无原文checkpoint恢复。
- [ ] 在受限合成文件上测RSS/停止时延，记录硬件和输入规模；检测/写库故障注入下无静默漏检，回归通过后提交。

### 任务13：采集分支对齐与文档能力状态（F26、F29、F30及批量最终联调）

文件：`cmd/flowmask/main.go`、`internal/app/dlp.go`、`server/app/api/routes_traffic.py`、`plugins.py`、`api/routes_health.py`、`l2_service/`、`l3_service/`、`parsers/pdf_parser.py`、`docx_parser.py`、启动脚本及前端能力提示。

- [ ] 先给Go和Python维护同一合成样本契约：跨TCP包身份证、规则更新、来源安全ID、风险与审计关联；明确Go旧L1-only模式仍可运行。
- [ ] 优先通过既有ingest桥接完整消息，避免第三套识别器；桥接失败反馈排队/丢弃。模型服务readiness与real/mock明确，启动按已启用能力验收；PDF/DOCX/NER截断返回coverage。
- [ ] 多文件部分失败/取消/重试与PCAP/live综合联调；模型真实权重、持续网卡、exe打包另列实机验收，无环境就保留U，不能用mock补签。

## 每批交付门槛

- [ ] 失败探针转为持久回归测试，证明原缺陷确实被捕获并修复；旧测试通过只是其中一项。
- [ ] Python全套；涉及Go时build/vet/test；涉及前端时Node测试与生产构建；只在发生变化或失败时扩大重复验证。
- [ ] 检查隐私、旧API、历史数据库迁移、原格式输出和可回滚性；不在真实用户库上做破坏性试验。
- [ ] 同步本报告问题状态、验收记录、版本文档与HANDOFF；独立提交，记录仍未完成与未验证项。

## 范围建议，不自动实施

外部审计锚点、企业账号、TLS解密、全格式保真转换、扫描PDF全面OCR及持久化原文恢复需要单独产品决定。当前优先提供真实能力状态和明确错误。完成第一批后根据新验证结果细化后续接口与预算，不把本计划当作允许大规模重写的授权。
