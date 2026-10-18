package dlp

import "testing"

func TestScoreLevels(t *testing.T) {
	cases := []struct {
		name   string
		text   string
		want   Level
		direct bool
	}{
		{name: "空文本", text: "", want: LevelSafe},
		{name: "普通业务文本", text: "GET /api/v1/traffic/now HTTP/1.1", want: LevelSafe},
		{name: "仅 IP", text: "服务器 192.168.1.10", want: LevelSafe},
		{name: "仅邮箱", text: "邮箱 zhang.san@example.com", want: LevelLow},
		{name: "仅手机号", text: "电话 13800138000", want: LevelMid},
		{name: "仅医疗信息", text: "诊断：高血压", want: LevelMid},
		{name: "身份证直达高危", text: "身份证 11010519491231002X", want: LevelHigh, direct: true},
		{name: "银行卡直达高危", text: "卡号 4111111111111111", want: LevelHigh, direct: true},
		{name: "密钥直达高危", text: `{"api_key":"sk-ABCDEFGH12345678"}`, want: LevelHigh, direct: true},
		{name: "身份组合共现升级", text: "姓名：张三，电话：13800138000，地址：福建省福州市鼓楼区五四路100号", want: LevelHigh},
		{name: "两类共现", text: "电话 13800138000 邮箱 zhang.san@example.com", want: LevelMid},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			res := Analyze(c.text)
			if res.Level != c.want {
				t.Fatalf("Level=%s(score=%.3f)，want %s", res.Level, res.Score, c.want)
			}
			if res.Direct != c.direct {
				t.Errorf("Direct=%v，want %v", res.Direct, c.direct)
			}
			if res.Level != LevelSafe && (res.Score <= 0 || res.Score > 1) {
				t.Errorf("Score=%.3f 超出 (0,1] 区间", res.Score)
			}
		})
	}
}

func TestScoreSafeHasNoHits(t *testing.T) {
	res := Analyze("普通日志：连接已建立")
	if len(res.Hits) != 0 || len(res.Types) != 0 || res.Score != 0 {
		t.Fatalf("SAFE 结果应无命中、无类型、分数为 0，实际：%+v", res)
	}
}

func TestScoreHitsSortedAndNonOverlapping(t *testing.T) {
	res := Analyze("电话 13800138000，身份证 11010519491231002X，邮箱 a@b.com")
	for i := 1; i < len(res.Hits); i++ {
		if res.Hits[i].Start < res.Hits[i-1].End {
			t.Fatalf("命中区间重叠或乱序：%+v", res.Hits)
		}
	}
	if len(res.Types) != 3 {
		t.Fatalf("Types=%v，want 3 类", res.Types)
	}
}

func TestParseLevel(t *testing.T) {
	cases := map[string]Level{"L3": LevelHigh, "l2": LevelMid, "L1": LevelLow, "safe": LevelSafe, "": LevelSafe}
	for s, want := range cases {
		got, err := ParseLevel(s)
		if err != nil || got != want {
			t.Errorf("ParseLevel(%q)=(%v,%v)，want %v", s, got, err, want)
		}
	}
	if _, err := ParseLevel("L9"); err == nil {
		t.Error("ParseLevel 对非法级别应返回错误")
	}
}

func TestLevelStringAndLabel(t *testing.T) {
	if LevelHigh.String() != "L3" || LevelHigh.Label() != "高危" {
		t.Error("Level 展示文案不符合约定")
	}
	if LevelSafe.String() != "SAFE" || LevelSafe.Label() != "安全" {
		t.Error("SAFE 展示文案不符合约定")
	}
}
