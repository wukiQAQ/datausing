// DLP 检测结果（findings）的存储抽象与两种后端共用的聚合逻辑。
//
// 分工：识别层（internal/dlp）只负责"算"——从载荷里找出敏感片段并给出分级；
// 结果存哪里、怎么分页过滤、怎么出统计数字，是存储层的事。上层 API 只依赖
// FindingStore 接口，SQLite 与 JSONL 两种后端的分页/过滤/聚合语义必须完全一致，
// 否则换个存储后端看板数字就变了，演示时很难解释。
package storage

import (
	"sort"
	"strings"
	"time"
)

// DLPFinding 是一条敏感数据检测结果。
//
// 关键约束：只留脱敏副本（Masked）与结构化结论，**原始敏感值不入库**。
// 这是设计文档里对评委明确的口径，也是 DLP 产品最基本的自证清白。
type DLPFinding struct {
	ID           int64
	Ts           time.Time
	SrcIP        string
	SrcPort      uint16
	DstIP        string
	DstPort      uint16
	Proto        string
	Host         string
	Path         string
	ContentType  string
	Level        string // L3 / L2 / L1
	Score        float64
	Types        []string // 命中的敏感类别（id_card、phone…）
	Hits         int
	Masked       string   // 脱敏后的载荷片段（可直接展示给评审）
	Reasons      []string // L2/L3 的解释文本
	Model        string   // l1 / bert / qwen2.5-3b
	PayloadBytes int
	Direct       bool // 是否由"证件/卡/密钥"等极端类别直达 L3
	Degraded     bool // 推理服务不可用时是否降级为纯 L1 结果
}

// FindingFilter 是 findings 查询条件（零值表示不限制）。
// Src 支持两种写法（与会话查询一致）：完整 IP 精确匹配、其它按前缀匹配。
type FindingFilter struct {
	Level  string
	Type   string
	Src    string
	From   time.Time
	To     time.Time
	Limit  int
	Offset int
}

// FindingCount 是分组计数结果（分级分布 / 类别 TopN）。
type FindingCount struct {
	Key   string `json:"key"`
	Count int    `json:"count"`
}

// FindingBucket 是时间线上的一个桶（用于折线/柱状图）。
type FindingBucket struct {
	Start   time.Time      `json:"-"`
	Total   int            `json:"total"`
	ByLevel map[string]int `json:"by_level"`
}

// FindingStats 是 /api/v1/dlp/stats 的数据来源。
type FindingStats struct {
	Total    int
	ByLevel  []FindingCount // 固定 L3/L2/L1/SAFE 顺序，缺的补 0，前端不用判空
	ByType   []FindingCount // 按条数降序，最多 10 项
	Timeline []FindingBucket
	From     time.Time
	To       time.Time
	Step     time.Duration
}

// FindingStore 是 DLP 检测结果的读写接口，由 SQLiteStore / FileStore 分别实现。
type FindingStore interface {
	// AppendFinding 写入一条检测结果（ID 为零时由后端分配）。
	AppendFinding(f DLPFinding) error
	// Finding 按 ID 查单条，第二个返回值为 false 表示不存在。
	Finding(id int64) (DLPFinding, bool, error)
	// QueryFindings 分页查询，返回当前页与满足条件的总条数。
	QueryFindings(f FindingFilter) ([]DLPFinding, int, error)
	// ExportFindings 不分页地取出满足条件的全部结果（调用方需自行控制总量）。
	ExportFindings(f FindingFilter) ([]DLPFinding, error)
	// FindingStats 统计分级分布、类别 TopN 与时间线。
	FindingStats(f FindingFilter, step time.Duration) (FindingStats, error)
}

const (
	// findingRetention 是检测结果的保留时长：比 flows 的 24h 更长，
	// 因为审计与赛后复盘都需要更长的历史；默认 7 天，与 flows 分别配置。
	findingRetention = 7 * 24 * time.Hour
	// defaultFindingPageSize 是分页默认值；maxFindingPageSize 是存储层硬上界（API 另有 100 的上界）。
	defaultFindingPageSize = 20
	maxFindingPageSize     = 1000
	// maxTimelineBuckets 限制时间线桶数：since 很大时按秒切桶会返回超大数组。
	maxTimelineBuckets = 240
	// maxTypeTop 是类别 TopN 的展示条数。
	maxTypeTop = 10
)

// findingLevelOrder 是分级展示顺序：危险在前。
var findingLevelOrder = []string{"L3", "L2", "L1", "SAFE"}

// normalizeFindingFilter 统一过滤条件的写法（大小写、空白、分页边界），
// 保证两种后端拿到的是同一份条件，不会出现"文件后端不区分大小写、SQL 后端区分"这类偏差。
func normalizeFindingFilter(f FindingFilter) FindingFilter {
	f.Level = strings.ToUpper(strings.TrimSpace(f.Level))
	f.Type = strings.ToLower(strings.TrimSpace(f.Type))
	f.Src = strings.TrimSpace(f.Src)
	if f.Limit <= 0 {
		f.Limit = defaultFindingPageSize
	}
	if f.Limit > maxFindingPageSize {
		f.Limit = maxFindingPageSize
	}
	if f.Offset < 0 {
		f.Offset = 0
	}
	return f
}

// joinTypes 把类别切片拼成逗号分隔串（空切片存空串，不存 "[]"）。
func joinTypes(types []string) string {
	if len(types) == 0 {
		return ""
	}
	return strings.Join(types, ",")
}

// splitTypes 解析逗号分隔的类别串，忽略空项与首尾空白。
func splitTypes(s string) []string {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// hasType 判断 finding 是否命中某类别（忽略大小写，与 SQL 的 instr 语义一致）。
func hasType(f DLPFinding, typ string) bool {
	for _, t := range f.Types {
		if strings.EqualFold(t, typ) {
			return true
		}
	}
	return false
}

// matchFinding 判断一条 finding 是否满足过滤条件。
// 过滤语义必须与 SQL 版（findingWhere）逐条对应，否则两种后端查询结果不一致。
func matchFinding(f DLPFinding, filter FindingFilter) bool {
	if filter.Level != "" && !strings.EqualFold(f.Level, filter.Level) {
		return false
	}
	if filter.Type != "" && !hasType(f, filter.Type) {
		return false
	}
	if filter.Src != "" && !matchIP(f.SrcIP, filter.Src) && !matchIP(f.DstIP, filter.Src) {
		return false
	}
	if !filter.From.IsZero() && f.Ts.Before(filter.From) {
		return false
	}
	if !filter.To.IsZero() && f.Ts.After(filter.To) {
		return false
	}
	return true
}

// sortFindings 按时间倒序（同一时刻按 ID 倒序），与 SQL 的 ORDER BY ts DESC, id DESC 一致。
func sortFindings(items []DLPFinding) {
	sort.SliceStable(items, func(i, j int) bool {
		if !items[i].Ts.Equal(items[j].Ts) {
			return items[i].Ts.After(items[j].Ts)
		}
		return items[i].ID > items[j].ID
	})
}

// aggregateFindings 计算统计结果：两种后端的统计都走这里，保证数字完全一致。
// from/to 为零值时按数据自身的时间范围兜底；时间线桶从 from 开始按 step 切分。
func aggregateFindings(items []DLPFinding, from, to time.Time, step time.Duration) FindingStats {
	stats := FindingStats{
		Total:   len(items),
		ByLevel: make([]FindingCount, 0, len(findingLevelOrder)),
		ByType:  []FindingCount{},
	}
	levelCount := map[string]int{}
	typeCount := map[string]int{}
	for _, f := range items {
		lvl := strings.ToUpper(strings.TrimSpace(f.Level))
		levelCount[lvl]++
		for _, t := range f.Types {
			typeCount[t]++
		}
	}
	for _, lvl := range findingLevelOrder {
		stats.ByLevel = append(stats.ByLevel, FindingCount{Key: lvl, Count: levelCount[lvl]})
	}
	for k, v := range typeCount {
		stats.ByType = append(stats.ByType, FindingCount{Key: k, Count: v})
	}
	sort.Slice(stats.ByType, func(i, j int) bool {
		if stats.ByType[i].Count != stats.ByType[j].Count {
			return stats.ByType[i].Count > stats.ByType[j].Count
		}
		return stats.ByType[i].Key < stats.ByType[j].Key
	})
	if len(stats.ByType) > maxTypeTop {
		stats.ByType = stats.ByType[:maxTypeTop]
	}

	// 时间范围的兜底：调用方没给（或给反了）时用数据自身的最小/最大时间。
	if len(items) > 0 {
		minTs, maxTs := items[0].Ts, items[0].Ts
		for _, f := range items {
			if f.Ts.Before(minTs) {
				minTs = f.Ts
			}
			if f.Ts.After(maxTs) {
				maxTs = f.Ts
			}
		}
		if from.IsZero() {
			from = minTs
		}
		if to.IsZero() {
			to = maxTs
		}
	}
	if from.IsZero() || to.Before(from) {
		to = from
	}
	stats.From, stats.To = from, to

	if step <= 0 {
		step = time.Minute
	}
	span := to.Sub(from)
	if span > 0 {
		// 桶数过多时自动放大步长，保证返回体大小有界。
		if buckets := int(span/step) + 1; buckets > maxTimelineBuckets {
			step = span/maxTimelineBuckets + time.Nanosecond
		}
	}
	bucketCount := 1
	if span > 0 {
		bucketCount = int(span/step) + 1
	}
	stats.Step = step
	stats.Timeline = make([]FindingBucket, bucketCount)
	for i := range stats.Timeline {
		stats.Timeline[i] = FindingBucket{
			Start:   from.Add(time.Duration(i) * step),
			ByLevel: map[string]int{"L3": 0, "L2": 0, "L1": 0, "SAFE": 0},
		}
	}
	for _, f := range items {
		idx := 0
		if step > 0 && !from.IsZero() {
			idx = int(f.Ts.Sub(from) / step)
		}
		if idx < 0 {
			idx = 0
		}
		if idx >= bucketCount {
			idx = bucketCount - 1
		}
		lvl := strings.ToUpper(strings.TrimSpace(f.Level))
		stats.Timeline[idx].Total++
		stats.Timeline[idx].ByLevel[lvl]++
	}
	return stats
}
