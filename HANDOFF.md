# FlowMask 批量文档对比交接 · 2026-11-12（一体机 0.27.2）

## 当前状态

已完成批量结果“查看 / 对比 / 明细 / 脱敏文件”、独立对比弹窗、字符级高亮、双向定位、映射同步滚动、差异导航、单栏/双栏、换行、虚拟行和 TXT 下载。原 JSONL 导出保留为“明细”。0.27.2 在 0.27.1 基础上做了对比体验与安全加固：错误响应 no-store、快照覆盖写记账、快照写入失败不影响任务、窄屏视图归一、换行长行定位两阶段校正、单栏/双栏保挂载同步、Escape 先收下拉、点击只联动对侧、字段信息复用 RiskIndicator、乱序/重叠 changes 与重复/负值锚点防御。版本资源同步 0.27.2；未打包、未部署、未发布。

验证：后端 751 通过、5 跳过；Node 43 通过；Edge Playwright 全量 34 通过（对比 7 项）；Vite 构建通过。测试仅使用隔离数据库。临时测试服务已停止；本地 8090 等既有服务未触碰。

## 已定决策

不要重新讨论这些：已识别敏感值不落库（未命中上下文按既有设计保留在脱敏副本中）。新批量任务复用现有 HttpOnly 会话 + Authorization 指纹隔离的 SnapshotStore（默认 5 分钟、32 个、32 MiB 共用预算）；历史/过期/淘汰快照不恢复原文。共享脱敏算子生成码点区间，前端不搜索原值、不模拟脱敏。对比与下载复验实际任务副本一致；权限/密钥/策略异常即拒绝。对比顶部的“命中数”是全部检测命中，批表明细/统计按结果最低级别过滤，口径不同且界面已说明。

结构化和 Office/PDF 使用解析片段视图，不还原原文件版式；下载为按片段顺序连接、与右侧一致的 UTF-8 TXT。原有 JSONL 是检测明细。前端关闭时取消请求并清除模型；服务端依旧按 TTL 清理，不承诺强制擦除堆内存。对比接口成功/失败路径都不得缓存。

## 下一步

当前功能已完成。使用本地现有桌面入口前需更新/重启对应 Python 服务并指向当前 web/dist；本轮未操作桌面入口。已知限制见 `docs/文档对比.md`：虚拟滚动下焦点高亮滚出视口会丢焦点；点击跨行/整段高亮后段时对侧按差异起始行定位；百万行极端文档前端建行模型内存较高；单栏下同步滚动置灰；下载请求不可取消。若未来需要完整 DOCX/PDF 版式对比或原格式下载，独立扩展解析位置与文档导出映射，不退回模糊搜索或长期存原文。

## 关键文件

- `docs/文档对比.md`：完整协议、安全、限制、测试和实际文件清单。
- `web/src/views/BatchView.vue`、`web/src/components/DocumentComparison.vue`、`web/src/components/DocumentPane.vue`、`web/src/comparison.js`、`web/src/api.js`。
- `server/app/api/routes_tasks.py`、`server/app/tasks/comparison.py`、`server/app/tasks/service.py`、`server/app/identify/{masking,snapshots}.py`、`server/app/security.py`。
- `server/tests/test_comparison.py`、`web/test/comparison.test.mjs`、`web/test/e2e/{comparison,workspace}.spec.js`。

---

# FlowMask B 方案前端改造交接 · 2026-11-11

## 当前状态

- 在 `codex/l2-l3` 完成冷白蓝工作台、54:46 双栏检测页和移动端导航。保留八个页面、主题偏好和已有 API。
- 已验证 Vite 构建、18 个前端单测、20 个 Edge 端到端用例和 26 个 Python API 用例。验收截图在 `web/.qa/b-redesign/screenshots/`。
- 桌面 `D:\31387\Desktop\FlowMask.lnk` 调用的 `datausing-main/datausing-main/server/scripts/start_l2l3.ps1` 已更新，通过 `--app-dir` 使用当前工作区服务，通过 `FLOWMASK_FRONTEND_DIR` 使用当前 `web/dist`。继续使用桌面副本的数据与 .env，复用其 L2/L3 进程，重复启动会复用主服务。
- 桌面入口已实测新双栏界面、合成手机号检测、快照预览和导出。入口为 `http://127.0.0.1:8090/app/`。启动脚本备份后缀为 `.before-ui-20261111-123859.bak`，数据库备份为桌面副本 `server/data/flowmask.before-ui-20261111-124152.db`。

## 已定决策

不要重新讨论这些。输入、检测选项、操作人或路由变化会使快照失效。原文对照默认收起，仅展示本次粘贴的输入。复制与导出复验快照，策略、会话或覆盖异常时锁定。结构化复制只取脱敏值，完整结构由后端导出。

## 下一步

- 原说明的 PNG 与 HTML 参照未随附件提供。若补齐，可按同一用例更新视觉对照。
- 服务端仅提供原文字符区间及已返回片段计数。解析器内部截断和脱敏后字符区间如需逐项标识，应先补 API 元数据。

## 关键文件

- `web/src/App.vue`、`web/src/style.css`、`web/src/views/DetectView.vue`
- `web/src/api.js`、`web/src/workspace.js`、`web/test/e2e/workspace.spec.js`

---

# FlowMask L2 / L3 交接 · 2026-11-10

## 当前状态

- 本地分支 `codex/l2-l3` 基于 `ae800c2`。L2 / L3 请求开关、推理异常降级、执行状态与结构化字段的 L2 证据保留已实现。
- Python 全套 727 通过、5 跳过，Node 测试 15 通过。Vite 构建 API 在仓库内临时目录构建成功。
- 使用本机 ONNX int8 分类模型和 CUDA 上的 `qwen2.5-1.5b-sens` 实测合成手机号。两层默认关闭时，显式启用得到 `l1>l2>l3`，执行覆盖为 `complete`，脱敏副本不含原手机号。
- 本地 `origin` 指向 `wukiQAQ/datausing`。GitHub 域名解析失败，未核实用户给出的 `lynn-0108-n/datausing` 与本地副本的关系及写权限。未推送、未创建 Issue 或 PR。

## 已定决策

不要重新讨论这些约定。省略 L2/L3 开关继承服务端默认值，显式布尔值覆盖默认值。风险等级与模型执行状态独立。保留 L2 的规则命中门槛、L3 的灰区门槛与默认升级上限。即时检测及快照预览按片段和文档返回执行状态，导出继续复用检测快照。

## 下一步

远端协作前先核对目标仓库与本地基线，再确认账号写权限或 fork 路径。后续可独立处理跨片段推理预算、模型就绪和真实/演示后端标识、任务级状态持久化。

## 关键文件

- 识别与 HTTP 契约：`server/app/identify/{pipeline,inference,l2,l3}.py`
- 开关和返回值：`server/app/api/{deps,routes_detect,routes_tasks,routes_traffic,schemas}.py`、`web/src/api.js`
- 用例：`server/tests/{test_api,test_pipeline,test_l2_service,test_l3_client}.py`、`web/test/api-snapshot.test.mjs`

---

# FlowMask 第一批核心安全修复交接 · 2026-11-05

## 当前状态

本轮用户收窄的四项核心范围已实现、验证、逐单元提交：结构化导出残留、未授权动态SAFE/L1降级、任务L1原值持久化、检测/策略预览/导出快照一致。不是完成全部32项，也不是原计划第一批所有扩展工作都完成。

- 分支：`codex/backend-p0`；基线`c7ba4c0`。当前改动全部留在该分支，未推送或发布。
- Python一体机：0.26.1本地exe已重新打包并通过6项实测，路径`server/dist_exe/FlowMask.exe`（54,034,236字节）；安装包已生成、载荷核验与实际exe前后端联调14项通过，已复制至`D:/31387/Desktop/FlowMask-Setup-0.26.1.exe`；未远程发布。Go生产版本与旧MeTD不变。
- 交付：`docs/第一批P0修复交付-2026-11-05.md`；执行记录：`docs/第一批安全修复记录.md`。
- 原始证据：`docs/后端业务全面审计-2026-11-05.md`；后续计划：`docs/superpowers/plans/2026-11-05-flowmask-backend.md`。

提交：`cf5a4ab`动态边界；`646de69`结构化回写；`80d75d5`持久化；`5df6aef`Go回压测试诊断与握手；`2d3d9c1`快照和复核安全补强。

## 已定决策 —— 不要重新讨论这些

1. 产品根目录是`D:/AAAdsh-allproject/flow work/datausing`，不是邻近旧版FlowMask。正式前端web/，主服务server/，桌面入口server/desktop.py；旧Go/MeTD能力保留。
2. 保留FastAPI/SQLite/Document/Segment与现有API，不替换框架、不引入分布式队列、不用Mock替代业务。按交付结论接续，不重做完整审计。
3. 风险等级与替换策略分离。持久化和未授权副本至少partial，高危block；无位置敏感证据整片段保护。合法动态原文访问保留。
4. 结构化回写使用类型化location，显示path无歧义。普通路径兼容，特殊JSON键为`$["a.b"]`，重名CSV列为`row1[0]`。正式前端索引已最小同步，未重做设计。
5. result_id是随机、会话隔离、短寿命内存句柄，固定文档/证据/规则修订/策略及密钥版本；导出不重跑识别。策略预览与导出复用相同替换值，加密也一致。
6. 缓存TTL300秒、32项、估算32MiB，每秒回收，应用退出清空。请求处理的临时内存另计。原文不为重试/恢复偷偷写盘，重启句柄失效。
7. 旧直接导出保留fresh检测语义；两阶段一致性要求传result_id。正式前端已接入，旧外部调用方需按交付说明迁移。源/选项/版本漂移409、跨会话403、过期410；安全错误带code/error_id，不带原异常正文。
8. 当前operator白名单是本机演示身份模型，不能当强身份认证；多角色主体和解密权限统一留第三批，不能声称本轮已实现企业隔离。
9. 已知历史`[低危]`副本查询/导出会遮蔽，但历史数据库和签名没有改写。旧磁盘可能仍含原值，未授权不做清理/迁移；其他历史元数据风险不能称已全面清洗。
10. Go回压已先诊断后修测试握手：不能把原60轮31失败归因于产品已接受项额外丢失；不改生产非阻塞语义、不增加sleep。证据在`docs/Go回压诊断-2026-11-05.md`。

## 已验证

- Python全套717 passed、5 skipped、1现有Starlette弃用警告；相对基线新增92项安全场景。
- Node14 passed；Vite生产构建成功。
- 真Edge+隔离FastAPI生产页面14 passed，包含规则停用后加密快照预览/下载逐字一致、八页、批量、审计、授权、流量示例、加密还原。仅原有loading用例注入一次422，其余业务走真实服务。
- Go test/build/vet退出码0；回压三种CPU×100轮=300次通过，race同样300次且无race报告。
- exe启动、内置页面/词表、动态保护、结构化导出、规则漂移与加密快照、任务持久化共6项通过；安装包编译与解包载荷已核验；真实模型权重/网卡持续采集/交互托盘/原生安装向导与卸载未验证。所有数据为合成样本，未读真实用户库。
- 受限Windows的Vite构建需要获准运行以解决Node realpath权限；后台测试服务须和浏览器在同一命令生命周期，单独启动会被环境清理。Go build模块stat缓存有权限警告但退出码0。

## 未完成与下一步

1. 先读本文件和交付报告，再按计划处理第二批规则版本/耗时预算、单机任务生命周期与调度失败隔离。
2. 明确排期仍开放的F01数字归一化误合并、F06完整PEM范围、F08引擎开关/执行状态；本轮只补直接导致输出泄露的交叠与无位置保护，不把这些项自动关闭。
3. F22流量URL/query元数据隐私仍开放，应单列P0优先排期；F23～F25/F31重组/截断/丢段/live完整性独立留第四批。正文遮蔽不代表流量路径已全部安全。
4. F02多来源分类/评分、F05全部SQL方言、F09完整自动格式警告、F27持久化命中详情仅部分覆盖，状态见计划表。
5. 不重启前端重设计；Vue/Element Plus、当前主题/Unicode半开区间/按需图表、动态权限由服务端决定等约定保留，历史细节见`docs/前端重设计.md`。

## 关键文件

- 安全边界：`server/app/security.py`、`api/routes_dynamic.py`、`tasks/service.py`、`tasks/export.py`
- 定位与重建：`server/app/parsers/{base,json_parser,csv_parser,sql_parser}.py`、`tasks/document_export.py`
- 快照：`server/app/identify/snapshots.py`、`api/routes_detect.py`、`api/schemas.py`、`main.py`
- 直接相关识别保护：`server/app/identify/{rules,types,pipeline,masking}.py`
- 测试：`server/tests/test_security_{boundaries,exports,persistence,snapshots}.py`、`web/test/api-snapshot.test.mjs`、`web/test/e2e/workspace.spec.js`、`internal/app/dlp_test.go`
- 前端最小接入：`web/src/api.js`、`web/src/views/DetectView.vue`、`web/src/workspace.js`

最新exe SHA256：`8806160CD7CC9D99C1F9EC3CC112EBF18E47AF8664D82AF2576184E5646DEDF5`。打包基于`e51afc1`，PyInstaller6.22.3，前端重新构建；详见交付文档第7节。

安装程序：55,224,391字节，SHA256 `A73130E9C041EECCF26923B52FFE9506C0CDC847D668603857FE68702257770D`。已对实际打包exe内嵌前端与后端运行Edge14项，48.2秒全通过；安装载荷exe与已验证exe的SHA256一致。详见交付文档第8节。
