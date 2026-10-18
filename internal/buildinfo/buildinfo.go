// Package buildinfo 提供程序版本与能力清单。
// 客户端连接后会读取 /api/v1/traffic/now 中的 version / features，
// 据此判断服务端是否支持某项功能（例如抓包导出），并在不支持时给出明确提示，
// 避免出现"点了按钮只报 404"的困惑。
package buildinfo

// Version 是采集端版本号。采集端与客户端各自独立编号（见 AGENTS.md「版本号规则」），
// 两个号段互不相干，不要把这里与 client/ 的版本号对齐。
const Version = "0.24.1"

// Features 列出该版本对外提供的可选能力；新增能力时在此追加。
//
// capture.offline 表示"可离线回放 pcap"（config 的 source=replay + core.offline，
// 或命令行 -replay-file/-offline）：客户端在本机模式下用它判断
// "自己拉起的这个本机引擎能否直接分析本地抓包文件"。
//
// source.local 表示"可在本机实时抓包（不需要远程 Linux）"，它同时依赖平台与权限：
// Windows 构建 + 进程具备管理员权限。因此它不在下面的静态清单里，
// 而由入口在启动阶段探测通过后调用 Enable 追加（见 cmd/netmon/capability_windows.go），
// 否则客户端会展示一个点了必然失败的入口（双模式设计文档 §4.3）。
var Features = []string{"history", "sessions", "alerts", "capture.dump", "actions.run", "files", "stream", "topn", "system.upgrade", "capture.multiqueue", "capture.filter", "plugins", "capture.cpupin", "capture.offline"}

// Enable 追加一项运行时探测出的能力（幂等：重复调用不会产生重复项）。
// 必须在 HTTP 服务开始对外提供接口之前调用：/api/v1/traffic/now 与 /metrics
// 每次请求都直接读取 Features，调用时机决定客户端看到的能力清单。
func Enable(name string) {
	if name == "" {
		return
	}
	for _, f := range Features {
		if f == name {
			return
		}
	}
	Features = append(Features, name)
}
