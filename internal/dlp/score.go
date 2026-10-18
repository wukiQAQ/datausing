package dlp

import "math"

// 评分阈值：把 0~1 的敏感度分数映射到四级（数值越高越敏感）。
const (
	thresholdHigh = 0.85
	thresholdMid  = 0.55
	thresholdLow  = 0.25
)

// 共现修正参数：多类别同时出现意味着"能组合定位到自然人"，
// 单纯相加会放大误报，因此用递减的固定步长并设上限。
const (
	coOccurStepPerType = 0.06 // 每多一个类别 +0.06
	coOccurCap         = 0.18 // 通用共现修正上限
	identityBonusTwo   = 0.10 // 命中 2 个身份类字段的额外修正
	identityBonusThree = 0.15 // 命中 3 个及以上身份类字段的额外修正
)

// directTypes 是"命中即高危"的类别（需求中的极端阈值直达）：
// 证件号、银行卡号、密钥凭据一旦出现，无论其他类别如何都必须按 L3 处理。
var directTypes = map[EntityType]bool{
	TypeIDCard:   true,
	TypeBankCard: true,
	TypeSecret:   true,
}

// identityTypes 是能单独定位到自然人的身份类字段，用于共现修正。
var identityTypes = map[EntityType]bool{
	TypeName:    true,
	TypePhone:   true,
	TypeAddress: true,
	TypeEmail:   true,
	TypeIDCard:  true,
}

// Analyze 一步完成 L1 识别 + 评分分级。
func Analyze(text string) Result {
	return Score(Detect(text))
}

// Score 对命中列表评分并映射级别。
//
// 算法（与需求一致）：
//  1. 类别差异化赋权取加权最大：score = max(weight × confidence)；
//  2. 多标签共现修正：通用步长 + 身份类字段数量加成，总体上限 1；
//  3. 极端阈值直达：证件/银行卡/密钥命中即 L3，不再受阈值影响；
//  4. 阈值映射：≥0.85 L3、≥0.55 L2、≥0.25 L1、其余 SAFE。
func Score(hits []Hit) Result {
	var res Result
	if len(hits) == 0 {
		return res
	}
	sorted := make([]Hit, len(hits))
	copy(sorted, hits)
	sortHits(sorted)
	res.Hits = sorted
	res.Types = distinctTypes(sorted)

	base := 0.0
	for _, h := range sorted {
		if s := h.Weight * h.Confidence; s > base {
			base = s
		}
	}

	bonus := 0.0
	if n := len(res.Types); n > 1 {
		bonus = math.Min(coOccurStepPerType*float64(n-1), coOccurCap)
	}
	identityCount := 0
	for _, t := range res.Types {
		if identityTypes[t] {
			identityCount++
		}
	}
	switch {
	case identityCount >= 3:
		bonus += identityBonusThree
	case identityCount == 2:
		bonus += identityBonusTwo
	}

	score := math.Min(base+bonus, 1)
	res.Score = math.Round(score*1000) / 1000

	for _, h := range sorted {
		if directTypes[h.Type] && h.Confidence >= 0.9 {
			res.Direct = true
			break
		}
	}

	switch {
	case res.Direct || res.Score >= thresholdHigh:
		res.Level = LevelHigh
	case res.Score >= thresholdMid:
		res.Level = LevelMid
	case res.Score >= thresholdLow:
		res.Level = LevelLow
	default:
		res.Level = LevelSafe
	}
	return res
}
