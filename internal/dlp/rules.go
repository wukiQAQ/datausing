package dlp

import (
	"regexp"
	"strconv"
	"strings"
)

// rule 是 L1 规则层的一条检测规则。
//
// group 指定命中内容位于哪个子组：0 表示整个匹配；带锚定的键值型规则（如
// `地址: "..."`）只需取第 1 组的值，避免把键名也算进敏感内容。
// validate 为可选的校验函数，用于剔除"长得像但不对"的命中（身份证校验位、银行卡 Luhn）。
type rule struct {
	name       string
	typ        EntityType
	re         *regexp.Regexp
	group      int
	weight     float64
	confidence float64
	validate   func(string) bool
}

// rules 是 L1 规则表。权重体现"类别差异化赋权"：能直接定位到自然人的类别权重高，
// 泛化标识（如 IP）权重低，避免把普通流量判成高危。
var rules = []rule{
	{
		name:       "idcard18",
		typ:        TypeIDCard,
		re:         regexp.MustCompile(`\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b`),
		weight:     0.95,
		confidence: 0.98,
		validate:   validIDCard18,
	},
	{
		name:       "idcard15",
		typ:        TypeIDCard,
		re:         regexp.MustCompile(`\b[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}\b`),
		weight:     0.80,
		confidence: 0.70,
	},
	{
		name:       "mobile",
		typ:        TypePhone,
		re:         regexp.MustCompile(`\b1[3-9]\d{9}\b`),
		weight:     0.65,
		confidence: 0.95,
	},
	{
		name:       "bankcard",
		typ:        TypeBankCard,
		re:         regexp.MustCompile(`\b(?:62|4|5[1-5])\d{13,17}\b`),
		weight:     0.90,
		confidence: 0.92,
		validate:   validLuhn,
	},
	{
		name:       "email",
		typ:        TypeEmail,
		re:         regexp.MustCompile(`[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`),
		weight:     0.45,
		confidence: 0.90,
	},
	{
		name:       "ipv4",
		typ:        TypeIP,
		re:         regexp.MustCompile(`\b(?:\d{1,3}\.){3}\d{1,3}\b`),
		weight:     0.15,
		confidence: 0.80,
		validate:   validIPv4,
	},
	{
		// 键值型密钥：api_key=xxx / "password": "xxx" / token: xxx
		name:       "secret_kv",
		typ:        TypeSecret,
		re:         regexp.MustCompile(`(?i)(?:api[_-]?key|apikey|access[_-]?key|secret[_-]?key|secret|token|password|passwd|pwd|private[_-]?key)\s*["']?\s*[:=：]\s*["']?([A-Za-z0-9/+_\-=]{8,})`),
		group:      1,
		weight:     1.00,
		confidence: 0.95,
	},
	{
		name:       "jwt",
		typ:        TypeSecret,
		re:         regexp.MustCompile(`\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b`),
		weight:     1.00,
		confidence: 0.97,
	},
	{
		name:       "pem_private_key",
		typ:        TypeSecret,
		re:         regexp.MustCompile(`-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----`),
		weight:     1.00,
		confidence: 0.99,
	},
	{
		name:       "name_kv",
		typ:        TypeName,
		re:         regexp.MustCompile(`(?i)(?:姓\s*名|用户名|real[_-]?name|name)\s*["']?\s*[:=：]\s*["']?([\p{Han}]{2,4})`),
		group:      1,
		weight:     0.40,
		confidence: 0.75,
	},
	{
		name:       "address_kv",
		typ:        TypeAddress,
		re:         regexp.MustCompile(`(?i)(?:收货地址|详细地址|住址|地址|address)\s*["']?\s*[:=：]\s*["']?([^"',，。；;、}\s]{6,40})`),
		group:      1,
		weight:     0.50,
		confidence: 0.75,
	},
	{
		name:       "medical_kv",
		typ:        TypeMedical,
		re:         regexp.MustCompile(`(?i)(?:病历|诊断|疾病|过敏史|用药|血压|血糖|病史)\s*["']?\s*[:=：]\s*["']?([^"',，。；;、}\s]{2,40})`),
		group:      1,
		weight:     0.75,
		confidence: 0.80,
	},
}

// Detect 用 L1 规则扫描文本，返回按位置升序、互不重叠的命中列表。
func Detect(text string) []Hit {
	if text == "" {
		return nil
	}
	var hits []Hit
	for _, r := range rules {
		for _, m := range r.re.FindAllStringSubmatchIndex(text, -1) {
			start, end := m[0], m[1]
			if r.group > 0 {
				gi := r.group * 2
				if gi+1 >= len(m) || m[gi] < 0 {
					continue
				}
				start, end = m[gi], m[gi+1]
			}
			if start < 0 || end > len(text) || start >= end {
				continue
			}
			value := text[start:end]
			if r.validate != nil && !r.validate(value) {
				continue
			}
			hits = append(hits, Hit{
				Type:       r.typ,
				Start:      start,
				End:        end,
				Value:      value,
				Weight:     r.weight,
				Confidence: r.confidence,
				Rule:       r.name,
			})
		}
	}
	return dedupeOverlaps(hits)
}

// dedupeOverlaps 去掉重叠命中：同区间保留权重高者；部分重叠时保留先出现者。
// 例如 18 位身份证里不会再单独冒出一个 15 位身份证命中。
func dedupeOverlaps(hits []Hit) []Hit {
	if len(hits) < 2 {
		return hits
	}
	sortHits(hits)
	out := hits[:0]
	lastEnd := -1
	for _, h := range hits {
		if h.Start < lastEnd {
			continue
		}
		out = append(out, h)
		lastEnd = h.End
	}
	return out
}

// validIDCard18 校验 18 位身份证号的校验位（ISO 7064:1983 MOD 11-2）。
func validIDCard18(s string) bool {
	if len(s) != 18 {
		return false
	}
	weights := [17]int{7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2}
	const codes = "10X98765432"
	sum := 0
	for i := 0; i < 17; i++ {
		d, err := strconv.Atoi(string(s[i]))
		if err != nil {
			return false
		}
		sum += d * weights[i]
	}
	want := codes[sum%11]
	got := s[17]
	if got == 'x' {
		got = 'X'
	}
	return got == want
}

// validLuhn 用 Luhn 算法校验银行卡号，过滤随机数字串。
func validLuhn(s string) bool {
	if len(s) < 12 {
		return false
	}
	sum := 0
	double := false
	for i := len(s) - 1; i >= 0; i-- {
		c := s[i]
		if c < '0' || c > '9' {
			return false
		}
		d := int(c - '0')
		if double {
			d *= 2
			if d > 9 {
				d -= 9
			}
		}
		sum += d
		double = !double
	}
	return sum%10 == 0
}

// validIPv4 校验点分十进制每段取值范围。
func validIPv4(s string) bool {
	parts := strings.Split(s, ".")
	if len(parts) != 4 {
		return false
	}
	for _, p := range parts {
		if p == "" || len(p) > 3 {
			return false
		}
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 || n > 255 {
			return false
		}
	}
	return true
}
