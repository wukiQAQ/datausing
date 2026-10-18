// 数据来源（三选一）纯逻辑模块：
//   remote  —— 连接远程 Linux 上的 netmon（原有能力，默认）
//   local   —— 在本机 Windows 上实时抓包（本版本尚未实现，只保留入口与提示）
//   offline —— 用本机 netmon.exe 回放一个 pcap 文件（-replay-file），界面里的数字来自文件而非当前网络
// 这里不引入 DOM / Tauri，全部是可用 node:test 直接验证的纯函数。
import { hasFeature, serverVersion } from "./capability.js";

// 三种数据来源的枚举，顺序即界面上的展示顺序
export const SOURCE_MODES = ["remote", "local", "offline"];

// 数据来源偏好的持久化键（与 profiles / settings 的 v1 键风格一致）
export const MODE_STORE_KEY = "netmon.source.v1";

// 采集端提供「离线回放」能力的最低版本，仅用于提示文案
const OFFLINE_SINCE = "V0.23.0+";

// 容错归一化：合法枚举原样返回，其余（undefined / null / 空串 / 非法值）一律回落到 remote
export function normalizeMode(mode) {
  return SOURCE_MODES.includes(mode) ? mode : "remote";
}

// 界面上的来源名称
export function modeLabel(mode) {
  switch (normalizeMode(mode)) {
    case "local":
      return "本机 Windows（实时抓包）";
    case "offline":
      return "离线 pcap 文件";
    default:
      return "远程 Linux";
  }
}

// 本机引擎的 HTTP 地址：listen 形如 127.0.0.1:18080
export function localBase(listen) {
  const s = typeof listen === "string" ? listen.trim() : "";
  return "http://" + s;
}

// 目录拼接前去掉结尾的路径分隔符，避免出现 `C:\a\\MeTD` 这种重复分隔符
function trimTrailingSep(path) {
  return String(path).trim().replace(/[\\/]+$/, "");
}

// 本机引擎的数据目录：优先 %LOCALAPPDATA%\MeTD\local-data，
// 退回 %USERPROFILE%\AppData\Local\MeTD\local-data，两者都没有时返回空串
// （env 由调用方注入，方便测试，Rust 侧也用同一套规则拼路径）。
export function localDataDir(env) {
  const e = env && typeof env === "object" ? env : {};
  const localAppData = typeof e.LOCALAPPDATA === "string" ? e.LOCALAPPDATA.trim() : "";
  if (localAppData) return trimTrailingSep(localAppData) + "\\MeTD\\local-data";
  const userProfile = typeof e.USERPROFILE === "string" ? e.USERPROFILE.trim() : "";
  if (userProfile) return trimTrailingSep(userProfile) + "\\AppData\\Local\\MeTD\\local-data";
  return "";
}

// 组装启动本机 netmon.exe 的参数（与 Rust 侧 sidecar 逻辑保持一致）：
//   remote  → null（不启动本机进程）
//   offline → ["-replay-file", pcap, "-data-dir", dir, "-listen", listen]
//   local   → ["-source", "live", ("-iface", ip)?, "-data-dir", dir, "-listen", listen]
// 缺参数时在这里就报出可读的中文错误，不把问题留到进程启动之后。
export function localEngineArgs(opts) {
  const o = opts && typeof opts === "object" ? opts : {};
  const mode = normalizeMode(o.mode);
  if (mode === "remote") return null;
  if (mode === "local") {
    // 本机实时抓包：采集端 0.24.1 起支持 -source / -iface，数据源由命令行直接指定，
    // 不必先落一份临时配置文件。iface 是可选的「本机 IPv4」，留空由引擎自动选默认路由出口
    // （Windows 上按 IP 绑定，写网卡名会被引擎当成"自动选择"，所以这里不做网卡名透传）。
    const dataDir = typeof o.dataDir === "string" ? o.dataDir.trim() : "";
    if (!dataDir) throw new Error("本机引擎缺少数据目录");
    const listen = typeof o.listen === "string" ? o.listen.trim() : "";
    if (!listen) throw new Error("本机引擎缺少监听地址");
    const args = ["-source", "live"];
    const iface = typeof o.iface === "string" ? o.iface.trim() : "";
    if (iface) args.push("-iface", iface);
    args.push("-data-dir", dataDir, "-listen", listen);
    return args;
  }
  const pcapPath = typeof o.pcapPath === "string" ? o.pcapPath.trim() : "";
  if (!pcapPath) throw new Error("离线 pcap 模式必须选择 pcap 文件");
  const dataDir = typeof o.dataDir === "string" ? o.dataDir.trim() : "";
  if (!dataDir) throw new Error("本机引擎缺少数据目录");
  const listen = typeof o.listen === "string" ? o.listen.trim() : "";
  if (!listen) throw new Error("本机引擎缺少监听地址");
  return ["-replay-file", pcapPath, "-data-dir", dataDir, "-listen", listen];
}

// 「启动本机引擎」按钮的前置校验（纯函数，便于测试）：
// 只校验启动所必需的本地入参——本机引擎的能力（有没有管理员权限、有没有声明 source.local）
// 要等它起来、连上之后才知道；启动前拿采集端上报的 features 去拦，会因为首次使用时
// `now` 还是空的而"永远点不动"。
//   offline → 必须填了 pcap 路径
//   local   → 不需要额外入参（数据目录与监听地址由 Rust 侧补齐）
export function checkStartLocalAnalysis(opts) {
  const o = opts && typeof opts === "object" ? opts : {};
  const mode = normalizeMode(o.mode);
  if (mode === "local") return { ok: true, reason: "" };
  if (mode !== "offline") {
    return { ok: false, reason: "远程 Linux 不需要启动本机引擎，直接连服务器地址即可" };
  }
  const pcapPath = typeof o.pcapPath === "string" ? o.pcapPath.trim() : "";
  if (!pcapPath) return { ok: false, reason: "离线 pcap 模式必须选择 pcap 文件" };
  return { ok: true, reason: "" };
}

// 每种来源的补充说明（界面直接显示，remote 没有额外说明）
export function modeHint(mode) {
  switch (normalizeMode(mode)) {
    case "local":
return "需要管理员权限（引擎以 -source live 抓本机网卡，未提权会失败并给出提示）；只能看到本机网卡上的 IP 流量——抓不到 127.0.0.1 回环（走本地代理的流量看不见）、抓不到 ARP";
    case "offline":
      return "无需管理员权限，只分析所选 pcap 文件；界面上的数字来自文件，而不是当前网络";
    default:
      return "";
  }
}

// 判断某种来源在当前环境下是否可用，返回 {ok, hint}：
//   remote  —— 恒可用（沿用原有连接流程）
//   offline —— 需要采集端声明 capture.offline（采集端 0.23.4 起提供）
//   local   —— **启动前一律放行**（原因见函数体注释），校验交给 verifyLocalEngine
export function modeSupport(mode, now, engineOs) {
  const m = normalizeMode(mode);
  if (m === "remote") return { ok: true, hint: "" };
  if (m === "offline") {
    if (hasFeature(now, "capture.offline")) return { ok: true, hint: "" };
    const v = serverVersion(now);
    return {
      ok: false,
      hint: v
        ? `服务端为 V${v}，不支持离线回放（需 ${OFFLINE_SINCE}），请更新 Linux 端 netmon`
        : `服务端未上报版本/能力，离线回放不可用（需 ${OFFLINE_SINCE}），请更新 Linux 端 netmon`,
    };
  }
  // 本机实时抓包启动前一律放行：此刻本机引擎还没起来，`now` 是上一次连接的残留（甚至为空），
  // 拿它判断必然误判——旧实现正是如此，导致这个入口永远点不动。真正的校验放在
  // 引擎起来、连上之后，见 verifyLocalEngine。
  return { ok: true, hint: "" };
}

// 本机实时抓包引擎起来、客户端连上之后的**最终校验**：
//   os       必须是 windows（原始套接字抓包是 Windows 实现）
//   features 必须含 source.local（引擎启动时探测到管理员权限才会声明）
// 返回 {ok, hint}；不通过时 hint 是给用户的可操作提示。
export function verifyLocalEngine(now, engineOs) {
  const os = String(engineOs || "").trim().toLowerCase();
  if (os !== "windows") {
    return {
      ok: false,
      hint: "本机引擎上报的系统是「" + (os || "未知") + "」，不是 Windows：实时抓包不可用",
    };
  }
  if (!hasFeature(now, "source.local")) {
    return {
      ok: false,
      hint: "本机引擎未声明 source.local —— 通常是没有管理员权限。请关闭 MeTD，右键「以管理员身份运行」后重试",
    };
  }
  return { ok: true, hint: "" };
}

// 字符串字段容错：只接受字符串，其它一律当成空串
function asString(v) {
  return v === undefined || v === null ? "" : String(v);
}

// 读取数据来源偏好（raw 为已解析的对象或 null），任何脏数据都回落到 remote
export function loadModePref(raw) {
  const r = raw && typeof raw === "object" ? raw : {};
  return {
    mode: normalizeMode(r.mode),
    pcapPath: asString(r.pcapPath),
    dataDir: asString(r.dataDir),
  };
}
