package storage

import (
	"path/filepath"
	"testing"
	"time"
)

// baseTs 是测试用的固定时间基准：所有样本时间都由它偏移，避免依赖墙钟。
var baseTs = time.Date(2026, 10, 20, 10, 0, 0, 0, time.UTC)

// findingSamples 返回三条覆盖 L3/L2、不同类别与端口的样本。
func findingSamples() []DLPFinding {
	return []DLPFinding{
		{
			Ts: baseTs, SrcIP: "10.0.0.5", SrcPort: 50001, DstIP: "203.0.113.10", DstPort: 80,
			Proto: "tcp", Host: "shop.example.com", Path: "/api/order", ContentType: "json",
			Level: "L3", Score: 0.958, Types: []string{"id_card", "phone"}, Hits: 3,
			Masked: `{"id_card":"[已屏蔽:身份证号]","phone":"[已屏蔽:手机号]"}`,
			Model:  "l1", PayloadBytes: 421, Direct: true,
		},
		{
			Ts: baseTs.Add(time.Minute), SrcIP: "10.0.0.7", SrcPort: 50002, DstIP: "203.0.113.20", DstPort: 8080,
			Proto: "tcp", ContentType: "form",
			Level: "L2", Score: 0.565, Types: []string{"email", "address"}, Hits: 2,
			Masked: `email=z******&address=福******`, Model: "l1", PayloadBytes: 233,
		},
		{
			Ts: baseTs.Add(2 * time.Minute), SrcIP: "10.0.0.9", SrcPort: 50003, DstIP: "203.0.113.30", DstPort: 443,
			Proto: "tcp", ContentType: "text",
			Level: "L3", Score: 1.0, Types: []string{"secret"}, Hits: 1,
			Masked: "api_key=[已屏蔽:密钥凭据]", Reasons: []string{"api_key 出现在 URL 查询串中"},
			Model: "l1", PayloadBytes: 118, Degraded: true,
		},
	}
}

// fillFindings 写入样本并返回查询到的 ID 列表（按时间倒序，ids[0] 是最新一条）。
func fillFindings(t *testing.T, store FindingStore) []int64 {
	t.Helper()
	ids := make([]int64, 0, 3)
	for _, f := range findingSamples() {
		if err := store.AppendFinding(f); err != nil {
			t.Fatalf("AppendFinding: %v", err)
		}
	}
	items, total, err := store.QueryFindings(FindingFilter{})
	if err != nil {
		t.Fatalf("QueryFindings: %v", err)
	}
	if total != len(items) {
		t.Fatalf("total=%d 与返回条数 %d 不一致", total, len(items))
	}
	for _, f := range items {
		if f.ID <= 0 {
			t.Fatalf("AppendFinding 未回填 ID: %+v", f)
		}
		ids = append(ids, f.ID)
	}
	return ids
}

// testFindingStore 是两种后端共用的行为测试：同一套断言，保证语义一致。
// reopen 用于验证落盘/持久化（会关闭传入的 store，因此放在最后执行）。
func testFindingStore(t *testing.T, store FindingStore, reopen func(t *testing.T) FindingStore) {
	t.Helper()
	ids := fillFindings(t, store)
	if len(ids) != 3 {
		t.Fatalf("期望 3 条样本，得到 %d", len(ids))
	}
	// QueryFindings 默认按时间倒序：最新写入的 secret 那条在最前。
	items, total, err := store.QueryFindings(FindingFilter{})
	if err != nil || total != 3 {
		t.Fatalf("QueryFindings total=%d err=%v", total, err)
	}
	if items[0].SrcIP != "10.0.0.9" {
		t.Fatalf("期望最新的 10.0.0.9 排第一，实际 %s", items[0].SrcIP)
	}

	t.Run("按分级过滤", func(t *testing.T) {
		got, total, err := store.QueryFindings(FindingFilter{Level: "L3"})
		if err != nil || total != 2 || len(got) != 2 {
			t.Fatalf("L3 过滤 total=%d len=%d err=%v", total, len(got), err)
		}
		// 大小写不敏感：API 层传小写也应命中
		got, total, _ = store.QueryFindings(FindingFilter{Level: "l3"})
		if total != 2 {
			t.Fatalf("小写级别过滤 total=%d", total)
		}
	})
	t.Run("按类别过滤", func(t *testing.T) {
		got, total, err := store.QueryFindings(FindingFilter{Type: "phone"})
		if err != nil || total != 1 || got[0].Level != "L3" {
			t.Fatalf("phone 过滤 total=%d err=%v", total, err)
		}
		if _, total, _ := store.QueryFindings(FindingFilter{Type: "id_card"}); total != 1 {
			t.Fatalf("id_card 过滤 total=%d", total)
		}
		// 子串不能误命中（instr 两端补逗号的语义）
		if _, total, _ := store.QueryFindings(FindingFilter{Type: "phonex"}); total != 0 {
			t.Fatalf("phonex 不应命中，total=%d", total)
		}
	})
	t.Run("按来源过滤", func(t *testing.T) {
		if _, total, _ := store.QueryFindings(FindingFilter{Src: "10.0.0."}); total != 3 {
			t.Fatalf("前缀过滤 total=%d", total)
		}
		if _, total, _ := store.QueryFindings(FindingFilter{Src: "10.0.0.9"}); total != 1 {
			t.Fatalf("精确 IP 过滤 total=%d（不应把 10.0.0.9 当成 10.0.0. 前缀）", total)
		}
	})
	t.Run("时间窗口", func(t *testing.T) {
		from := baseTs.Add(30 * time.Second)
		to := baseTs.Add(90 * time.Second)
		got, total, err := store.QueryFindings(FindingFilter{From: from, To: to})
		if err != nil || total != 1 || got[0].ContentType != "form" {
			t.Fatalf("时间窗口 total=%d err=%v", total, err)
		}
	})
	t.Run("分页", func(t *testing.T) {
		page1, total, err := store.QueryFindings(FindingFilter{Limit: 2})
		if err != nil || total != 3 || len(page1) != 2 {
			t.Fatalf("第一页 total=%d len=%d err=%v", total, len(page1), err)
		}
		page2, _, err := store.QueryFindings(FindingFilter{Limit: 2, Offset: 2})
		if err != nil || len(page2) != 1 {
			t.Fatalf("第二页 len=%d err=%v", len(page2), err)
		}
		if page1[0].ID == page2[0].ID {
			t.Fatalf("分页出现重复记录 id=%d", page1[0].ID)
		}
		empty, _, err := store.QueryFindings(FindingFilter{Limit: 2, Offset: 99})
		if err != nil || len(empty) != 0 {
			t.Fatalf("越界偏移应返回空页，len=%d err=%v", len(empty), err)
		}
	})
	t.Run("单条查询", func(t *testing.T) {
		got, ok, err := store.Finding(ids[0])
		if err != nil || !ok {
			t.Fatalf("Finding(%d) ok=%v err=%v", ids[0], ok, err)
		}
		if got.Level != "L3" || len(got.Types) != 1 || got.Types[0] != "secret" {
			t.Fatalf("单条内容不符: %+v", got)
		}
		if len(got.Reasons) != 1 || !got.Degraded {
			t.Fatalf("单条 reasons/degraded 不符: %+v", got)
		}
		if _, ok, err := store.Finding(999999); err != nil || ok {
			t.Fatalf("不存在的 ID 应返回 ok=false，实际 ok=%v err=%v", ok, err)
		}
	})
	t.Run("导出不分页", func(t *testing.T) {
		all, err := store.ExportFindings(FindingFilter{})
		if err != nil || len(all) != 3 {
			t.Fatalf("ExportFindings len=%d err=%v", len(all), err)
		}
		l3, err := store.ExportFindings(FindingFilter{Level: "L3"})
		if err != nil || len(l3) != 2 {
			t.Fatalf("ExportFindings L3 len=%d err=%v", len(l3), err)
		}
	})
	t.Run("统计", func(t *testing.T) {
		stats, err := store.FindingStats(FindingFilter{From: baseTs, To: baseTs.Add(3 * time.Minute)}, time.Minute)
		if err != nil {
			t.Fatalf("FindingStats: %v", err)
		}
		if stats.Total != 3 {
			t.Fatalf("统计总数=%d", stats.Total)
		}
		byLevel := map[string]int{}
		for _, c := range stats.ByLevel {
			byLevel[c.Key] = c.Count
		}
		if byLevel["L3"] != 2 || byLevel["L2"] != 1 || byLevel["L1"] != 0 || byLevel["SAFE"] != 0 {
			t.Fatalf("分级分布不符: %+v", stats.ByLevel)
		}
		if len(stats.ByLevel) != 4 || stats.ByLevel[0].Key != "L3" {
			t.Fatalf("分级分布应固定四个级别且 L3 在前: %+v", stats.ByLevel)
		}
		byType := map[string]int{}
		for _, c := range stats.ByType {
			byType[c.Key] = c.Count
		}
		if byType["phone"] != 1 || byType["email"] != 1 || byType["secret"] != 1 {
			t.Fatalf("类别统计不符: %+v", stats.ByType)
		}
		if len(stats.Timeline) != 4 {
			t.Fatalf("3 分钟窗口按 1 分钟切桶应有 4 个桶，实际 %d", len(stats.Timeline))
		}
		sum := 0
		for _, b := range stats.Timeline {
			sum += b.Total
		}
		if sum != stats.Total {
			t.Fatalf("时间线合计 %d != 总数 %d", sum, stats.Total)
		}
	})
	t.Run("统计限制桶数", func(t *testing.T) {
		// 30 天窗口按 1 秒切桶会爆量，必须自动放大步长
		stats, err := store.FindingStats(FindingFilter{From: baseTs.Add(-30 * 24 * time.Hour), To: baseTs}, time.Second)
		if err != nil {
			t.Fatalf("FindingStats: %v", err)
		}
		if len(stats.Timeline) > maxTimelineBuckets {
			t.Fatalf("时间线桶数 %d 超过上限 %d", len(stats.Timeline), maxTimelineBuckets)
		}
		if stats.Step <= time.Second {
			t.Fatalf("步长应被放大，实际 %v", stats.Step)
		}
	})
	t.Run("保留策略", func(t *testing.T) {
		// 写入一条 8 天前的旧结果，Prune 时应被清掉（findings 保留 7 天）。
		if err := store.AppendFinding(DLPFinding{Ts: baseTs.Add(-8 * 24 * time.Hour), SrcIP: "10.0.0.1", Proto: "tcp", Level: "L1", Types: []string{"email"}, Masked: "x"}); err != nil {
			t.Fatalf("AppendFinding 旧数据: %v", err)
		}
		pruner, ok := store.(interface{ Prune(time.Time) error })
		if !ok {
			t.Fatalf("存储未实现 Prune")
		}
		if err := pruner.Prune(baseTs); err != nil {
			t.Fatalf("Prune: %v", err)
		}
		if _, total, _ := store.QueryFindings(FindingFilter{}); total != 3 {
			t.Fatalf("保留策略清理后应剩 3 条，实际 %d", total)
		}
	})
	t.Run("持久化", func(t *testing.T) {
		reopened := reopen(t)
		items, total, err := reopened.QueryFindings(FindingFilter{})
		if err != nil || total != 3 {
			t.Fatalf("重新打开后 total=%d err=%v", total, err)
		}
		// 抽查一条：类别、reasons、degraded 都要原样保留
		var secret DLPFinding
		for _, f := range items {
			if f.Types[0] == "secret" {
				secret = f
			}
		}
		if secret.Masked == "" || len(secret.Reasons) != 1 || !secret.Degraded || len(secret.Types) != 1 {
			t.Fatalf("重新打开后字段丢失: %+v", secret)
		}
	})
}

func TestFindingStoreSQLite(t *testing.T) {
	path := filepath.Join(t.TempDir(), "netmon.db")
	store, err := OpenSQLiteStore(path, time.Hour)
	if err != nil {
		t.Fatalf("OpenSQLiteStore: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	testFindingStore(t, store, func(t *testing.T) FindingStore {
		t.Helper()
		s, err := OpenSQLiteStore(path, time.Hour)
		if err != nil {
			t.Fatalf("重开 SQLite: %v", err)
		}
		t.Cleanup(func() { _ = s.Close() })
		return s
	})
}

func TestFindingStoreFile(t *testing.T) {
	dir := t.TempDir()
	store, err := OpenFileStore(dir, time.Hour)
	if err != nil {
		t.Fatalf("OpenFileStore: %v", err)
	}
	t.Cleanup(func() { _ = store.Close() })
	testFindingStore(t, store, func(t *testing.T) FindingStore {
		t.Helper()
		s, err := OpenFileStore(dir, time.Hour)
		if err != nil {
			t.Fatalf("重开 FileStore: %v", err)
		}
		t.Cleanup(func() { _ = s.Close() })
		return s
	})
}

// TestFindingSplitTypes 校验类别串的解析边界（空串、多余逗号、空白）。
func TestFindingSplitTypes(t *testing.T) {
	cases := []struct {
		in   string
		want int
	}{
		{"", 0},
		{" , ", 0},
		{"id_card", 1},
		{"id_card,phone", 2},
		{"id_card, phone ,", 2},
	}
	for _, c := range cases {
		if got := splitTypes(c.in); len(got) != c.want {
			t.Fatalf("splitTypes(%q) = %v，期望 %d 项", c.in, got, c.want)
		}
	}
}
