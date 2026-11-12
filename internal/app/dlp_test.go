package app

import (
	"net/netip"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/wukiQAQ/datausing/internal/aggregator"
	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/config"
	"github.com/wukiQAQ/datausing/internal/flow"
	"github.com/wukiQAQ/datausing/internal/parser"
	"github.com/wukiQAQ/datausing/internal/storage"
)

// dlpTestConfig 返回启用在线检测的配置（测试里按需再改字段）。
func dlpTestConfig() *config.Config {
	cfg := config.Default()
	cfg.DLP = config.DLPConfig{
		Enabled:    true,
		Mode:       "live",
		QueueSize:  8,
		Workers:    2,
		MaxPayload: 65536,
		MinLevel:   "SAFE",
	}
	return cfg
}

// newDLPTestApp 装配一个只跑检测链路的 App（数据源不参与）。
func newDLPTestApp(t *testing.T) (*App, storage.FindingStore) {
	t.Helper()
	cfg := dlpTestConfig()
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	bridge := storage.FindingStore(store)
	a := New(cfg, nil, flow.NewTable(30*time.Second, 5*time.Minute, nil), aggregator.New(100), store)
	if err := a.EnableDLP(store); err != nil {
		t.Fatalf("EnableDLP: %v", err)
	}
	t.Cleanup(a.CloseDLP)
	return a, bridge
}

// waitFindings 轮询等待至少 n 条结果（worker 异步，不能用固定 sleep 断言）。
func waitFindings(t *testing.T, store storage.FindingStore, n int, timeout time.Duration) []storage.DLPFinding {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		rows, err := store.ExportFindings(storage.FindingFilter{Limit: 10})
		if err != nil {
			t.Fatalf("ExportFindings: %v", err)
		}
		if len(rows) >= n {
			return rows
		}
		if time.Now().After(deadline) {
			t.Fatalf("等待 %d 条 finding 超时，实际 %d 条", n, len(rows))
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// TestOnlineDLPStoresMaskedFinding 验证"在线抓包 → L1 识别 → 脱敏落库"的完整链路：
// 落库的必须是脱敏副本（不含原始身份证号），并带级别/类别/来源五元组。
func TestOnlineDLPStoresMaskedFinding(t *testing.T) {
	a, findings := newDLPTestApp(t)
	text := "用户身份证 11010519491231002X 余额 8800 元"
	pkt := &capture.Packet{
		Ts:  time.Unix(1700000000, 0).UTC(),
		Raw: parser.BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 40000, 80, []byte(text), false),
	}
	if !a.HandlePacket(pkt) {
		t.Fatal("HandlePacket 解析失败")
	}
	rows := waitFindings(t, findings, 1, 3*time.Second)
	f := rows[0]
	if f.Level != "L3" {
		t.Errorf("Level=%q, want L3", f.Level)
	}
	if strings.Contains(f.Masked, "11010519491231002X") {
		t.Errorf("落库内容是明文，必须脱敏: %q", f.Masked)
	}
	if !strings.Contains(f.Masked, "[已屏蔽") {
		t.Errorf("masked 缺少屏蔽标记: %q", f.Masked)
	}
	if len(f.Types) == 0 || f.Types[0] != "id_card" {
		t.Errorf("types=%v, want 包含 id_card", f.Types)
	}
	if f.Model != "l1" || f.Proto != "tcp" {
		t.Errorf("model=%q proto=%q, want l1/tcp", f.Model, f.Proto)
	}
	if f.SrcIP != "10.0.0.1" || f.DstIP != "10.0.0.2" || f.SrcPort != 40000 || f.DstPort != 80 {
		t.Errorf("来源五元组不对: %s:%d -> %s:%d", f.SrcIP, f.SrcPort, f.DstIP, f.DstPort)
	}
	if f.PayloadBytes != len(text) {
		t.Errorf("PayloadBytes=%d, want %d", f.PayloadBytes, len(text))
	}
	if frames, hits, dropped := a.DLPSnapshot(); frames < 1 || hits != 1 || dropped != 0 {
		t.Errorf("计数 frames=%d hits=%d dropped=%d, want frames>=1 hits=1 dropped=0", frames, hits, dropped)
	}
}

// TestOnlineDLPMinLevelFilters 验证 min_level 过滤：低于阈值的命中仍会被分析，但不落库。
func TestOnlineDLPMinLevelFilters(t *testing.T) {
	cfg := dlpTestConfig()
	cfg.DLP.MinLevel = "L3"
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	defer store.Close()
	a := New(cfg, nil, flow.NewTable(30*time.Second, 5*time.Minute, nil), aggregator.New(100), store)
	if err := a.EnableDLP(store); err != nil {
		t.Fatalf("EnableDLP: %v", err)
	}
	defer a.CloseDLP()

	// 手机号是低/中危，低于 L3：分析帧数应增加，但命中的落库数为 0。
	pkt := &capture.Packet{
		Ts:  time.Unix(1700000000, 0).UTC(),
		Raw: parser.BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 40000, 80, []byte("联系电话 13800138000"), false),
	}
	if !a.HandlePacket(pkt) {
		t.Fatal("HandlePacket 解析失败")
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		if frames, _, _ := a.DLPSnapshot(); frames >= 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("等待分析帧数增加超时")
		}
		time.Sleep(5 * time.Millisecond)
	}
	// 再给 worker 一点时间完成落库判定
	time.Sleep(30 * time.Millisecond)
	if _, hits, _ := a.DLPSnapshot(); hits != 0 {
		t.Errorf("min_level=L3 时手机号命中不应落库，hits=%d", hits)
	}
	findings, err := store.ExportFindings(storage.FindingFilter{})
	if err != nil {
		t.Fatalf("ExportFindings: %v", err)
	}
	if len(findings) != 0 {
		t.Errorf("findings 条数=%d, want 0", len(findings))
	}
}

// TestOnlineDLPTruncatesOversizePayload 验证超长载荷按 max_payload 截断分析，并在结果里标注原因。
func TestOnlineDLPTruncatesOversizePayload(t *testing.T) {
	cfg := dlpTestConfig()
	cfg.DLP.MaxPayload = 64
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	defer store.Close()
	a := New(cfg, nil, flow.NewTable(30*time.Second, 5*time.Minute, nil), aggregator.New(100), store)
	if err := a.EnableDLP(store); err != nil {
		t.Fatalf("EnableDLP: %v", err)
	}
	defer a.CloseDLP()

	text := "身份证 11010519491231002X " + strings.Repeat("x", 300)
	pkt := &capture.Packet{
		Ts:  time.Unix(1700000000, 0).UTC(),
		Raw: parser.BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 40000, 80, []byte(text), false),
	}
	if !a.HandlePacket(pkt) {
		t.Fatal("HandlePacket 解析失败")
	}
	rows := waitFindings(t, store, 1, 3*time.Second)
	f := rows[0]
	if len(f.Reasons) == 0 || !strings.Contains(f.Reasons[0], "max_payload") {
		t.Errorf("截断结果应附原因，got %v", f.Reasons)
	}
	if f.PayloadBytes != 64 {
		t.Errorf("PayloadBytes=%d, want 截断后的 64", f.PayloadBytes)
	}
}

// TestEnableDLPRejectsDisabledConfig 验证未启用时不会静默起 worker：
// 调用方必须显式打开 cfg.DLP.Enabled，否则应拿到错误。
func TestEnableDLPRejectsDisabledConfig(t *testing.T) {
	cfg := dlpTestConfig()
	cfg.DLP.Enabled = false
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	defer store.Close()
	a := New(cfg, nil, flow.NewTable(30*time.Second, 5*time.Minute, nil), aggregator.New(100), store)
	if err := a.EnableDLP(store); err == nil {
		t.Fatal("DLP 未启用时 EnableDLP 应返回错误")
	}
}

// TestDLPRejectsInvalidMinLevel 验证 min_level 拼错时启动即失败，而不是静默按 SAFE 跑。
func TestDLPRejectsInvalidMinLevel(t *testing.T) {
	cfg := dlpTestConfig()
	cfg.DLP.MinLevel = "L9"
	store, err := storage.OpenFileStore(t.TempDir(), time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	defer store.Close()
	a := New(cfg, nil, flow.NewTable(30*time.Second, 5*time.Minute, nil), aggregator.New(100), store)
	if err := a.EnableDLP(store); err == nil {
		t.Fatal("非法 min_level 应报错")
	}
}

// blockingStore 是只阻塞 AppendFinding 的假存储，用来稳定复现"队列满丢帧"。
type blockingStore struct {
	gate     chan struct{}
	entered  chan struct{}
	mu       sync.Mutex
	appended []storage.DLPFinding
}

func (b *blockingStore) AppendFinding(f storage.DLPFinding) error {
	if b.entered != nil {
		select {
		case b.entered <- struct{}{}:
		default:
		}
	}
	<-b.gate // 一直挡住 worker，让队列被填满
	b.mu.Lock()
	b.appended = append(b.appended, f)
	b.mu.Unlock()
	return nil
}

func (b *blockingStore) Finding(int64) (storage.DLPFinding, bool, error) {
	return storage.DLPFinding{}, false, nil
}

func (b *blockingStore) QueryFindings(storage.FindingFilter) ([]storage.DLPFinding, int, error) {
	return nil, 0, nil
}

func (b *blockingStore) ExportFindings(storage.FindingFilter) ([]storage.DLPFinding, error) {
	return nil, nil
}

func (b *blockingStore) FindingStats(storage.FindingFilter, time.Duration) (storage.FindingStats, error) {
	return storage.FindingStats{}, nil
}

// TestOnlineDLPCountsDroppedWhenQueueFull 验证 backpressure 语义：
// worker 卡住时队列被填满，后续载荷必须被丢弃并计入 dlp_dropped，而不是无限堆积内存。
func TestOnlineDLPCountsDroppedWhenQueueFull(t *testing.T) {
	fake := &blockingStore{gate: make(chan struct{}), entered: make(chan struct{}, 1)}
	cfg := config.DLPConfig{Enabled: true, QueueSize: 1, Workers: 1, MaxPayload: 1024, MinLevel: "SAFE"}
	p, err := newDLPPipeline(fake, cfg)
	if err != nil {
		t.Fatalf("newDLPPipeline: %v", err)
	}
	job := func() dlpJob {
		payload := []byte("联系电话 13800138000")
		return dlpJob{payload: payload, payloadBytes: len(payload), proto: "tcp"}
	}
	p.submit(job()) // 进 worker 后被 gate 挡住
	select {
	case <-fake.entered: // 首项确实离开队列并进入存储，才填第二项。
	case <-time.After(2 * time.Second):
		close(fake.gate)
		p.close()
		t.Fatal("worker 未进入存储")
	}
	p.submit(job()) // 占满长度为 1 的队列
	p.submit(job()) // 队列满 → 丢弃
	close(fake.gate)
	p.close()
	if frames, dropped := p.frames.Load(), p.dropped.Load(); frames != 2 || dropped != 1 {
		t.Errorf("提交3条应分析2条且丢弃1条，实际分析 %d、丢弃 %d", frames, dropped)
	}
	if hits := p.hits.Load(); hits != 2 {
		t.Errorf("放行后应处理 2 条（1 条在 worker + 1 条在队列），实际落库 %d 条", hits)
	}
}
