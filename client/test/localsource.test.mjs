// 数据来源模块（client/src/localsource.js）的单元测试
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  MODE_STORE_KEY,
  SOURCE_MODES,
  checkStartLocalAnalysis,
  loadModePref,
  localBase,
  localDataDir,
  localEngineArgs,
  modeHint,
  modeLabel,
  modeSupport,
  normalizeMode,
  verifyLocalEngine,
} from "../src/localsource.js";

test("SOURCE_MODES 为远程/本机/离线三选一", () => {
  assert.deepEqual(SOURCE_MODES, ["remote", "local", "offline"]);
  assert.equal(MODE_STORE_KEY, "netmon.source.v1");
});

test("normalizeMode 容错非法值", () => {
  assert.equal(normalizeMode("remote"), "remote");
  assert.equal(normalizeMode("local"), "local");
  assert.equal(normalizeMode("offline"), "offline");
  // undefined / null / 空串 / 非法值 / 其它类型 一律回落到 remote
  assert.equal(normalizeMode(undefined), "remote");
  assert.equal(normalizeMode(null), "remote");
  assert.equal(normalizeMode(""), "remote");
  assert.equal(normalizeMode("REMOTE"), "remote");
  assert.equal(normalizeMode("远程"), "remote");
  assert.equal(normalizeMode(0), "remote");
  assert.equal(normalizeMode({ mode: "local" }), "remote");
});

test("modeLabel 输出中文来源名", () => {
  assert.equal(modeLabel("remote"), "远程 Linux");
  assert.equal(modeLabel("local"), "本机 Windows（实时抓包）");
  assert.equal(modeLabel("offline"), "离线 pcap 文件");
  // 非法值按 remote 处理
  assert.equal(modeLabel("bad"), "远程 Linux");
});

test("localBase 拼出本机引擎地址", () => {
  assert.equal(localBase("127.0.0.1:18080"), "http://127.0.0.1:18080");
  assert.equal(localBase(" 127.0.0.1:9527 "), "http://127.0.0.1:9527");
  // 没有 listen 时只返回协议前缀，由上层校验拦住
  assert.equal(localBase(""), "http://");
  assert.equal(localBase(undefined), "http://");
});

test("localDataDir 优先 LOCALAPPDATA", () => {
  const dir = localDataDir({ LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local", USERPROFILE: "C:\\Users\\me" });
  assert.equal(dir, "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data");
});

test("localDataDir 无 LOCALAPPDATA 时退回 USERPROFILE", () => {
  const dir = localDataDir({ USERPROFILE: "C:\\Users\\me" });
  assert.equal(dir, "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data");
});

test("localDataDir 两者都没有返回空串", () => {
  assert.equal(localDataDir({}), "");
  assert.equal(localDataDir(null), "");
  // 仅空白的变量视为缺失，避免拼出 "\MeTD\local-data" 这种相对路径
  assert.equal(localDataDir({ LOCALAPPDATA: "   ", USERPROFILE: "" }), "");
});

test("localDataDir 不产生重复分隔符", () => {
  assert.equal(
    localDataDir({ LOCALAPPDATA: "C:\\Users\\me\\AppData\\Local\\" }),
    "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
  );
  assert.equal(
    localDataDir({ LOCALAPPDATA: "C:/Users/me/AppData/Local/" }),
    "C:/Users/me/AppData/Local\\MeTD\\local-data",
  );
});

test("localEngineArgs remote 不启动本机进程", () => {
  assert.equal(localEngineArgs({ mode: "remote" }), null);
  // 缺 mode 时按 remote 处理
  assert.equal(localEngineArgs({}), null);
  assert.equal(localEngineArgs(undefined), null);
});

test("localEngineArgs offline 返回 netmon 参数数组", () => {
  const args = localEngineArgs({
    mode: "offline",
    pcapPath: "D:\\pcaps\\demo.pcap",
    dataDir: "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
    listen: "127.0.0.1:18080",
  });
  assert.deepEqual(args, [
    "-replay-file",
    "D:\\pcaps\\demo.pcap",
    "-data-dir",
    "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
    "-listen",
    "127.0.0.1:18080",
  ]);
});

test("localEngineArgs offline 缺 pcap 抛错", () => {
  assert.throws(
    () => localEngineArgs({ mode: "offline", pcapPath: "  ", dataDir: "C:\\d", listen: "127.0.0.1:1" }),
    /离线 pcap 模式必须选择 pcap 文件/,
  );
});

test("localEngineArgs offline 缺数据目录抛错", () => {
  assert.throws(
    () => localEngineArgs({ mode: "offline", pcapPath: "D:\\a.pcap", dataDir: "", listen: "127.0.0.1:1" }),
    /本机引擎缺少数据目录/,
  );
});

test("localEngineArgs offline 缺监听地址抛错", () => {
  assert.throws(
    () => localEngineArgs({ mode: "offline", pcapPath: "D:\\a.pcap", dataDir: "C:\\d", listen: "" }),
    /本机引擎缺少监听地址/,
  );
});

test("localEngineArgs local 返回实时抓包参数", () => {
  const base = {
    mode: "local",
    dataDir: "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
    listen: "127.0.0.1:18080",
  };
  // 留空 iface：由引擎自动选默认路由出口网卡
  assert.deepEqual(localEngineArgs(base), [
    "-source",
    "live",
    "-data-dir",
    "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
    "-listen",
    "127.0.0.1:18080",
  ]);
  // 指定 iface：透传本机 IPv4（Windows 上按 IP 绑定，不接受网卡名）
  assert.deepEqual(localEngineArgs({ ...base, iface: " 10.24.65.246 " }), [
    "-source",
    "live",
    "-iface",
    "10.24.65.246",
    "-data-dir",
    "C:\\Users\\me\\AppData\\Local\\MeTD\\local-data",
    "-listen",
    "127.0.0.1:18080",
  ]);
});

test("localEngineArgs local 缺数据目录/监听地址抛错", () => {
  assert.throws(() => localEngineArgs({ mode: "local", listen: "127.0.0.1:1" }), /缺少数据目录/);
  assert.throws(() => localEngineArgs({ mode: "local", dataDir: "C:\\d" }), /缺少监听地址/);
});

test("modeHint 逐来源给出说明", () => {
  assert.equal(modeHint("remote"), "");
  assert.match(modeHint("local"), /管理员权限/);
  assert.match(modeHint("local"), /127\.0\.0\.1/);
  assert.match(modeHint("offline"), /无需管理员权限/);
  assert.match(modeHint("offline"), /文件/);
});

test("modeSupport remote 恒可用", () => {
  assert.deepEqual(modeSupport("remote", {}, "linux"), { ok: true, hint: "" });
  assert.deepEqual(modeSupport("remote", null, undefined), { ok: true, hint: "" });
});

test("modeSupport offline 依赖采集端能力", () => {
  // 老采集端未声明 capture.offline，提示升级并带上服务端版本号
  const noCap = modeSupport("offline", { version: "0.22.0", features: ["capture.dump"] }, "linux");
  assert.equal(noCap.ok, false);
  assert.match(noCap.hint, /离线回放/);
  assert.match(noCap.hint, /0\.22\.0/);
  // 未上报版本时也有可读提示
  const noVer = modeSupport("offline", {}, "linux");
  assert.equal(noVer.ok, false);
  assert.match(noVer.hint, /未上报版本/);
  // 采集端 0.23.4 声明了能力，可用
  const ok = modeSupport("offline", { version: "0.23.4", features: ["capture.offline"] }, "linux");
  assert.deepEqual(ok, { ok: true, hint: "" });
  // 远程来源的能力与 engineOs 无关（本机引擎跑在 Windows，但 offline 不依赖它）
  assert.deepEqual(modeSupport("offline", { features: ["capture.offline"] }, "windows"), { ok: true, hint: "" });
});

test("modeSupport local 启动前一律放行（能力判定推后到连上之后）", () => {
  // 此刻本机引擎还没起来，now 是空的/上一次连接的残留：拿它拦会导致入口永远点不动
  assert.deepEqual(modeSupport("local", {}, undefined), { ok: true, hint: "" });
  assert.deepEqual(modeSupport("local", null, "linux"), { ok: true, hint: "" });
  assert.deepEqual(
    modeSupport("local", { version: "0.24.1", features: ["source.local", "capture.offline"] }, "windows"),
    { ok: true, hint: "" },
  );
});

test("verifyLocalEngine 连上后校验系统与权限能力", () => {
  // Windows + 声明了 source.local：可用
  assert.deepEqual(
    verifyLocalEngine({ os: "windows", features: ["capture.offline", "source.local"] }, "windows"),
    { ok: true, hint: "" },
  );
  // 未声明 source.local：多为未提权，提示必须能照着做
  const notAdmin = verifyLocalEngine({ os: "windows", features: ["capture.offline"] }, "windows");
  assert.equal(notAdmin.ok, false);
  assert.match(notAdmin.hint, /管理员/);
  // 中枢不是 Windows：直接说明，不误导用户去提权
  const notWin = verifyLocalEngine({ os: "linux", features: ["source.local"] }, "linux");
  assert.equal(notWin.ok, false);
  assert.match(notWin.hint, /不是 Windows/);
});

test("checkStartLocalAnalysis 按来源校验入参", () => {
  // 远程来源不需要启动本机引擎
  assert.equal(checkStartLocalAnalysis({ mode: "remote", pcapPath: "D:\\a.pcap" }).ok, false);
  assert.match(checkStartLocalAnalysis({ mode: "remote" }).reason, /远程 Linux/);
  // 本机实时抓包不需要任何本地入参（数据目录/监听地址由 Rust 侧补齐）
  assert.deepEqual(checkStartLocalAnalysis({ mode: "local" }), { ok: true, reason: "" });
  // 离线但没填 pcap
  assert.equal(checkStartLocalAnalysis({ mode: "offline", pcapPath: "   " }).ok, false);
  assert.match(checkStartLocalAnalysis({ mode: "offline" }).reason, /必须选择 pcap 文件/);
  // 离线 + 有 pcap：即便此刻还没连上任何采集端（now 里还没有 features）也要放行，
  // 否则第一次使用时会因为"未上报能力"而永远点不动（能力判定推后到连上之后）
  assert.deepEqual(checkStartLocalAnalysis({ mode: "offline", pcapPath: " D:\\a.pcap " }), { ok: true, reason: "" });
});

test("loadModePref 容错读取偏好", () => {
  assert.deepEqual(loadModePref(null), { mode: "remote", pcapPath: "", dataDir: "" });
  assert.deepEqual(loadModePref(undefined), { mode: "remote", pcapPath: "", dataDir: "" });
  assert.deepEqual(loadModePref("offline"), { mode: "remote", pcapPath: "", dataDir: "" });
  assert.deepEqual(loadModePref({ mode: "offline" }), { mode: "offline", pcapPath: "", dataDir: "" });
  assert.deepEqual(
    loadModePref({ mode: "offline", pcapPath: "D:\\a.pcap", dataDir: "C:\\d" }),
    { mode: "offline", pcapPath: "D:\\a.pcap", dataDir: "C:\\d" },
  );
  // 非法 mode 回落 remote，字段一律转成字符串
  assert.deepEqual(loadModePref({ mode: "nope", pcapPath: 7 }), { mode: "remote", pcapPath: "7", dataDir: "" });
});
