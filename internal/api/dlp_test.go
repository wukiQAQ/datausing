package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/wukiQAQ/datausing/internal/aggregator"
	"github.com/wukiQAQ/datausing/internal/config"
	"github.com/wukiQAQ/datausing/internal/flow"
	"github.com/wukiQAQ/datausing/internal/storage"
	"github.com/wukiQAQ/datausing/internal/webui"
)

// seedFindings 写入三条时间接近当前的三条检测结果，返回它们的 ID（按写入顺序）。
// 时间取 now-30s 起：既要落在接口默认 1h 窗口内，又不能超过 now（否则窗口右边界会切掉）。
func seedFindings(t *testing.T, store storage.FindingStore) []int64 {
	t.Helper()
	now := time.Now().UTC()
	samples := []storage.DLPFinding{
		{
			Ts: now.Add(-30 * time.Second), SrcIP: "10.0.0.5", SrcPort: 50001,
			DstIP: "203.0.113.10", DstPort: 80, Proto: "tcp", ContentType: "json",
			Level: "L3", Score: 0.958, Types: []string{"id_card", "phone"}, Hits: 3,
			Masked: `{"id_card":"[已屏蔽:身份证号]"}`, Model: "l1", PayloadBytes: 421, Direct: true,
		},
		{
			Ts: now.Add(-20 * time.Second), SrcIP: "10.0.0.7", SrcPort: 50002,
			DstIP: "203.0.113.20", DstPort: 8080, Proto: "tcp", ContentType: "form",
			Level: "L2", Score: 0.565, Types: []string{"email", "address"}, Hits: 2,
			Masked: "email=z******", Model: "l1", PayloadBytes: 233,
		},
		{
			Ts: now.Add(-10 * time.Second), SrcIP: "10.0.0.9", SrcPort: 50003,
			DstIP: "203.0.113.30", DstPort: 443, Proto: "tcp", ContentType: "text",
			Level: "L3", Score: 1.0, Types: []string{"secret"}, Hits: 1,
			Masked: "api_key=[已屏蔽:密钥凭据]", Reasons: []string{"api_key 出现在查询串中"},
			Model: "l1", PayloadBytes: 118, Degraded: true,
		},
	}
	ids := make([]int64, 0, len(samples))
	for _, f := range samples {
		if err := store.AppendFinding(f); err != nil {
			t.Fatalf("AppendFinding: %v", err)
		}
		items, _, err := store.QueryFindings(storage.FindingFilter{Src: f.SrcIP, Limit: 1})
		if err != nil || len(items) != 1 {
			t.Fatalf("回查刚写入的 finding 失败: %v", err)
		}
		ids = append(ids, items[0].ID)
	}
	return ids
}

// TestDLPFindingsAPI 校验分页查询的参数解析、过滤与字段契约。
func TestDLPFindingsAPI(t *testing.T) {
	ts, store := newTestServer(t)
	ids := seedFindings(t, store)
	if len(ids) != 3 {
		t.Fatalf("种子数据写入失败")
	}

	var list struct {
		Total    int              `json:"total"`
		Page     int              `json:"page"`
		PageSize int              `json:"page_size"`
		Items    []dlpFindingItem `json:"items"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/findings", &list)
	if list.Total != 3 || len(list.Items) != 3 {
		t.Fatalf("默认查询 total=%d len=%d", list.Total, len(list.Items))
	}
	// 新的在前：10.0.0.9 那条最先写入但时间最晚
	if list.Items[0].SrcIP != "10.0.0.9" {
		t.Fatalf("期望最新记录在首位，实际 %s", list.Items[0].SrcIP)
	}
	if list.Items[0].LevelLabel != "高危" || list.Items[0].Level != "L3" {
		t.Fatalf("级别/中文名不符: %+v", list.Items[0])
	}
	if list.Items[0].Masked == "" || !list.Items[0].Degraded || len(list.Items[0].Reasons) != 1 {
		t.Fatalf("脱敏片段/reasons/degraded 缺失: %+v", list.Items[0])
	}
	if list.Items[0].Ts == "" || !strings.Contains(list.Items[0].Ts, "T") {
		t.Fatalf("ts 应输出 RFC3339，实际 %q", list.Items[0].Ts)
	}
	if list.PageSize != 10 || list.Page != 1 {
		t.Fatalf("默认分页不符: page=%d page_size=%d", list.Page, list.PageSize)
	}

	// 分级过滤（大小写不敏感）
	var l3 struct {
		Total int              `json:"total"`
		Items []dlpFindingItem `json:"items"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/findings?level=l3", &l3)
	if l3.Total != 2 {
		t.Fatalf("level=l3 total=%d", l3.Total)
	}
	// 类别过滤
	var phone struct {
		Total int `json:"total"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/findings?type=phone", &phone)
	if phone.Total != 1 {
		t.Fatalf("type=phone total=%d", phone.Total)
	}
	// 来源过滤（前缀）
	var bySrc struct {
		Total int `json:"total"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/findings?src=10.0.0.", &bySrc)
	if bySrc.Total != 3 {
		t.Fatalf("src 前缀 total=%d", bySrc.Total)
	}
	// 分页上界：page_size=1000 被夹到 100
	var clamped struct {
		PageSize int `json:"page_size"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/findings?page_size=1000", &clamped)
	if clamped.PageSize != maxPageSize {
		t.Fatalf("page_size 未夹紧: %d", clamped.PageSize)
	}
	// 非法级别返回 400
	r, err := http.Get(ts.URL + "/api/v1/dlp/findings?level=L9")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	r.Body.Close()
	if r.StatusCode != http.StatusBadRequest {
		t.Fatalf("非法 level 应返回 400，实际 %d", r.StatusCode)
	}
}

// TestDLPFindingDetailAPI 校验单条详情与 404/400 分支。
func TestDLPFindingDetailAPI(t *testing.T) {
	ts, store := newTestServer(t)
	ids := seedFindings(t, store)

	var one dlpFindingItem
	getJSON(t, ts.URL+"/api/v1/dlp/findings/"+itoa(ids[2]), &one)
	if one.ID != ids[2] || one.Types[0] != "secret" || one.Masked == "" {
		t.Fatalf("详情内容不符: %+v", one)
	}

	r, err := http.Get(ts.URL + "/api/v1/dlp/findings/999999")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	r.Body.Close()
	if r.StatusCode != http.StatusNotFound {
		t.Fatalf("不存在的 ID 应返回 404，实际 %d", r.StatusCode)
	}

	r, err = http.Get(ts.URL + "/api/v1/dlp/findings/abc")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	r.Body.Close()
	if r.StatusCode != http.StatusBadRequest {
		t.Fatalf("非法 ID 应返回 400，实际 %d", r.StatusCode)
	}
}

// TestDLPStatsAPI 校验看板统计的结构与合计一致性。
func TestDLPStatsAPI(t *testing.T) {
	ts, store := newTestServer(t)
	seedFindings(t, store)

	var stats struct {
		Total    int                    `json:"total"`
		StepS    int                    `json:"step_s"`
		ByLevel  []storage.FindingCount `json:"by_level"`
		ByType   []storage.FindingCount `json:"by_type"`
		Timeline []map[string]any       `json:"timeline"`
		From     string                 `json:"from"`
		To       string                 `json:"to"`
	}
	getJSON(t, ts.URL+"/api/v1/dlp/stats?since=3600&step=60", &stats)
	if stats.Total != 3 || stats.StepS != 60 {
		t.Fatalf("统计 total=%d step_s=%d", stats.Total, stats.StepS)
	}
	byLevel := map[string]int{}
	for _, c := range stats.ByLevel {
		byLevel[c.Key] = c.Count
	}
	if byLevel["L3"] != 2 || byLevel["L2"] != 1 || len(stats.ByLevel) != 4 {
		t.Fatalf("分级分布不符: %+v", stats.ByLevel)
	}
	byType := map[string]int{}
	for _, c := range stats.ByType {
		byType[c.Key] = c.Count
	}
	if byType["phone"] != 1 || byType["secret"] != 1 {
		t.Fatalf("类别统计不符: %+v", stats.ByType)
	}
	if len(stats.Timeline) != 61 {
		t.Fatalf("1h/60s 应有 61 个桶，实际 %d", len(stats.Timeline))
	}
	sum := 0
	for _, b := range stats.Timeline {
		if n, ok := b["total"].(float64); ok {
			sum += int(n)
		}
	}
	if sum != stats.Total {
		t.Fatalf("时间线合计 %d != 总数 %d", sum, stats.Total)
	}
	if stats.From == "" || stats.To == "" {
		t.Fatalf("from/to 缺失: %+v", stats)
	}
}

// TestDLPExportAPI 校验审计导出的 JSONL 结构（首行 meta + 一行一条）。
func TestDLPExportAPI(t *testing.T) {
	ts, store := newTestServer(t)
	seedFindings(t, store)

	r, err := http.Get(ts.URL + "/api/v1/dlp/export?format=jsonl&since=3600")
	if err != nil {
		t.Fatalf("GET: %v", err)
	}
	defer r.Body.Close()
	if r.StatusCode != http.StatusOK {
		t.Fatalf("导出状态码 %d", r.StatusCode)
	}
	if ct := r.Header.Get("Content-Type"); !strings.Contains(ct, "ndjson") {
		t.Fatalf("Content-Type 不符: %q", ct)
	}
	if cd := r.Header.Get("Content-Disposition"); !strings.Contains(cd, "attachment") {
		t.Fatalf("缺少下载头: %q", cd)
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		t.Fatalf("读响应: %v", err)
	}
	lines := strings.Split(strings.TrimSpace(string(body)), "\n")
	if len(lines) != 4 {
		t.Fatalf("期望 1 行 meta + 3 行数据，实际 %d 行", len(lines))
	}
	var meta map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &meta); err != nil {
		t.Fatalf("meta 行不是 JSON: %v", err)
	}
	if meta["type"] != "meta" || meta["operator"] != "api" {
		t.Fatalf("meta 内容不符: %+v", meta)
	}
	if total, _ := meta["total"].(float64); int(total) != 3 {
		t.Fatalf("meta.total=%v", meta["total"])
	}
	// 数据行里必须只有脱敏副本，不能出现原始敏感值
	for _, line := range lines[1:] {
		var f dlpFindingItem
		if err := json.Unmarshal([]byte(line), &f); err != nil {
			t.Fatalf("数据行不是 JSON: %v", err)
		}
		if f.Masked == "" || strings.Contains(f.Masked, "11010519491231002X") {
			t.Fatalf("导出行不含脱敏副本或泄露原文: %+v", f)
		}
	}
}

// plainBackend 只实现 Backend、不实现 FindingStore，用于验证"后端不支持"时的 503 分支。
type plainBackend struct{ storage.Backend }

// TestDLPDisabled 校验存储后端不支持 findings 时接口返回 503、能力位为 false。
func TestDLPDisabled(t *testing.T) {
	cfg := config.Default()
	agg := aggregator.New(10)
	table := flow.NewTable(time.Minute, 5*time.Minute, nil)
	srv := New(cfg, agg, table, plainBackend{}, webui.FS)
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)

	for _, path := range []string{
		"/api/v1/dlp/findings",
		"/api/v1/dlp/findings/1",
		"/api/v1/dlp/stats",
		"/api/v1/dlp/export",
	} {
		r, err := http.Get(ts.URL + path)
		if err != nil {
			t.Fatalf("GET %s: %v", path, err)
		}
		r.Body.Close()
		if r.StatusCode != http.StatusServiceUnavailable {
			t.Fatalf("%s 应返回 503，实际 %d", path, r.StatusCode)
		}
	}

	var now struct {
		DLP bool `json:"dlp"`
	}
	getJSON(t, ts.URL+"/api/v1/traffic/now", &now)
	if now.DLP {
		t.Fatalf("不支持 findings 的后端不应上报 dlp=true")
	}
}

// itoa 是测试里的小工具，避免为了一处转换引入 strconv。
func itoa(v int64) string {
	if v == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for v > 0 {
		i--
		b[i] = byte('0' + v%10)
		v /= 10
	}
	return string(b[i:])
}
