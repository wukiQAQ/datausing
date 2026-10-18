package dlp

import "strings"

// Mask 按分级结论对文本做脱敏，返回脱敏后的文本。
//
// 四级模板（与需求一致）：
//   - L3 高危：整段值全屏蔽，替换为 [已屏蔽:类别]，避免任何信息泄漏；
//   - L2 中危：保留首字符 + 星号，保留可读线索但不暴露完整值；
//   - L1 低危：保留原文，前置 [低危] 警示标记，便于人工复核；
//   - SAFE：原文返回，不做任何改动。
//
// 文本中未被命中的部分原样保留，因此可直接用于"脱敏前后对照"展示。
func Mask(text string, res Result) string {
	if res.Level == LevelSafe || len(res.Hits) == 0 {
		return text
	}
	var b strings.Builder
	b.Grow(len(text))
	last := 0
	for _, h := range res.Hits {
		if h.Start < last || h.End > len(text) || h.Start >= h.End {
			continue // 防御：命中区间异常或与上一段重叠时跳过
		}
		b.WriteString(text[last:h.Start])
		b.WriteString(MaskValue(h.Value, h.Type, res.Level))
		last = h.End
	}
	b.WriteString(text[last:])
	return b.String()
}

// MaskValue 按级别脱敏单个值，供结构化字段（JSON/CSV 单元格）直接调用。
func MaskValue(v string, typ EntityType, lvl Level) string {
	if v == "" {
		return v
	}
	switch lvl {
	case LevelHigh:
		return "[已屏蔽:" + typ.Label() + "]"
	case LevelMid:
		return firstRune(v) + strings.Repeat("*", minInt(runeLen(v)-1, 6))
	case LevelLow:
		return "[低危]" + v
	default:
		return v
	}
}

// runeLen 返回 UTF-8 字符串的字符数（脱敏按字符而非字节计算，避免把汉字切坏）。
func runeLen(s string) int {
	return len([]rune(s))
}

// firstRune 返回首字符；空串返回空。
func firstRune(s string) string {
	for _, r := range s {
		return string(r)
	}
	return ""
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
