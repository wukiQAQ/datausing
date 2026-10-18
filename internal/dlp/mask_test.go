package dlp

import "testing"

func TestMaskLevels(t *testing.T) {
	cases := []struct {
		name string
		text string
		want string
	}{
		{
			name: "SAFE 原文返回",
			text: "普通业务文本",
			want: "普通业务文本",
		},
		{
			name: "L2 保留首字符加星号",
			text: "电话 13800138000",
			want: "电话 1******",
		},
		{
			name: "L1 前置警示保留原文",
			text: "邮箱 zhang.san@example.com",
			want: "邮箱 [低危]zhang.san@example.com",
		},
		{
			name: "L3 多类全屏蔽",
			text: "电话 13800138000，身份证 11010519491231002X",
			want: "电话 [已屏蔽:手机号]，身份证 [已屏蔽:身份证号]",
		},
		{
			name: "L3 密钥值屏蔽但保留键名",
			text: `{"api_key":"sk-ABCDEFGH12345678"}`,
			want: `{"api_key":"[已屏蔽:密钥凭据]"}`,
		},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := Mask(c.text, Analyze(c.text))
			if got != c.want {
				t.Fatalf("Mask=%q，want %q", got, c.want)
			}
		})
	}
}

func TestMaskValueDirect(t *testing.T) {
	if got := MaskValue("13800138000", TypePhone, LevelHigh); got != "[已屏蔽:手机号]" {
		t.Errorf("L3 MaskValue=%q", got)
	}
	if got := MaskValue("13800138000", TypePhone, LevelMid); got != "1******" {
		t.Errorf("L2 MaskValue=%q，want 1******", got)
	}
	if got := MaskValue("张三", TypeName, LevelLow); got != "[低危]张三" {
		t.Errorf("L1 MaskValue=%q", got)
	}
	if got := MaskValue("原文", TypeName, LevelSafe); got != "原文" {
		t.Errorf("SAFE MaskValue=%q", got)
	}
	if got := MaskValue("", TypeName, LevelHigh); got != "" {
		t.Errorf("空值应原样返回，实际 %q", got)
	}
}

// TestMaskKeepsSurroundingText 保证脱敏只改命中片段，前后文（含中文多字节字符）不被破坏。
func TestMaskKeepsSurroundingText(t *testing.T) {
	text := "请求体：{\"phone\":\"13800138000\",\"note\":\"请尽快联系\"}"
	got := Mask(text, Analyze(text))
	want := "请求体：{\"phone\":\"1******\",\"note\":\"请尽快联系\"}"
	if got != want {
		t.Fatalf("Mask=%q，want %q", got, want)
	}
}

// TestMaskIdempotentOnSafe 保证对 SAFE 文本重复脱敏不会改变内容。
func TestMaskIdempotentOnSafe(t *testing.T) {
	text := "GET /index.html HTTP/1.1"
	once := Mask(text, Analyze(text))
	if once != text {
		t.Fatalf("SAFE 文本被修改：%q", once)
	}
}
