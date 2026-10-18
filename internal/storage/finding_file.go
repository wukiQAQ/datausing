// 文件后端（JSONL）的 DLP 检测结果实现。
//
// 与 metrics/flows 一致：内存索引 + 追加写盘 + 保留策略重写。
// 单独一个 findings.jsonl：检测结果的保留期（7 天）比流量（24h）长，
// 混在一个文件里清理时会互相牵制。
package storage

import (
	"bufio"
	"os"
	"sort"
	"time"

	"encoding/json"
)

// findingLine 是 findings.jsonl 的一行（字段名与 API 契约保持一致，便于直接 grep/审计）。
type findingLine struct {
	ID           int64    `json:"id"`
	Ts           int64    `json:"ts"`
	SrcIP        string   `json:"src_ip"`
	SrcPort      uint16   `json:"src_port"`
	DstIP        string   `json:"dst_ip"`
	DstPort      uint16   `json:"dst_port"`
	Proto        string   `json:"proto"`
	Host         string   `json:"host,omitempty"`
	Path         string   `json:"path,omitempty"`
	ContentType  string   `json:"content_type,omitempty"`
	Level        string   `json:"level"`
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

func findingLineFrom(f DLPFinding) findingLine {
	return findingLine{
		ID: f.ID, Ts: f.Ts.UnixNano(),
		SrcIP: f.SrcIP, SrcPort: f.SrcPort, DstIP: f.DstIP, DstPort: f.DstPort,
		Proto: f.Proto, Host: f.Host, Path: f.Path, ContentType: f.ContentType,
		Level: f.Level, Score: f.Score, Types: f.Types, Hits: f.Hits,
		Masked: f.Masked, Reasons: f.Reasons, Model: f.Model,
		PayloadBytes: f.PayloadBytes, Direct: f.Direct, Degraded: f.Degraded,
	}
}

func (l findingLine) finding() DLPFinding {
	return DLPFinding{
		ID: l.ID, Ts: time.Unix(0, l.Ts).UTC(),
		SrcIP: l.SrcIP, SrcPort: l.SrcPort, DstIP: l.DstIP, DstPort: l.DstPort,
		Proto: l.Proto, Host: l.Host, Path: l.Path, ContentType: l.ContentType,
		Level: l.Level, Score: l.Score, Types: l.Types, Hits: l.Hits,
		Masked: l.Masked, Reasons: l.Reasons, Model: l.Model,
		PayloadBytes: l.PayloadBytes, Direct: l.Direct, Degraded: l.Degraded,
	}
}

// loadFindings 读取 findings.jsonl，并据此初始化自增 ID（取现有最大值 +1）。
func (s *FileStore) loadFindings(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1024*1024), 8*1024*1024)
	var maxID int64
	for sc.Scan() {
		var l findingLine
		if json.Unmarshal(sc.Bytes(), &l) != nil {
			continue // 容忍损坏行
		}
		rec := l.finding()
		s.findings = append(s.findings, rec)
		if rec.ID > maxID {
			maxID = rec.ID
		}
	}
	s.findingID = maxID
	return sc.Err()
}

// nextID 分配一个自增 ID（调用方需持有锁）。
func (s *FileStore) nextID() int64 {
	s.findingID++
	return s.findingID
}

// AppendFinding 追加一条检测结果并落盘。
func (s *FileStore) AppendFinding(f DLPFinding) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if f.ID <= 0 {
		f.ID = s.nextID()
	} else if f.ID > s.findingID {
		s.findingID = f.ID
	}
	s.findings = append(s.findings, f)
	return writeLine(s.df, findingLineFrom(f))
}

// Finding 按 ID 查单条。
func (s *FileStore) Finding(id int64) (DLPFinding, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, f := range s.findings {
		if f.ID == id {
			return f, true, nil
		}
	}
	return DLPFinding{}, false, nil
}

// QueryFindings 分页查询（新的在前）。
func (s *FileStore) QueryFindings(filter FindingFilter) ([]DLPFinding, int, error) {
	norm := normalizeFindingFilter(filter)
	items, err := s.filterFindings(norm)
	if err != nil {
		return nil, 0, err
	}
	total := len(items)
	if norm.Offset >= total {
		return []DLPFinding{}, total, nil
	}
	end := norm.Offset + norm.Limit
	if end > total {
		end = total
	}
	return items[norm.Offset:end], total, nil
}

// ExportFindings 不分页地取出全部结果（审计导出）。
func (s *FileStore) ExportFindings(filter FindingFilter) ([]DLPFinding, error) {
	return s.filterFindings(normalizeFindingFilter(filter))
}

// filterFindings 过滤 + 按时间倒序排序。
func (s *FileStore) filterFindings(filter FindingFilter) ([]DLPFinding, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]DLPFinding, 0, len(s.findings))
	for _, f := range s.findings {
		if matchFinding(f, filter) {
			out = append(out, f)
		}
	}
	sortFindings(out)
	return out, nil
}

// FindingStats 统计分级分布、类别 TopN 与时间线。
// 与 SQLite 后端共用 aggregateFindings：同一份数据两种后端给出同样的数字。
func (s *FileStore) FindingStats(filter FindingFilter, step time.Duration) (FindingStats, error) {
	norm := normalizeFindingFilter(filter)
	items, err := s.filterFindings(norm)
	if err != nil {
		return FindingStats{}, err
	}
	return aggregateFindings(items, norm.From, norm.To, step), nil
}

// 编译期断言：两种后端都必须实现 FindingStore。
var (
	_ FindingStore = (*FileStore)(nil)
	_ FindingStore = (*SQLiteStore)(nil)
)

// sortFindingsByTsAsc 供测试与调试使用（导出 JSONL 时按时间正序更好读）。
func sortFindingsByTsAsc(items []DLPFinding) {
	sort.SliceStable(items, func(i, j int) bool {
		if !items[i].Ts.Equal(items[j].Ts) {
			return items[i].Ts.Before(items[j].Ts)
		}
		return items[i].ID < items[j].ID
	})
}
