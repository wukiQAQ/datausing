// SQLite 后端的 DLP 检测结果读写实现。
//
// 表结构与设计文档 8.2 一致：每条 finding 一行，只存脱敏副本；
// 时间戳沿用 flows 的口径（UnixNano 整数），便于和会话表按时间对齐。
package storage

import (
	"database/sql"
	"encoding/json"
	"net/netip"
	"strings"
	"time"
)

// dlpFindingSchema 是 dlp_findings 的建表与索引语句。
// 单独抽出来是为了让 OpenSQLiteStore 的 schema 列表保持"基础表"职责，
// DLP 相关表在启用 DLP 时也只是多几条 IF NOT EXISTS（不影响老库升级）。
var dlpFindingSchema = []string{
	`CREATE TABLE IF NOT EXISTS dlp_findings (
		id            INTEGER PRIMARY KEY AUTOINCREMENT,
		ts            INTEGER NOT NULL,
		src_ip        TEXT    NOT NULL,
		src_port      INTEGER NOT NULL,
		dst_ip        TEXT    NOT NULL,
		dst_port      INTEGER NOT NULL,
		proto         TEXT    NOT NULL,
		host          TEXT,
		path          TEXT,
		content_type  TEXT,
		level         TEXT    NOT NULL,
		score         REAL    NOT NULL,
		types         TEXT    NOT NULL,
		hits          INTEGER NOT NULL,
		masked        TEXT    NOT NULL,
		reasons       TEXT,
		model         TEXT,
		payload_bytes INTEGER NOT NULL,
		direct        INTEGER NOT NULL DEFAULT 0,
		degraded      INTEGER NOT NULL DEFAULT 0
	)`,
	`CREATE INDEX IF NOT EXISTS idx_dlp_ts       ON dlp_findings(ts)`,
	`CREATE INDEX IF NOT EXISTS idx_dlp_level_ts ON dlp_findings(level, ts)`,
	`CREATE INDEX IF NOT EXISTS idx_dlp_src      ON dlp_findings(src_ip)`,
	`CREATE INDEX IF NOT EXISTS idx_dlp_types    ON dlp_findings(types)`,
}

// ensureFindingSchema 幂等建表：老库启动时补上 DLP 相关表。
func ensureFindingSchema(db *sql.DB) error {
	for _, q := range dlpFindingSchema {
		if _, err := db.Exec(q); err != nil {
			return err
		}
	}
	return nil
}

// findingWhere 生成 findings 查询的 WHERE 子句与参数。
// IP 条件与 flowWhere 保持同一套写法（完整 IP 等值、前缀走区间），两条查询路径语义一致。
func findingWhere(f FindingFilter) (string, []any) {
	where := []string{"1=1"}
	args := []any{}
	if f.Level != "" {
		where = append(where, "level = ?")
		args = append(args, f.Level)
	}
	if f.Type != "" {
		// types 是逗号分隔串：两端补逗号后再 instr，避免 "id_card" 误匹配 "id_cards"。
		where = append(where, "instr(',' || types || ',', ?) > 0")
		args = append(args, ","+f.Type+",")
	}
	if f.Src != "" {
		if _, err := netip.ParseAddr(f.Src); err == nil {
			where = append(where, "(src_ip = ? OR dst_ip = ?)")
			args = append(args, f.Src, f.Src)
		} else if hi := prefixUpper(f.Src); hi != "" {
			where = append(where, "((src_ip >= ? AND src_ip < ?) OR (dst_ip >= ? AND dst_ip < ?))")
			args = append(args, f.Src, hi, f.Src, hi)
		}
	}
	if !f.From.IsZero() {
		where = append(where, "ts >= ?")
		args = append(args, f.From.UnixNano())
	}
	if !f.To.IsZero() {
		where = append(where, "ts <= ?")
		args = append(args, f.To.UnixNano())
	}
	return strings.Join(where, " AND "), args
}

// AppendFinding 写入一条检测结果，并回填自增 ID。
func (s *SQLiteStore) AppendFinding(f DLPFinding) error {
	res, err := s.db.Exec(
		`INSERT INTO dlp_findings(ts, src_ip, src_port, dst_ip, dst_port, proto, host, path,
			content_type, level, score, types, hits, masked, reasons, model, payload_bytes, direct, degraded)
		 VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		f.Ts.UnixNano(), f.SrcIP, f.SrcPort, f.DstIP, f.DstPort, f.Proto,
		nullableString(f.Host), nullableString(f.Path), nullableString(f.ContentType),
		strings.ToUpper(f.Level), f.Score, joinTypes(f.Types), f.Hits, f.Masked,
		joinReasons(f.Reasons), nullableString(f.Model), f.PayloadBytes, f.Direct, f.Degraded)
	if err != nil {
		return err
	}
	id, err := res.LastInsertId()
	if err == nil {
		f.ID = id
	}
	return nil
}

// nullableString 把空串写成 NULL：host/path/content_type/model 都可以缺省，
// 存 NULL 比存空串更能表达"没有这个信息"。
func nullableString(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// joinReasons 把多条解释编码成 JSON 数组字符串；空值存 NULL。
func joinReasons(reasons []string) any {
	if len(reasons) == 0 {
		return nil
	}
	b, err := json.Marshal(reasons)
	if err != nil {
		return nil
	}
	return string(b)
}

// splitReasons 解析 JSON 数组字符串，坏数据按空处理（不影响主流程）。
func splitReasons(s string) []string {
	if s == "" {
		return nil
	}
	var out []string
	if err := json.Unmarshal([]byte(s), &out); err != nil {
		return nil
	}
	return out
}

// findingColumns 是查询 findings 的固定列顺序。
const findingColumns = `id, ts, src_ip, src_port, dst_ip, dst_port, proto, host, path,
	content_type, level, score, types, hits, masked, reasons, model, payload_bytes, direct, degraded`

// scanFinding 把一行结果读进 DLPFinding。
func scanFinding(scan func(dest ...any) error) (DLPFinding, error) {
	var (
		f                 DLPFinding
		ts                int64
		host, path, ctype sql.NullString
		reasons, model    sql.NullString
		types             string
		direct, degraded  bool
	)
	err := scan(&f.ID, &ts, &f.SrcIP, &f.SrcPort, &f.DstIP, &f.DstPort, &f.Proto,
		&host, &path, &ctype, &f.Level, &f.Score, &types, &f.Hits, &f.Masked,
		&reasons, &model, &f.PayloadBytes, &direct, &degraded)
	if err != nil {
		return DLPFinding{}, err
	}
	f.Ts = time.Unix(0, ts).UTC()
	f.Host, f.Path, f.ContentType = host.String, path.String, ctype.String
	f.Types = splitTypes(types)
	f.Reasons = splitReasons(reasons.String)
	f.Model = model.String
	f.Direct, f.Degraded = direct, degraded
	return f, nil
}

// Finding 按 ID 查单条。
func (s *SQLiteStore) Finding(id int64) (DLPFinding, bool, error) {
	row := s.db.QueryRow(`SELECT `+findingColumns+` FROM dlp_findings WHERE id = ?`, id)
	f, err := scanFinding(row.Scan)
	if err == sql.ErrNoRows {
		return DLPFinding{}, false, nil
	}
	if err != nil {
		return DLPFinding{}, false, err
	}
	return f, true, nil
}

// QueryFindings 分页查询检测结果（新的在前）。
func (s *SQLiteStore) QueryFindings(f FindingFilter) ([]DLPFinding, int, error) {
	norm := normalizeFindingFilter(f)
	return s.queryFindings(norm, norm.Limit, norm.Offset)
}

// ExportFindings 不分页地取出全部结果（审计导出）。
func (s *SQLiteStore) ExportFindings(f FindingFilter) ([]DLPFinding, error) {
	items, _, err := s.queryFindings(normalizeFindingFilter(f), 0, 0)
	return items, err
}

// queryFindings 是分页查询的公共实现：limit<=0 表示不限条数。
func (s *SQLiteStore) queryFindings(f FindingFilter, limit, offset int) ([]DLPFinding, int, error) {
	cond, args := findingWhere(f)
	var total int
	if err := s.db.QueryRow(`SELECT COUNT(*) FROM dlp_findings WHERE `+cond, args...).Scan(&total); err != nil {
		return nil, 0, err
	}
	q := `SELECT ` + findingColumns + ` FROM dlp_findings WHERE ` + cond + ` ORDER BY ts DESC, id DESC`
	qargs := append([]any{}, args...) // 复制一份：后面追加 LIMIT 参数时不能污染 count 用的 args
	if limit > 0 {
		q += ` LIMIT ? OFFSET ?`
		qargs = append(qargs, limit, offset)
	}
	rows, err := s.db.Query(q, qargs...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items, err := scanFindingRows(rows)
	if err != nil {
		return nil, 0, err
	}
	return items, total, nil
}

// FindingStats 统计分级分布、类别 TopN 与时间线。
// 只取 ts/level/types 三列，聚合在 Go 里做：与文件后端共用 aggregateFindings，数字必然一致。
func (s *SQLiteStore) FindingStats(f FindingFilter, step time.Duration) (FindingStats, error) {
	norm := normalizeFindingFilter(f)
	cond, args := findingWhere(norm)
	rows, err := s.db.Query(`SELECT ts, level, types FROM dlp_findings WHERE `+cond+` ORDER BY ts`, args...)
	if err != nil {
		return FindingStats{}, err
	}
	defer rows.Close()
	items := make([]DLPFinding, 0, 64)
	for rows.Next() {
		var (
			ts    int64
			level string
			types string
		)
		if err := rows.Scan(&ts, &level, &types); err != nil {
			return FindingStats{}, err
		}
		items = append(items, DLPFinding{Ts: time.Unix(0, ts).UTC(), Level: level, Types: splitTypes(types)})
	}
	if err := rows.Err(); err != nil {
		return FindingStats{}, err
	}
	return aggregateFindings(items, norm.From, norm.To, step), nil
}

// scanFindingRows 把结果集读成切片（供 queryFindings 复用）。
func scanFindingRows(rows *sql.Rows) ([]DLPFinding, error) {
	items := make([]DLPFinding, 0, 32)
	for rows.Next() {
		f, err := scanFinding(rows.Scan)
		if err != nil {
			return nil, err
		}
		items = append(items, f)
	}
	return items, rows.Err()
}

// PruneFindings 删除早于 before 的检测结果（保留策略由 Backend.Prune 统一触发）。
func (s *SQLiteStore) PruneFindings(before time.Time) error {
	_, err := s.db.Exec(`DELETE FROM dlp_findings WHERE ts < ?`, before.UnixNano())
	return err
}
