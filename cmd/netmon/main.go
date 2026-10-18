// netmon 是 Linux 网络流量监控系统的入口程序。
// 职责：加载配置 → 选择数据源 → 装配各层 → 启动 HTTP 服务与采集循环。
package main

import (
	"context"
	"errors"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/wukiQAQ/datausing/internal/action"
	"github.com/wukiQAQ/datausing/internal/aggregator"
	"github.com/wukiQAQ/datausing/internal/alert"
	"github.com/wukiQAQ/datausing/internal/api"
	"github.com/wukiQAQ/datausing/internal/app"
	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/config"
	"github.com/wukiQAQ/datausing/internal/filter"
	"github.com/wukiQAQ/datausing/internal/flow"
	"github.com/wukiQAQ/datausing/internal/plugin"
	"github.com/wukiQAQ/datausing/internal/storage"
	"github.com/wukiQAQ/datausing/internal/updater"
	"github.com/wukiQAQ/datausing/internal/webui"
)

func main() {
	configPath := flag.String("config", "config.toml", "配置文件路径")
	replayFile := flag.String("replay-file", "", "覆盖 capture.replay_file：本机回放的 pcap 文件路径")
	offline := flag.Bool("offline", false, "覆盖 core.offline：离线分析模式（报文时间戳驱动聚合，回放结束不退出）")
	dataDir := flag.String("data-dir", "", "覆盖 storage.data_dir：本机模式的数据目录（sqlite_path 一并挪入该目录）")
	listen := flag.String("listen", "", "覆盖 api.listen：本机模式建议 127.0.0.1:8080，避免暴露到局域网")
	source := flag.String("source", "", "覆盖 core.source：数据源 live | replay | synthetic（本机实时抓包用 live）")
	iface := flag.String("iface", "", "覆盖 capture.iface：Linux 写网卡名；Windows 实时抓包写本机 IPv4（留空自动选默认路由出口）")
	flag.Parse()

	cfg, err := config.Load(*configPath)
	if errors.Is(err, os.ErrNotExist) {
		cfg = config.Default()
		log.Printf("[config] 未找到 %s，使用默认配置（synthetic 数据源）", *configPath)
	} else if err != nil {
		log.Fatalf("[config] 配置加载失败: %v", err)
	}
	if err := applyCLIOverrides(cfg, cliOverrides{
		replayFile: *replayFile,
		offline:    *offline,
		dataDir:    *dataDir,
		listen:     *listen,
		source:     *source,
		iface:      *iface,
	}); err != nil {
		log.Fatalf("[config] 命令行参数不可用: %v", err)
	}
	log.Printf("[config] machine=%s source=%s iface=%s tick=%v offline=%v listen=%s",
		cfg.MachineID, cfg.Source, cfg.Iface, cfg.Tick, cfg.Offline, cfg.Listen)
	registerPlatformCapabilities()
	// H2：token 为空且监听非回环时只提醒、不改默认值（改默认会让现有部署与文档失效）。
	if w := api.AuthWarning(cfg.APIToken, cfg.Listen); w != "" {
		log.Printf("[api] 【安全提醒】%s", w)
	}

	src, err := buildSource(cfg)
	if err != nil {
		log.Fatalf("[capture] 数据源创建失败: %v", err)
	}
	// 采集过滤：表达式为空表示不过滤。
	// 表达式非法时直接退出，避免"以为过滤生效了、其实一个都没过滤"的静默错误。
	flt, err := filter.Parse(cfg.Filter)
	if err != nil {
		log.Fatalf("[filter] capture.filter 不可用: %v", err)
	}
	var filtered *capture.FilteredSource
	if flt != nil {
		filtered = capture.NewFilteredSource(src, flt)
		src = filtered
		log.Printf("[filter] 已启用采集过滤: %s（不匹配的帧在解析前丢弃）", flt)
	}
	defer src.Close()

	table := flow.NewTable(30*time.Second, 5*time.Minute, nil)
	agg := aggregator.New(3600)
	var store storage.Backend
	if cfg.Storage == "file" {
		store, err = storage.OpenFileStore(cfg.DataDir, cfg.Retention)
	} else {
		store, err = storage.OpenSQLiteStore(cfg.SQLitePath, cfg.Retention)
	}
	if err != nil {
		log.Fatalf("[storage] 存储初始化失败: %v", err)
	}
	defer store.Close()

	eng := buildAlertEngine(cfg)
	// 按需抓包：导出 pcap 供 Wireshark 分析（文件放在数据目录的 dumps 子目录）
	dumper := capture.NewDumper(filepath.Join(cfg.DataDir, "dumps"))
	a := app.New(cfg, src, table, agg, store)
	a.Alerts = eng
	a.Dumper = dumper
	srv := api.New(cfg, agg, table, store, webui.FS)
	// 查询窗口跟随"数据时间"：离线回放的历史 pcap 数据时间远早于墙钟，
	// 不注入的话 /topn 与 /traffic/history 会恒为空（见 api.SetDataClock）。
	srv.SetDataClock(a.DataNow)
	srv.SetAlerts(eng)
	srv.SetDumper(dumper)
	// 白名单运维动作：命令预览 + 参数校验 + 审计
	srv.SetActions(action.NewExecutor())
	// 过滤信息上报：客户端与网页可看到"当前过滤了什么、已过滤多少帧"
	if filtered != nil {
		srv.SetFilter(flt.String(), filtered.Filtered, filtered.KernelAttached())
	}
	// 界面插件框架：读取插件目录里的声明式清单（纯 JSON，不含可执行代码）
	specs, pluginWarnings, err := plugin.Load(cfg.Plugins.Dir)
	if err != nil {
		log.Printf("[plugin] 插件目录读取失败（已跳过）: %v", err)
	}
	for _, w := range pluginWarnings {
		log.Printf("[plugin] %s", w)
	}
	if len(specs) > 0 {
		log.Printf("[plugin] 已加载 %d 个界面插件", len(specs))
	}
	srv.SetPlugins(specs, pluginWarnings, cfg.Plugins.Dir != "")
	// 安全版自升级（默认关闭；开启后仅允许白名单地址 + SHA256 校验通过的新版本）
	up := updater.New(updater.Config{
		Enabled:         cfg.Update.Enabled,
		AllowedPrefixes: cfg.Update.AllowedPrefixes,
		MaxBytes:        int64(cfg.Update.MaxMB) << 20,
		Service:         cfg.Update.Service,
	})
	srv.SetUpdater(up)
	if cfg.Update.Enabled {
		log.Printf("[update] 自升级已启用（白名单 %d 个前缀，上限 %d MB，服务 %s）",
			len(cfg.Update.AllowedPrefixes), cfg.Update.MaxMB, cfg.Update.Service)
	}
	handler := srv.Handler()
	httpSrv := &http.Server{Addr: cfg.Listen, Handler: handler}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	serveErr := make(chan error, 1)
	go func() {
		log.Printf("[api] HTTP 服务启动: %s", api.ListenURL(cfg.Listen))
		serveErr <- httpSrv.ListenAndServe()
	}()

	runErr := make(chan error, 1)
	go func() { runErr <- a.Run(ctx) }()

	select {
	case err := <-serveErr:
		if err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("[api] HTTP 服务异常退出: %v", err)
		}
	case err := <-runErr:
		if err != nil {
			log.Printf("[app] 采集循环退出: %v", err)
		}
	case <-ctx.Done():
		log.Printf("[main] 收到退出信号，正在关闭…")
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_ = httpSrv.Shutdown(shutdownCtx)
	log.Printf("[main] 已退出")
}

// cliOverrides 汇总需要用命令行覆盖的配置项，避免参数列表越加越长。
type cliOverrides struct {
	replayFile string
	offline    bool
	dataDir    string
	listen     string
	source     string
	iface      string
}

// applyCLIOverrides 用命令行参数覆盖配置文件中的对应项，并拒绝互相矛盾的组合
// （宁可启动即报错，也不要其中一个参数被悄悄忽略）：客户端与本机脚本都要能直接
// 拉起一个数据源，而不必先落一份临时配置文件。
//
// 返回错误表示参数组合不可用，调用方必须按致命错误处理。
//
// 注意：给出 -replay-file 时必须同时把数据源切到 replay。
// 早期实现只覆盖 capture.replay_file，若配置里仍是默认的 source="synthetic"，
// 程序会去造包空跑、把 -replay-file 完全忽略，而且不报任何错——用户只会看到
// "跑起来了但数据不是我的 pcap"，极难自查。
func applyCLIOverrides(cfg *config.Config, o cliOverrides) error {
	if o.replayFile != "" {
		// 给出 -replay-file 时必须把数据源切到 replay；显式 -source 与之矛盾就直接报错。
		if o.source != "" && o.source != "replay" {
			return errors.New("-replay-file 与 -source " + o.source + " 冲突：前者要求数据源为 replay")
		}
		cfg.ReplayFile = o.replayFile
		cfg.Source = "replay"
	}
	if o.source != "" {
		cfg.Source = o.source
	}
	if o.iface != "" {
		cfg.Iface = o.iface
	}
	// 选了 replay 却没给文件时立刻报错：否则会走到"打开空路径"这种没头没尾的失败。
	if cfg.Source == "replay" && cfg.ReplayFile == "" {
		return errors.New("source=replay 但没有 pcap 文件：请给出 -replay-file，或在配置里设置 capture.replay_file")
	}
	if o.offline {
		cfg.Offline = true
	}
	// -data-dir：本机模式要求所有落盘文件都待在同一个可清理目录里。
	// 只改 data_dir 不够——默认 sqlite_path 是相对路径 data/netmon.db，
	// 会随进程工作目录落到别处，用户卸载后留下残留文件。因此一并挪进新目录。
	if o.dataDir != "" {
		cfg.DataDir = o.dataDir
		if cfg.Storage == "" || cfg.Storage == "sqlite" {
			cfg.SQLitePath = filepath.Join(o.dataDir, "netmon.db")
		}
	}
	if o.listen != "" {
		cfg.Listen = o.listen
	}
	return nil
}

// buildAlertEngine 按配置构建告警引擎；未启用或无有效阈值规则时返回 nil。
func buildAlertEngine(cfg *config.Config) *alert.Engine {
	if !cfg.Alert.Enabled {
		return nil
	}
	rules := []alert.Rule{}

	if cfg.Alert.BpsThreshold > 0 {
		rules = append(rules, alert.Rule{ID: "bps-high", Series: "traffic.bps", Threshold: cfg.Alert.BpsThreshold, For: time.Duration(cfg.Alert.BpsForSecs) * time.Second})
	}
	if cfg.Alert.PpsThreshold > 0 {
		rules = append(rules, alert.Rule{ID: "pps-high", Series: "traffic.pps", Threshold: cfg.Alert.PpsThreshold, For: time.Duration(cfg.Alert.PpsForSecs) * time.Second})
	}
	if cfg.Alert.ConnsThreshold > 0 {
		rules = append(rules, alert.Rule{ID: "conns-high", Series: "traffic.conns", Threshold: cfg.Alert.ConnsThreshold, For: time.Duration(cfg.Alert.ConnsForSecs) * time.Second})
	}
	if cfg.Alert.DropsThreshold > 0 {
		rules = append(rules, alert.Rule{ID: "drops-high", Series: "traffic.drops", Threshold: cfg.Alert.DropsThreshold, For: time.Duration(cfg.Alert.DropsForSecs) * time.Second})
	}
	if len(rules) == 0 {
		log.Printf("[alert] 已启用但未配置有效阈值规则，跳过")
		return nil
	}
	log.Printf("[alert] 启用 %d 条规则（webhook=%s）", len(rules), cfg.Alert.Webhook)
	return alert.New(rules, cfg.Alert.Webhook)
}

// buildSource 按配置创建数据源；live 模式在 Linux（AF_PACKET）与 Windows（原始套接字 + SIO_RCVALL）上可用。
func buildSource(cfg *config.Config) (capture.Source, error) {
	switch cfg.Source {
	case "synthetic":
		return capture.NewSynthetic(cfg.Pps, cfg.Iface), nil
	case "replay":
		return capture.OpenPcapFile(cfg.ReplayFile)
	case "live":
		// 平台判断交给 capture 层：Linux 走 AF_PACKET，Windows 走原始套接字 + SIO_RCVALL，
		// 其余平台由 live_other.go 返回明确错误。这里不再用 runtime.GOOS 拦一道，
		// 否则新增平台支持时容易出现「实现有了但入口仍拒绝」的分叉。
		return capture.NewLiveWithReadersPinned(cfg.Iface, cfg.Readers, cfg.CPUPin)
	default:
		return nil, errors.New("未知数据源: " + cfg.Source + "（可选 synthetic/replay/live）")
	}
}
