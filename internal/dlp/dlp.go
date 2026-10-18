// Package dlp 提供内容级敏感信息识别、敏感度评分分级与分级脱敏。
//
// 它是 FlowMask 作品的核心识别层，与采集/存储完全解耦：
// 输入是已还原的载荷文本，输出是一条可落库的 finding。
// 识别策略按需求分三级，本包先实现 L1（规则层），L2/L3 通过 HTTP 调 Python 推理服务
// （见 internal/dlp 的 client，后续提交）。
package dlp

import (
	"fmt"
	"sort"
	"strings"
)

// Level 是需求中的四级敏感度：L3 高危 / L2 中危 / L1 低危 / SAFE。
// 数值越大越敏感，便于直接比较。
type Level int

const (
	LevelSafe Level = iota // SAFE：无命中，原文放行
	LevelLow               // L1：低危，前置警示
	LevelMid               // L2：中危，保留首字符 + 星号
	LevelHigh              // L3：高危，全屏蔽
)

// String 返回展示用的级别名（与需求文档一致）。
func (l Level) String() string {
	switch l {
	case LevelHigh:
		return "L3"
	case LevelMid:
		return "L2"
	case LevelLow:
		return "L1"
	default:
		return "SAFE"
	}
}

// Label 返回中文级别名，用于报告与前端展示。
func (l Level) Label() string {
	switch l {
	case LevelHigh:
		return "高危"
	case LevelMid:
		return "中危"
	case LevelLow:
		return "低危"
	default:
		return "安全"
	}
}

// ParseLevel 解析级别字符串（大小写不敏感），用于配置文件与接口参数。
func ParseLevel(s string) (Level, error) {
	switch strings.ToUpper(strings.TrimSpace(s)) {
	case "L3", "HIGH":
		return LevelHigh, nil
	case "L2", "MID":
		return LevelMid, nil
	case "L1", "LOW":
		return LevelLow, nil
	case "SAFE", "":
		return LevelSafe, nil
	default:
		return LevelSafe, fmt.Errorf("未知的敏感级别: %q", s)
	}
}

// EntityType 是敏感信息类别。取值与 Go↔Python 契约、数据库持久化字段保持一致。
type EntityType string

const (
	TypePhone    EntityType = "phone"     // 手机号
	TypeIDCard   EntityType = "id_card"   // 身份证号
	TypeBankCard EntityType = "bank_card" // 银行卡号
	TypeEmail    EntityType = "email"     // 邮箱
	TypeIP       EntityType = "ip"        // IP 地址
	TypeSecret   EntityType = "secret"    // 密钥/凭据（API Key、JWT、私钥）
	TypePassword EntityType = "password"  // 口令字段
	TypeName     EntityType = "name"      // 姓名
	TypeAddress  EntityType = "address"   // 地址
	TypeMedical  EntityType = "medical"   // 医疗健康信息
)

// typeLabels 是类别的中文名，用于脱敏模板与前端展示。
var typeLabels = map[EntityType]string{
	TypePhone:    "手机号",
	TypeIDCard:   "身份证号",
	TypeBankCard: "银行卡号",
	TypeEmail:    "邮箱",
	TypeIP:       "IP地址",
	TypeSecret:   "密钥凭据",
	TypePassword: "密码",
	TypeName:     "姓名",
	TypeAddress:  "地址",
	TypeMedical:  "医疗信息",
}

// Label 返回类别中文名；未登记的类别回退为英文标识。
func (t EntityType) Label() string {
	if s, ok := typeLabels[t]; ok {
		return s
	}
	return string(t)
}

// ParseEntityType 把中文名或英文标识还原成 EntityType。
// 展示层用中文（"身份证号"）、存储与接口层用英文键（"id_card"），两者在这里互转。
func ParseEntityType(s string) (EntityType, bool) {
	v := EntityType(strings.TrimSpace(s))
	if _, ok := typeLabels[v]; ok {
		return v, true
	}
	for typ, label := range typeLabels {
		if label == string(v) {
			return typ, true
		}
	}
	return "", false
}

// Hit 是一次敏感信息命中。Start/End 是在原始文本中的字节下标，Value 为命中内容。
type Hit struct {
	Type       EntityType
	Start      int
	End        int
	Value      string
	Weight     float64 // 类别基础权重（0~1）
	Confidence float64 // 规则置信度（0~1），由正则精度与校验算法决定
	Rule       string  // 命中的规则名，便于排障与消融实验
}

// Result 是一次"识别 + 评分"的完整结论。
type Result struct {
	Hits   []Hit        // 按 Start 升序、互不重叠
	Score  float64      // 0~1，加权最大 + 共现修正后的敏感度
	Level  Level        // 映射后的四级
	Types  []EntityType // 命中类别（去重，按首次出现顺序）
	Direct bool         // 是否由极端阈值直达高危及（如身份证/密钥）
}

// TypeNames 返回命中类别的中文名列表，供展示使用。
func (r Result) TypeNames() []string {
	names := make([]string, 0, len(r.Types))
	for _, t := range r.Types {
		names = append(names, t.Label())
	}
	return names
}

// sortHits 按起点升序、同起点权重高者优先排序。
// 两级调用（Detect 去重叠前、Mask 遍历前）共用，保证结果稳定。
func sortHits(hits []Hit) {
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].Start != hits[j].Start {
			return hits[i].Start < hits[j].Start
		}
		return hits[i].Weight*hits[i].Confidence > hits[j].Weight*hits[j].Confidence
	})
}

// distinctTypes 按命中出现顺序返回去重后的类别列表。
func distinctTypes(hits []Hit) []EntityType {
	seen := make(map[EntityType]bool, len(hits))
	out := make([]EntityType, 0, len(hits))
	for _, h := range hits {
		if seen[h.Type] {
			continue
		}
		seen[h.Type] = true
		out = append(out, h.Type)
	}
	return out
}
