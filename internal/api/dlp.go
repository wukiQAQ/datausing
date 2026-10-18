// DLP 检测结果相关接口：findings 分页查询、单条详情、看板统计、审计导出。
//
// 契约见《设计文档》9.2。这里所有响应字段名都沿用 cmd/flowmask 的 JSON 契约，
// 前端/评测脚本可以同时消费离线 CLI 输出与在线接口，不用为两套字段写两份代码。
package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/wukiQAQ/datausing/internal/storage"
)

// dlpFindingItem 是 findings 的对外视图。
// 与 storage.DLPFinding 分开：存储层的字段是内部实现，对外的字段一旦发出去就是契约。
type dlpFindingItem struct {
	ID           int64    `json:"id"`
	Ts           string   `json:"ts"`
	SrcIP        string   `json:"src_ip"`
	SrcPort      uint16   `json:"src_port"`
	DstIP        string   `json:"dst_ip"`
	DstPort      uint16   `json:"dst_port"`
	Proto        string   `json:"proto"`
	Host         string   `json:"host,omitempty"`
	Path         string   `json:"path,omitempty"`
	ContentType  string   `json:"content_type,omitempty"`
	Level        string   `json:"level"`
	LevelLabel   string   `json:"level_label"`
	Score        float64  `json:"score"`
	Types        []string `json:"types"`
	Hits         int      `json:"hits"`
	Masked       string   `json:"masked"`
	Reasons      []string `json:"reasons,omitempty"`
	Model        string   `json:"model,omitempty"`
	PayloadBytes int      `json:"payload_bytes"`
	Direct       bool     `json:"direct,omitempty"`
	Degraded     bool     `json:"degraded,omitempty"`
}

// dlpLevelLabel 把级别转成中文展示名（与 internal/dlp 的 Label() 一致，此处避免引包循环）。
func dlpLevelLabel(level string) string {
	switch level {
	case "L3":
		return "高危"
	case "L2":
		return "中危"
	case "L1":
		return "低危"
	case "SAFE":
		return "安全"
	default:
		return level
	}
}

// validDLPLevel 校验级别参数；空串表示不过滤。
func validDLPLevel(level string) bool {
	switch level {
	case "", "L3", "L2", "L1", "SAFE":
		return true
	default:
		return false
	}
}

// findingItem 把存储层的 finding 转成对外视图。
func findingItem(f storage.DLPFinding) dlpFindingItem {
	level := strings.ToUpper(f.Level)
	return dlpFindingItem{
		ID: f.ID, Ts: f.Ts.Format(time.RFC3339),
		SrcIP: f.SrcIP, SrcPort: f.SrcPort, DstIP: f.DstIP, DstPort: f.DstPort,
		Proto: f.Proto, Host: f.Host, Path: f.Path, ContentType: f.ContentType,
		Level: level, LevelLabel: dlpLevelLabel(level), Score: f.Score,
		Types: f.Types, Hits: f.Hits, Masked: f.Masked, Reasons: f.Reasons,
		Model: f.Model, PayloadBytes: f.PayloadBytes, Direct: f.Direct, Degraded: f.Degraded,
	}
}

// dlpWindow 解析 since（回溯秒数）并返回查询窗口。
// 时间基准用 s.now()：离线回放时数据时间来自 pcap 时间戳，用墙钟会把数据整体切掉。
func (s *Server) dlpWindow(r *http.Request, defSince int) (from, to time.Time) {
	to = s.now()
	since := intParam(r, "since", defSince)
	if since < 0 {
		since = 0
	}
	if since > maxHistorySeconds {
		since = maxHistorySeconds
	}
	return to.Add(-time.Duration(since) * time.Second), to
}

// requireFindings 返回 findings 存储；未启用时直接写 503 并返回 nil。
func (s *Server) requireFindings(w http.ResponseWriter) storage.FindingStore {
	if s.findings == nil {
		httpError(w, http.StatusServiceUnavailable, "DLP 检测结果存储未启用")
		return nil
	}
	return s.findings
}

// handleDLPFindings 分页查询检测结果。
// 参数：since（默认 1h）、level、type、src、page、page_size。
func (s *Server) handleDLPFindings(w http.ResponseWriter, r *http.Request) {
	store := s.requireFindings(w)
	if store == nil {
		return
	}
	q := r.URL.Query()
	level := strings.ToUpper(q.Get("level"))
	if !validDLPLevel(level) {
		httpError(w, http.StatusBadRequest, "level 仅支持 L3 / L2 / L1 / SAFE")
		return
	}
	from, to := s.dlpWindow(r, 3600)
	page := intParam(r, "page", 1)
	if page < 1 {
		page = 1
	}
	pageSize := intParam(r, "page_size", 10)
	if pageSize < 1 {
		pageSize = 10
	}
	if pageSize > maxPageSize {
		pageSize = maxPageSize
	}
	filter := storage.FindingFilter{
		Level: level, Type: q.Get("type"), Src: q.Get("src"),
		From: from, To: to, Limit: pageSize, Offset: (page - 1) * pageSize,
	}
	rows, total, err := store.QueryFindings(filter)
	if err != nil {
		httpError(w, http.StatusInternalServerError, "查询检测结果失败: "+err.Error())
		return
	}
	items := make([]dlpFindingItem, 0, len(rows))
	for _, f := range rows {
		items = append(items, findingItem(f))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"total": total, "page": page, "page_size": pageSize,
		"from": from.Format(time.RFC3339), "to": to.Format(time.RFC3339),
		"items": items,
	})
}

// handleDLPFindingDetail 返回单条检测结果（命中明细 + 脱敏片段）。
func (s *Server) handleDLPFindingDetail(w http.ResponseWriter, r *http.Request) {
	store := s.requireFindings(w)
	if store == nil {
		return
	}
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	if err != nil || id <= 0 {
		httpError(w, http.StatusBadRequest, "id 必须是正整数")
		return
	}
	f, ok, err := store.Finding(id)
	if err != nil {
		httpError(w, http.StatusInternalServerError, "查询检测结果失败: "+err.Error())
		return
	}
	if !ok {
		httpError(w, http.StatusNotFound, "检测结果不存在")
		return
	}
	writeJSON(w, http.StatusOK, findingItem(f))
}

// handleDLPStats 看板统计：分级分布（饼图）、类别 TopN（柱状）、时间线（折线）。
// 参数：since（默认 1h）、step（时间线桶宽，秒，默认 60）。
func (s *Server) handleDLPStats(w http.ResponseWriter, r *http.Request) {
	store := s.requireFindings(w)
	if store == nil {
		return
	}
	from, to := s.dlpWindow(r, 3600)
	step := intParam(r, "step", 60)
	if step < 1 {
		step = 60
	}
	if step > 3600 {
		step = 3600
	}
	stats, err := store.FindingStats(storage.FindingFilter{From: from, To: to}, time.Duration(step)*time.Second)
	if err != nil {
		httpError(w, http.StatusInternalServerError, "统计检测结果失败: "+err.Error())
		return
	}
	timeline := make([]map[string]any, 0, len(stats.Timeline))
	for _, b := range stats.Timeline {
		timeline = append(timeline, map[string]any{
			"start":    b.Start.Format(time.RFC3339),
			"total":    b.Total,
			"by_level": b.ByLevel,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"total":    stats.Total,
		"from":     stats.From.Format(time.RFC3339),
		"to":       stats.To.Format(time.RFC3339),
		"step_s":   int(stats.Step / time.Second),
		"by_level": stats.ByLevel,
		"by_type":  stats.ByType,
		"timeline": timeline,
	})
}

// maxDLPExportRows 是单次导出的行数上限：避免一个请求把内存/连接打满。
const maxDLPExportRows = 10000

// handleDLPExport 审计日志导出（JSONL）：首行是元信息，其后一行一条 finding。
func (s *Server) handleDLPExport(w http.ResponseWriter, r *http.Request) {
	store := s.requireFindings(w)
	if store == nil {
		return
	}
	if format := r.URL.Query().Get("format"); format != "" && format != "jsonl" {
		httpError(w, http.StatusBadRequest, "format 仅支持 jsonl")
		return
	}
	from, to := s.dlpWindow(r, 86400)
	items, err := store.ExportFindings(storage.FindingFilter{From: from, To: to})
	if err != nil {
		httpError(w, http.StatusInternalServerError, "导出检测结果失败: "+err.Error())
		return
	}
	truncated := false
	if len(items) > maxDLPExportRows {
		items = items[:maxDLPExportRows]
		truncated = true
	}
	name := fmt.Sprintf("flowmask-findings-%s.jsonl", time.Now().UTC().Format("20060102-150405"))
	w.Header().Set("Content-Type", "application/x-ndjson; charset=utf-8")
	w.Header().Set("Content-Disposition", `attachment; filename="`+name+`"`)
	if truncated {
		w.Header().Set("X-FlowMask-Truncated", "true")
	}
	enc := json.NewEncoder(w)
	_ = enc.Encode(map[string]any{
		"type": "meta", "exported_at": time.Now().UTC().Format(time.RFC3339),
		"operator": "api", "total": len(items), "from": from.Format(time.RFC3339),
		"to": to.Format(time.RFC3339), "truncated": truncated,
	})
	for _, f := range items {
		if err := enc.Encode(findingItem(f)); err != nil {
			return // 客户端断开时不继续写
		}
	}
}
