# 前端验证

`npm test` 运行 Unicode 高亮、HTML 转义、结构化路径、计数口径和错误脱敏的 Node 单元测试。项目为 JavaScript，无独立 TypeScript 检查；`npm run build` 包含 Vue 模板编译。

浏览器测试使用系统 Edge（`FLOWMASK_BROWSER_CHANNEL` 可切换），依赖真实 FastAPI，所有数据必须指向隔离目录。不要指向生产服务。

PowerShell，第一个终端（从项目 `datausing/` 启动）：

```powershell
$env:FLOWMASK_DATA_DIR = "$PWD/web/.qa/runtime"
$env:FLOWMASK_DB_PATH = "$PWD/web/.qa/runtime/flowmask.db"
$env:FLOWMASK_REVEAL_OPERATORS = 'auditor-qa'
$env:FLOWMASK_ENABLE_L2 = '0'
$env:FLOWMASK_ENABLE_L3 = '0'
$env:FLOWMASK_ENABLE_NER = '0'
python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8090 --no-access-log
```

第二个终端：

```powershell
cd web
npm run dev -- --host 127.0.0.1
```

第三个终端：

```powershell
cd web
npm run test:e2e
```

也可以 `npm run build` 后，设置 `$env:FLOWMASK_TEST_URL='http://127.0.0.1:8090/app/'` 验证正式产物，此时不需要 Vite 开发服务器。

受限 Windows 环境若 esbuild/Playwright 无权写系统 TEMP：先创建 `web/.qa/temp`，将当前命令进程的 `TEMP`、`TMP` 指向其绝对路径。此配置只用于验证，不修改系统环境。

测试中的 `auditor-qa` 仅是隔离服务的授权夹具，不得添加到实际部署白名单。普通操作人请求原文必须被拒绝。测试使用合成示例、临时规则和临时任务；异常测试单独注入 422 响应验证加载与错误出口，其余业务链路使用真实服务。`.qa/` 是忽略的本机输出目录。
