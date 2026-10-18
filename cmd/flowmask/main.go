// Package main 是 FlowMask 的内容级敏感数据检测命令行工具。
//
// 它把"采集 → 载荷抽取 → 规则识别 → 评分分级 → 脱敏预览"这条链路串起来：
// 读取一个 pcap，逐帧抽取传输层载荷，交给 internal/dlp 做 L1 规则识别与
// 敏感度分级，最后按级别输出 findings 报表（默认表格，-json 可产出给前端/
// 后续接在线推理服务的结构化数据）。
//
// 设计上刻意不依赖 netmon 主程序：旁路分析只需要 pcap 与识别库，
// 这样采集端可以照常跑实时抓包，DLP 侧单独做离线/批量分析。
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/dlp"
	"github.com/wukiQAQ/datausing/internal/parser"
	"github.com/wukiQAQ/datausing/internal/storage"
)

// finding 是一条内容级检测结果：一帧含载荷报文对应一条。
// 字段名即 JSON 契约里对外的名字，后续 /api/v1/dlp/findings 复用同一套字段。
type finding struct {
	Ts     time.Time `json:"ts"`
	Src    string    `json:"src"`
	Dst    string    `json:"dst"`
	Proto  string    `json:"proto"`
	Level  string    `json:"level"`
	Score  float64   `json:"score"`
	Types  []string  `json:"types"`
	Hits   int       `json:"hits"`
	Masked string    `json:"masked"`
	Direct bool      `json:"direct,omitempty"`
	// PayloadBytes 是这帧传输层载荷的字节数：既是成本指标，也说明 L1 只看正文
	PayloadBytes int `json:"payload_bytes,omitempty"`
}

// stats 汇总一次检测的规模与分级分布，末尾打印。
type stats struct {
	Frames     int            `json:"frames"`      // 读到的帧数
	WithBody   int            `json:"with_body"`   // 抽到非空载荷的帧数
	Findings   int            `json:"findings"`    // 命中敏感数据的条数
	ByLevel    map[string]int `json:"by_level"`    // 各等级条数
	DurationMs int64          `json:"duration_ms"` // 耗时，用于看 L1 吞吐
}

// protoName 把 parser 的协议号转成展示名。
func protoName(p uint8) string {
	switch p {
	case parser.ProtoTCP:
		return "TCP"
	case parser.ProtoUDP:
		return "UDP"
	case parser.ProtoICMP:
		return "ICMP"
	case parser.ProtoICMPv6:
		return "ICMPv6"
	default:
		return fmt.Sprintf("proto-%d", p)
	}
}

// analyzePacket 从一帧原始报文里抽载荷并做规则识别；无载荷或未命中返回 nil。
func analyzePacket(raw []byte) (*finding, bool) {
	info, err := parser.Parse(raw)
	if err != nil {
		return nil, false
	}
	payload, ok := parser.Payload(raw)
	if !ok || len(payload) == 0 {
		return nil, false
	}
	text := strings.TrimSpace(string(payload))
	if text == "" {
		return nil, false
	}
	res := dlp.Analyze(text)
	if len(res.Hits) == 0 {
		return nil, false
	}
	f := &finding{
		Src:    fmt.Sprintf("%s:%d", info.SrcIP, info.SrcPort),
		Dst:    fmt.Sprintf("%s:%d", info.DstIP, info.DstPort),
		Proto:  protoName(info.Protocol),
		Level:  res.Level.String(),
		Score:  res.Score,
		Types:  res.TypeNames(),
		Hits:   len(res.Hits),
		Masked: preview(dlp.Mask(text, res), previewRunes),
		Direct: res.Direct,
		// 记录原始载荷长度（不是脱敏后）：说明这条结果"看了多少正文"
		PayloadBytes: len(payload),
	}
	return f, true
}

const previewRunes = 100

// maskMarkers 是四级脱敏模板产生的标记，用来在预览里定位"被替换成了什么"。
// 顺序无关，取最先出现的那个。
var maskMarkers = []string{"已屏蔽", "***"}

// preview 把载荷压成单行并截断，避免表格被多行报文撑爆。
// 若文本里能定位到脱敏标记，就围绕标记取窗口——演示时一眼能看到
// "身份证号 → [已屏蔽:身份证号]"这种替换效果，而不是被 HTTP 头占满。
func preview(s string, max int) string {
	flat := strings.Join(strings.Fields(s), " ")
	if max <= 0 {
		return ""
	}
	runes := []rune(flat)
	if len(runes) <= max {
		return flat
	}
	anchor := -1
	for _, m := range maskMarkers {
		i := strings.Index(flat, m)
		if i < 0 {
			continue
		}
		if a := utf8.RuneCountInString(flat[:i]); anchor < 0 || a < anchor {
			anchor = a
		}
	}
	if anchor > max/2 {
		start := anchor - max/2
		end := start + max
		if end > len(runes) {
			end = len(runes)
			start = end - max
		}
		return "…" + string(runes[start:end]) + "…"
	}
	return truncateRunes(flat, max)
}

// truncateRunes 按字符（不是字节）截断，保证中文不会被切坏。
func truncateRunes(s string, max int) string {
	if max <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max]) + "…"
}

// detect 遍历数据源，返回不低于 minLevel 的 findings 与统计；limit<=0 表示不限。
func detect(src capture.Source, minLevel dlp.Level, limit int) ([]finding, stats, error) {
	st := stats{ByLevel: map[string]int{}}
	start := time.Now()
	ctx := context.Background()
	out := make([]finding, 0, 16)

	for {
		pkt, err := src.Next(ctx)
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return out, st, fmt.Errorf("读取报文失败：%w", err)
		}
		st.Frames++
		if _, ok := parser.Payload(pkt.Raw); !ok {
			continue
		}
		st.WithBody++

		f, ok := analyzePacket(pkt.Raw)
		if !ok {
			continue
		}
		lvl, err := dlp.ParseLevel(f.Level)
		if err != nil {
			continue
		}
		st.Findings++
		st.ByLevel[lvl.String()]++
		if lvl < minLevel {
			continue
		}
		f.Ts = pkt.Ts
		out = append(out, *f)
		if limit > 0 && len(out) >= limit {
			break
		}
	}
	st.DurationMs = time.Since(start).Milliseconds()
	return out, st, nil
}

// printTable 打印人类可读的 findings 表格。
func printTable(w io.Writer, fs []finding) {
	if len(fs) == 0 {
		fmt.Fprintln(w, "未发现达到阈值的敏感数据。")
		return
	}
	fmt.Fprintf(w, "%-8s %-26s %-4s %-5s %-6s %s\n", "时间", "源 → 目的", "级别", "分数", "命中", "脱敏预览")
	fmt.Fprintln(w, strings.Repeat("-", 110))
	for _, f := range fs {
		fmt.Fprintf(w, "%-8s %-26s %-4s %-5.2f %-6d %s\n",
			f.Ts.Format("15:04:05"), f.Src+"→"+f.Dst, f.Level, f.Score, f.Hits, f.Masked)
	}
}

// printStats 打印本次检测的规模与分级分布。
func printStats(w io.Writer, st stats, fs int) {
	order := []dlp.Level{dlp.LevelHigh, dlp.LevelMid, dlp.LevelLow}
	parts := make([]string, 0, 3)
	for _, l := range order {
		parts = append(parts, fmt.Sprintf("%s=%d", l.String(), st.ByLevel[l.String()]))
	}
	fmt.Fprintf(w, "\n共 %d 帧 / 含载荷 %d 帧 / 命中 %d 条 / 输出 %d 条（%s）/ 耗时 %d ms\n",
		st.Frames, st.WithBody, st.Findings, fs, strings.Join(parts, " "), st.DurationMs)
}

// findingTypeKeys 把展示用的中文类别名转成存储/接口用的英文键。
// 解析不出来的原样保留，宁可查到怪值也不要静默丢数据。
func findingTypeKeys(labels []string) []string {
	keys := make([]string, 0, len(labels))
	for _, label := range labels {
		if typ, ok := dlp.ParseEntityType(label); ok {
			keys = append(keys, string(typ))
			continue
		}
		keys = append(keys, label)
	}
	return keys
}

// findingRetention 是落库的保留时长，与在线存储的默认策略保持一致（7 天）。
const findingRetention = 7 * 24 * time.Hour

// splitEndpoint 把 "ip:port" 拆成两段；解析不出端口时端口记 0（仅作展示）。
func splitEndpoint(s string) (string, uint16) {
	i := strings.LastIndex(s, ":")
	if i < 0 {
		return s, 0
	}
	port, err := strconv.Atoi(s[i+1:])
	if err != nil || port < 0 || port > 65535 {
		return s[:i], 0
	}
	return s[:i], uint16(port)
}

// persistFindings 把检测结果写入 SQLite，供 /api/v1/dlp/findings 等接口查询。
// 让 CLI 直接落库而不是绕 HTTP：初赛演示要能"离线跑一遍 → 网页立刻能查"，
// 一条命令完成，不需要额外起采集端。
func persistFindings(dbPath string, findings []finding) (int, error) {
	store, err := storage.OpenSQLiteStore(dbPath, findingRetention)
	if err != nil {
		return 0, err
	}
	defer store.Close()
	n := 0
	for _, f := range findings {
		srcIP, srcPort := splitEndpoint(f.Src)
		dstIP, dstPort := splitEndpoint(f.Dst)
		rec := storage.DLPFinding{
			Ts: f.Ts, SrcIP: srcIP, SrcPort: srcPort, DstIP: dstIP, DstPort: dstPort,
			Proto: strings.ToLower(f.Proto), Level: f.Level, Score: f.Score,
			Types: findingTypeKeys(f.Types), Hits: f.Hits, Masked: f.Masked, Model: "l1",
			PayloadBytes: f.PayloadBytes, Direct: f.Direct,
		}
		if err := store.AppendFinding(rec); err != nil {
			return n, err
		}
		n++
	}
	return n, nil
}

// fatal 统一错误出口，便于 CLI 行为一致。
func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "flowmask: "+format+"\n", args...)
	os.Exit(1)
}

func main() {
	var (
		pcapPath = flag.String("pcap", "data/demo-dlp.pcap", "要检测的 pcap 文件路径")
		minLevel = flag.String("level", "SAFE", "只输出不低于该级别的结果：SAFE/L1/L2/L3")
		asJSON   = flag.Bool("json", false, "以 JSON 输出（findings 数组 + 统计），便于接入上层系统")
		limit    = flag.Int("limit", 0, "最多输出多少条，0 表示不限")
		quiet    = flag.Bool("quiet", false, "不逐条打印，只输出统计摘要")
		dbPath   = flag.String("db", "", "把检测结果写入该 SQLite 文件，供 /api/v1/dlp/* 查询；为空则不落库")
	)
	flag.Parse()

	minLvl, err := dlp.ParseLevel(*minLevel)
	if err != nil {
		fatal("级别参数无效：%v", err)
	}

	src, err := capture.OpenPcapFile(*pcapPath)
	if err != nil {
		fatal("打开 pcap 失败：%v（提示：可先运行 go run ./cmd/genpcaps 生成演示数据）", err)
	}
	defer src.Close()

	fs, st, err := detect(src, minLvl, *limit)
	if err != nil {
		fatal("%v", err)
	}

	// 先落库再输出：即使终端输出异常，证据也已经存进 SQLite。
	if *dbPath != "" {
		n, err := persistFindings(*dbPath, fs)
		if err != nil {
			fatal("写入检测结果失败：%v", err)
		}
		fmt.Fprintf(os.Stderr, "已写入 %d 条检测结果到 %s\n", n, *dbPath)
	}

	if *asJSON {
		payload := struct {
			Findings []finding `json:"findings"`
			Stats    stats     `json:"stats"`
		}{Findings: fs, Stats: st}
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(payload); err != nil {
			fatal("序列化失败：%v", err)
		}
		return
	}

	if !*quiet {
		printTable(os.Stdout, fs)
	}
	printStats(os.Stdout, st, len(fs))
}
