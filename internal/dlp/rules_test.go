package dlp

import "testing"

// findType 返回文本中指定类别的命中值，便于断言"抓到了什么"。
func findType(hits []Hit, typ EntityType) []string {
	var out []string
	for _, h := range hits {
		if h.Type == typ {
			out = append(out, h.Value)
		}
	}
	return out
}

func TestDetectMobile(t *testing.T) {
	hits := Detect(`{"phone":"13800138000"}`)
	got := findType(hits, TypePhone)
	if len(got) != 1 || got[0] != "13800138000" {
		t.Fatalf("手机号命中=%v，want [13800138000]", got)
	}
}

func TestDetectIDCard18Valid(t *testing.T) {
	hits := Detect("身份证号 11010519491231002X")
	got := findType(hits, TypeIDCard)
	if len(got) != 1 || got[0] != "11010519491231002X" {
		t.Fatalf("身份证命中=%v，want [11010519491231002X]", got)
	}
	// 18 位身份证不应再冒出一个 15 位身份证的重复命中
	if len(hits) != 1 {
		t.Fatalf("命中 %d 条，want 1 条（重叠未去重）：%+v", len(hits), hits)
	}
}

func TestDetectIDCardBadChecksum(t *testing.T) {
	if hits := Detect("身份证号 110105194912310021"); len(hits) != 0 {
		t.Fatalf("校验位错误的身份证不应命中，实际：%+v", hits)
	}
}

func TestDetectBankCardLuhn(t *testing.T) {
	if hits := findType(Detect("卡号 4111111111111111"), TypeBankCard); len(hits) != 1 {
		t.Fatalf("合法银行卡未命中：%v", hits)
	}
	if hits := Detect("卡号 4111111111111112"); len(hits) != 0 {
		t.Fatalf("Luhn 校验失败的卡号不应命中，实际：%+v", hits)
	}
}

func TestDetectEmail(t *testing.T) {
	got := findType(Detect("联系 zhang.san@example.com 处理"), TypeEmail)
	if len(got) != 1 || got[0] != "zhang.san@example.com" {
		t.Fatalf("邮箱命中=%v", got)
	}
}

func TestDetectIPv4Validation(t *testing.T) {
	if got := findType(Detect("服务器 192.168.1.10"), TypeIP); len(got) != 1 {
		t.Fatalf("合法 IPv4 未命中：%v", got)
	}
	// 999.1.1.1 形似但不是合法地址，validate 应将其剔除
	// 说明：正则先匹配到 99.1.1.1 这类子串属于可接受行为，这里只验证不会整段报错
	if hits := Detect("版本号 999.1.1.1"); len(findType(hits, TypeIP)) > 0 {
		// 允许命中其中的合法子串，但不允许把 999 当首段
		for _, h := range findType(hits, TypeIP) {
			if h == "999.1.1.1" {
				t.Fatalf("非法 IPv4 被当作合法地址：%+v", hits)
			}
		}
	}
}

func TestDetectSecretKeyValue(t *testing.T) {
	hits := Detect(`{"api_key":"sk-ABCDEFGH12345678"}`)
	got := findType(hits, TypeSecret)
	if len(got) != 1 || got[0] != "sk-ABCDEFGH12345678" {
		t.Fatalf("密钥命中=%v，want [sk-ABCDEFGH12345678]", got)
	}
}

func TestDetectJWT(t *testing.T) {
	token := "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
	got := findType(Detect("Authorization: Bearer "+token), TypeSecret)
	if len(got) != 1 || got[0] != token {
		t.Fatalf("JWT 命中=%v", got)
	}
}

func TestDetectKeyValueAnchoredFields(t *testing.T) {
	text := `姓名：张三，地址：福建省福州市鼓楼区五四路100号，诊断：高血压`
	hits := Detect(text)
	if got := findType(hits, TypeName); len(got) != 1 || got[0] != "张三" {
		t.Errorf("姓名命中=%v", got)
	}
	if got := findType(hits, TypeAddress); len(got) != 1 {
		t.Errorf("地址命中=%v", got)
	}
	if got := findType(hits, TypeMedical); len(got) != 1 {
		t.Errorf("医疗命中=%v", got)
	}
}

func TestValidHelpers(t *testing.T) {
	if !validIDCard18("11010519491231002X") {
		t.Error("validIDCard18 应将 11010519491231002X 判为合法")
	}
	if validIDCard18("110105194912310021") {
		t.Error("validIDCard18 应将校验位错误的号码判为非法")
	}
	if !validLuhn("4111111111111111") {
		t.Error("validLuhn 应将 4111111111111111 判为合法")
	}
	if validLuhn("4111111111111112") {
		t.Error("validLuhn 应将 4111111111111112 判为非法")
	}
	if !validIPv4("192.168.1.10") || validIPv4("999.1.1.1") || validIPv4("1.2.3") {
		t.Error("validIPv4 校验结果不符合预期")
	}
}
