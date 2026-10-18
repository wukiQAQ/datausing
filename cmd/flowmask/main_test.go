package main

import (
	"bytes"
	"net/netip"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/dlp"
	"github.com/wukiQAQ/datausing/internal/parser"
	"github.com/wukiQAQ/datausing/internal/storage"
)

var (
	testClient = netip.MustParseAddr("10.0.0.5")
	testServer = netip.MustParseAddr("203.0.113.10")
)

const (
	// idCardPayload 同时含姓名、手机号、身份证，走"极端阈值直达"应判 L3。
	idCardPayload = `{"name":"张三","phone":"13800138000","id_card":"11010519491231002X"}`
	// formPayload 是表单编码，邮箱 + 地址两类共现，应判 L2。
	formPayload = `username=zhangsan&email=zhang.san@example.com&address=福建省福州市鼓楼区五四路100号`
	// bankPayload 是 CSV，银行卡通过 Luhn 校验，应判 L3。
	bankPayload = "name,phone,bank_card\n李四,13900139000,4111111111111111"
	// safePayload 是正常健康检查，用于误报对照。
	safePayload = "GET /api/v1/health HTTP/1.1"
)

// buildFrame 构造一帧带应用层载荷的 TCP 报文。
func buildFrame(t *testing.T, sport uint16, payload string) []byte {
	t.Helper()
	return parser.BuildEthIPv4TCP(testClient, testServer, sport, 80, []byte(payload), false)
}

// writePcap 把若干载荷写成一个临时 pcap，每条载荷前补一个 SYN 帧（与真实握手一致）。
func writePcap(t *testing.T, payloads []string) string {
	t.Helper()
	ts := time.Date(2026, 10, 18, 10, 0, 0, 0, time.UTC)
	pkts := make([]capture.Packet, 0, len(payloads)*2)
	for i, p := range payloads {
		sport := uint16(50000 + i)
		pkts = append(pkts, capture.Packet{
			Ts:    ts,
			Raw:   parser.BuildEthIPv4TCP(testClient, testServer, sport, 80, nil, true),
			Iface: "test",
		})
		ts = ts.Add(50 * time.Millisecond)
		pkts = append(pkts, capture.Packet{Ts: ts, Raw: buildFrame(t, sport, p), Iface: "test"})
		ts = ts.Add(50 * time.Millisecond)
	}
	path := filepath.Join(t.TempDir(), "demo.pcap")
	if err := capture.WritePCAP(path, pkts); err != nil {
		t.Fatalf("写 pcap 失败：%v", err)
	}
	return path
}

// TestAnalyzePacketLevels 校验各级敏感载荷的识别结果。
func TestAnalyzePacketLevels(t *testing.T) {
	cases := []struct {
		name      string
		payload   string
		wantLevel string
		wantTypes []string
	}{
		{"JSON 订单", idCardPayload, "L3", []string{"身份证号", "手机号", "姓名"}},
		{"表单注册", formPayload, "L2", []string{"邮箱", "地址"}},
		{"CSV 银行卡", bankPayload, "L3", []string{"手机号", "银行卡号"}},
		{"正常请求", safePayload, "", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			f, ok := analyzePacket(buildFrame(t, 51001, tc.payload))
			if tc.wantLevel == "" {
				if ok {
					t.Fatalf("正常请求不应命中，却得到 %+v", f)
				}
				return
			}
			if !ok || f == nil {
				t.Fatalf("应命中敏感数据，实际未命中")
			}
			if f.Level != tc.wantLevel {
				t.Fatalf("级别 = %s，期望 %s（分数 %.2f，类别 %v）", f.Level, tc.wantLevel, f.Score, f.Types)
			}
			for _, want := range tc.wantTypes {
				found := false
				for _, got := range f.Types {
					if got == want {
						found = true
					}
				}
				if !found {
					t.Fatalf("类别缺少 %q，实际 %v", want, f.Types)
				}
			}
			if f.Masked == "" {
				t.Fatalf("脱敏预览不应为空")
			}
			// analyzePacket 不填时间（由 detect 补充），但源/目的必须已填充。
			if f.Src == "" || f.Dst == "" || f.Proto != "TCP" {
				t.Fatalf("五元组信息缺失：src=%q dst=%q proto=%q", f.Src, f.Dst, f.Proto)
			}
		})
	}
}

// TestMaskedHidesSecrets 脱敏预览里不能出现完整的身份证号/手机号。
func TestMaskedHidesSecrets(t *testing.T) {
	f, ok := analyzePacket(buildFrame(t, 51002, idCardPayload))
	if !ok {
		t.Fatal("应命中敏感数据")
	}
	for _, secret := range []string{"11010519491231002X", "13800138000"} {
		if strings.Contains(f.Masked, secret) {
			t.Fatalf("脱敏预览泄露了原文 %q：%s", secret, f.Masked)
		}
	}
	if !strings.Contains(f.Masked, "已屏蔽") {
		t.Fatalf("L3 应采用全屏蔽模板，实际：%s", f.Masked)
	}
}

// TestAnalyzePacketWithoutPayload 纯 SYN 帧没有载荷，不应产生 finding。
func TestAnalyzePacketWithoutPayload(t *testing.T) {
	syn := parser.BuildEthIPv4TCP(testClient, testServer, 51003, 80, nil, true)
	if f, ok := analyzePacket(syn); ok {
		t.Fatalf("无载荷报文不应命中，却得到 %+v", f)
	}
}

// TestDetectFiltersByLevelAndLimit 校验级别过滤与条数上限。
func TestDetectFiltersByLevelAndLimit(t *testing.T) {
	path := writePcap(t, []string{safePayload, idCardPayload, formPayload, bankPayload})

	src, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("打开 pcap 失败：%v", err)
	}
	defer src.Close()
	fs, st, err := detect(src, dlp.LevelLow, 0)
	if err != nil {
		t.Fatalf("detect 失败：%v", err)
	}
	if len(fs) != 3 {
		t.Fatalf("L1 及以上应有 3 条，实际 %d", len(fs))
	}
	if st.Frames != 8 || st.WithBody != 4 || st.Findings != 3 {
		t.Fatalf("统计异常：%+v", st)
	}
	if st.ByLevel["L3"] != 2 || st.ByLevel["L2"] != 1 {
		t.Fatalf("分级统计异常：%v", st.ByLevel)
	}

	src2, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("打开 pcap 失败：%v", err)
	}
	defer src2.Close()
	high, _, err := detect(src2, dlp.LevelHigh, 0)
	if err != nil {
		t.Fatalf("detect 失败：%v", err)
	}
	if len(high) != 2 {
		t.Fatalf("仅 L3 应有 2 条，实际 %d", len(high))
	}
	for _, f := range high {
		if f.Level != "L3" {
			t.Fatalf("级别过滤失效：%s", f.Level)
		}
	}

	src3, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("打开 pcap 失败：%v", err)
	}
	defer src3.Close()
	limited, _, err := detect(src3, dlp.LevelSafe, 1)
	if err != nil {
		t.Fatalf("detect 失败：%v", err)
	}
	if len(limited) != 1 {
		t.Fatalf("limit=1 应只输出 1 条，实际 %d", len(limited))
	}
	// detect 负责补上时间戳，表格与 JSON 都依赖它。
	if limited[0].Ts.IsZero() {
		t.Fatal("finding 缺少时间戳")
	}
}

// TestPrintTableAndStats 校验表格与摘要的输出形状。
func TestPrintTableAndStats(t *testing.T) {
	path := writePcap(t, []string{idCardPayload, formPayload})
	src, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("打开 pcap 失败：%v", err)
	}
	defer src.Close()
	fs, st, err := detect(src, dlp.LevelSafe, 0)
	if err != nil {
		t.Fatalf("detect 失败：%v", err)
	}

	var buf bytes.Buffer
	printTable(&buf, fs)
	out := buf.String()
	for _, want := range []string{"时间", "级别", "脱敏预览", "L3", "L2", "→"} {
		if !strings.Contains(out, want) {
			t.Fatalf("表格缺少 %q：\n%s", want, out)
		}
	}

	var buf2 bytes.Buffer
	printStats(&buf2, st, len(fs))
	if got := buf2.String(); !strings.Contains(got, "共 4 帧") {
		t.Fatalf("摘要异常：%s", got)
	}
}

// TestPrintEmptyTable 没有命中时也要给出明确提示。
func TestPrintEmptyTable(t *testing.T) {
	var buf bytes.Buffer
	printTable(&buf, nil)
	if !strings.Contains(buf.String(), "未发现") {
		t.Fatalf("空结果提示异常：%s", buf.String())
	}
}

// TestPreviewAnchorsOnMaskMarker 预览应围绕脱敏标记取窗口，而不是被报文头占满。
func TestPreviewAnchorsOnMaskMarker(t *testing.T) {
	longHeader := "POST /api/order HTTP/1.1 Host: shop.example.com Content-Type: application/json Content-Length: 118 Authorization: Bearer abcdefghijklmnop"
	body := longHeader + ` {"name":"[已屏蔽:姓名]","id_card":"[已屏蔽:身份证号]"}`
	got := preview(body, 40)
	if !strings.Contains(got, "已屏蔽") {
		t.Fatalf("预览未展示脱敏片段：%s", got)
	}
	if strings.HasPrefix(got, "POST") {
		t.Fatalf("预览应从脱敏标记附近开始：%s", got)
	}
	// 收窄窗口后仍要给出前后省略号，表示被截断。
	if !strings.HasPrefix(got, "…") {
		t.Fatalf("截断预览应带前缀省略号：%s", got)
	}
	// 短文本不裁剪、不加省略号。
	if short := preview(`"phone":"1******"`, 100); short != `"phone":"1******"` {
		t.Fatalf("短文本被改动：%q", short)
	}
}

func TestTruncateRunes(t *testing.T) {
	got := truncateRunes("客户张伟，电话13700137000，需要改签", 4)
	if want := "客户张伟" + "…"; got != want {
		t.Fatalf("截断结果 = %q，期望 %q", got, want)
	}
	if truncateRunes("短文本", 10) != "短文本" {
		t.Fatal("短文本不应截断")
	}
	if truncateRunes("abc", 0) != "" {
		t.Fatal("max<=0 应返回空串")
	}
	// 截断后仍是合法 UTF-8（用 rune 解码检查，出现 U+FFFD 即坏）。
	if strings.ContainsRune(got, '\uFFFD') {
		t.Fatalf("截断产生了非法字符：%q", got)
	}
}

// TestProtoName 协议号转展示名。
func TestProtoName(t *testing.T) {
	cases := map[uint8]string{
		parser.ProtoTCP:    "TCP",
		parser.ProtoUDP:    "UDP",
		parser.ProtoICMP:   "ICMP",
		parser.ProtoICMPv6: "ICMPv6",
		99:                 "proto-99",
	}
	for proto, want := range cases {
		if got := protoName(proto); got != want {
			t.Fatalf("protoName(%d) = %q，期望 %q", proto, got, want)
		}
	}
}

// TestSplitEndpoint 校验 "ip:port" 拆分与异常输入（端口解析失败时记 0，不 panic）。
func TestSplitEndpoint(t *testing.T) {
	cases := []struct {
		in   string
		ip   string
		port uint16
	}{
		{"10.0.0.5:50001", "10.0.0.5", 50001},
		{"10.0.0.5", "10.0.0.5", 0},
		{"10.0.0.5:abc", "10.0.0.5", 0},
		{"10.0.0.5:70000", "10.0.0.5", 0},
		{"[2001:db8::1]:443", "[2001:db8::1]", 443},
	}
	for _, c := range cases {
		ip, port := splitEndpoint(c.in)
		if ip != c.ip || port != c.port {
			t.Fatalf("splitEndpoint(%q) = (%q,%d)，期望 (%q,%d)", c.in, ip, port, c.ip, c.port)
		}
	}
}

// TestPersistFindings 校验 -db 落库：CLI 检测结果能被存储层按契约查回来，
// 且入库的是脱敏副本、级别/类别/元信息完整。
func TestPersistFindings(t *testing.T) {
	path := writePcap(t, []string{idCardPayload, formPayload, safePayload})
	src, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("打开 pcap 失败：%v", err)
	}
	defer src.Close()
	fs, st, err := detect(src, dlp.LevelSafe, 0)
	if err != nil {
		t.Fatalf("detect 失败：%v", err)
	}
	if len(fs) != 2 {
		t.Fatalf("期望 2 条命中（身份证 + 表单），实际 %d", len(fs))
	}
	if st.Findings != 2 {
		t.Fatalf("统计命中数 = %d", st.Findings)
	}

	dbPath := filepath.Join(t.TempDir(), "flowmask.db")
	n, err := persistFindings(dbPath, fs)
	if err != nil {
		t.Fatalf("落库失败：%v", err)
	}
	if n != len(fs) {
		t.Fatalf("落库条数 = %d，期望 %d", n, len(fs))
	}

	store, err := storage.OpenSQLiteStore(dbPath, findingRetention)
	if err != nil {
		t.Fatalf("重新打开存储失败：%v", err)
	}
	defer store.Close()
	rows, total, err := store.QueryFindings(storage.FindingFilter{})
	if err != nil {
		t.Fatalf("查询落库结果失败：%v", err)
	}
	if total != 2 || len(rows) != 2 {
		t.Fatalf("total=%d len=%d，期望 2", total, len(rows))
	}
	// 倒序返回：表单那条时间更晚，排在前
	if rows[0].Level != "L2" || rows[1].Level != "L3" {
		t.Fatalf("级别顺序不符：%s / %s", rows[0].Level, rows[1].Level)
	}
	// L3 那条：脱敏副本不含原文、直达标记保留、端口与字节数完整
	if strings.Contains(rows[1].Masked, "11010519491231002X") || !strings.Contains(rows[1].Masked, "已屏蔽") {
		t.Fatalf("L3 落库内容不是脱敏副本：%q", rows[1].Masked)
	}
	if !rows[1].Direct || rows[1].SrcIP != "10.0.0.5" || rows[1].SrcPort != 50000 {
		t.Fatalf("L3 元信息不完整：%+v", rows[1])
	}
	if rows[1].PayloadBytes <= 0 || rows[1].Model != "l1" {
		t.Fatalf("L3 payload_bytes/model 缺失：%+v", rows[1])
	}
	if !hasType(rows[1].Types, "id_card") || !hasType(rows[1].Types, "phone") {
		t.Fatalf("L3 类别 = %v", rows[1].Types)
	}
	// L2 那条：邮箱 + 地址共现
	if !hasType(rows[0].Types, "email") || !hasType(rows[0].Types, "address") {
		t.Fatalf("L2 类别 = %v", rows[0].Types)
	}

	// 统计口径与 API 一致：L3/L2 各一条，SAFE 补零
	stats, err := store.FindingStats(storage.FindingFilter{}, time.Minute)
	if err != nil {
		t.Fatalf("统计失败：%v", err)
	}
	byLevel := map[string]int{}
	for _, c := range stats.ByLevel {
		byLevel[c.Key] = c.Count
	}
	if byLevel["L3"] != 1 || byLevel["L2"] != 1 || len(stats.ByLevel) != 4 {
		t.Fatalf("落库统计不符：%+v", stats.ByLevel)
	}
}

// hasType 判断类别列表里是否包含指定类别。
func hasType(types []string, want string) bool {
	for _, typ := range types {
		if typ == want {
			return true
		}
	}
	return false
}
