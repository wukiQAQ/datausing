package main

import (
	"path/filepath"
	"testing"

	"github.com/wukiQAQ/datausing/internal/config"
)

// TestApplyCLIOverrides 校验命令行覆盖语义：
//   - -replay-file 非空时覆盖 capture.replay_file 并把数据源切到 replay（否则会造包空跑）；
//   - -offline 为真时开启离线模式，未给出（false）时不得把配置文件里已开启的离线模式关掉。
func TestApplyCLIOverrides(t *testing.T) {
	cfg := config.Default()
	if cfg.Source != "synthetic" {
		t.Fatalf("前提不成立：Default() 的数据源应为 synthetic，实际 %q", cfg.Source)
	}
	applyCLIOverrides(cfg, cliOverrides{})
	if cfg.ReplayFile != "" || cfg.Offline || cfg.Source != "synthetic" {
		t.Fatalf("未给参数时不应改变默认配置: replay=%q offline=%v source=%q",
			cfg.ReplayFile, cfg.Offline, cfg.Source)
	}

	applyCLIOverrides(cfg, cliOverrides{replayFile: "dump.pcap", offline: true})
	if cfg.ReplayFile != "dump.pcap" {
		t.Fatalf("-replay-file 未覆盖: %q", cfg.ReplayFile)
	}
	if cfg.Source != "replay" {
		t.Fatalf("-replay-file 必须同时把数据源切到 replay，实际 %q（否则会静默造包空跑）", cfg.Source)
	}
	if !cfg.Offline {
		t.Fatal("-offline 未开启离线模式")
	}

	// 配置文件里已开启离线，命令行未显式给出时保持开启
	cfg2 := config.Default()
	cfg2.Offline = true
	applyCLIOverrides(cfg2, cliOverrides{})
	if !cfg2.Offline {
		t.Error("命令行未给出 -offline 时不应关闭配置里的离线模式")
	}
	if cfg2.Source != "synthetic" {
		t.Errorf("未给 -replay-file 时不应改数据源: %q", cfg2.Source)
	}
}

// TestApplyCLIOverridesDataDir 覆盖 -data-dir：
// 数据目录与 sqlite 库必须一起挪动，否则本机模式的数据库会落在进程工作目录里，
// 客户端"清理本机数据"时会留下一份谁也找不到的残留库。
func TestApplyCLIOverridesDataDir(t *testing.T) {
	cfg := config.Default()
	dir := filepath.Join(t.TempDir(), "local-data")
	applyCLIOverrides(cfg, cliOverrides{dataDir: dir})
	if cfg.DataDir != dir {
		t.Fatalf("data_dir 未覆盖: %q", cfg.DataDir)
	}
	if want := filepath.Join(dir, "netmon.db"); cfg.SQLitePath != want {
		t.Fatalf("sqlite_path 必须挪进数据目录: got %q want %q", cfg.SQLitePath, want)
	}

	// 文件存储不使用 sqlite_path，不得被改写
	fileCfg := config.Default()
	fileCfg.Storage = "file"
	applyCLIOverrides(fileCfg, cliOverrides{dataDir: dir})
	if fileCfg.SQLitePath != "data/netmon.db" {
		t.Errorf("storage=file 时不应改 sqlite_path: %q", fileCfg.SQLitePath)
	}
	if fileCfg.DataDir != dir {
		t.Errorf("storage=file 时 data_dir 仍应被覆盖: %q", fileCfg.DataDir)
	}
}

// TestApplyCLIOverridesListen 覆盖 -listen：本机模式只监听回环地址。
func TestApplyCLIOverridesListen(t *testing.T) {
	cfg := config.Default()
	applyCLIOverrides(cfg, cliOverrides{listen: "127.0.0.1:18080"})
	if cfg.Listen != "127.0.0.1:18080" {
		t.Fatalf("-listen 未覆盖: %q", cfg.Listen)
	}

	cfg2 := config.Default()
	applyCLIOverrides(cfg2, cliOverrides{})
	if cfg2.Listen != ":8080" {
		t.Errorf("未给 -listen 时不应改监听地址: %q", cfg2.Listen)
	}
}

// TestApplyCLIOverridesSourceAndIface 覆盖 -source / -iface：
// 本机实时抓包要能只靠命令行选数据源与网卡，不必先落一份临时配置文件
// （仓库脚本 deploy/windows/start-local-analysis.ps1 -Live 与客户端 sidecar 都依赖这一点）。
func TestApplyCLIOverridesSourceAndIface(t *testing.T) {
	cfg := config.Default()
	if err := applyCLIOverrides(cfg, cliOverrides{source: "live", iface: "10.24.68.159"}); err != nil {
		t.Fatalf("覆盖失败: %v", err)
	}
	if cfg.Source != "live" {
		t.Errorf("source 未覆盖: %q", cfg.Source)
	}
	if cfg.Iface != "10.24.68.159" {
		t.Errorf("iface 未覆盖: %q", cfg.Iface)
	}

	// 未给出的项不得被改动
	cfg2 := config.Default()
	beforeIface := cfg2.Iface
	if err := applyCLIOverrides(cfg2, cliOverrides{}); err != nil {
		t.Fatalf("空覆盖不应报错: %v", err)
	}
	if cfg2.Source != "synthetic" {
		t.Errorf("空覆盖不应改动 source: %q", cfg2.Source)
	}
	if cfg2.Iface != beforeIface {
		t.Errorf("空覆盖不应改动 iface: got %q want %q", cfg2.Iface, beforeIface)
	}
}

// TestApplyCLIOverridesRejectsConflicts 校验矛盾组合必须报错，
// 而不是让其中一个参数被悄悄忽略（静默取舍正是最难自查的那类问题）。
func TestApplyCLIOverridesRejectsConflicts(t *testing.T) {
	cfg := config.Default()
	if err := applyCLIOverrides(cfg, cliOverrides{replayFile: "a.pcap", source: "live"}); err == nil {
		t.Error("-replay-file 与 -source live 同时给出应报错")
	}

	cfg2 := config.Default()
	if err := applyCLIOverrides(cfg2, cliOverrides{source: "replay"}); err == nil {
		t.Error("source=replay 却没有任何 pcap 文件应报错")
	}

	// 一致的组合不应报错
	cfg3 := config.Default()
	if err := applyCLIOverrides(cfg3, cliOverrides{replayFile: "a.pcap", source: "replay"}); err != nil {
		t.Errorf("一致的组合不应报错: %v", err)
	}
	if cfg3.Source != "replay" || cfg3.ReplayFile != "a.pcap" {
		t.Errorf("结果不符合预期: source=%q replay=%q", cfg3.Source, cfg3.ReplayFile)
	}
}
