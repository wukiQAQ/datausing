package dlp

import "testing"

// TestParseEntityType 校验中文名/英文键都能解析回同一个类别，未知值必须返回 false。
func TestParseEntityType(t *testing.T) {
	cases := []struct {
		in   string
		want EntityType
		ok   bool
	}{
		{"id_card", TypeIDCard, true},
		{"身份证号", TypeIDCard, true},
		{" 手机号 ", TypePhone, true},
		{"bank_card", TypeBankCard, true},
		{"不存在的类别", "", false},
		{"", "", false},
	}
	for _, c := range cases {
		got, ok := ParseEntityType(c.in)
		if ok != c.ok {
			t.Fatalf("ParseEntityType(%q) ok=%v，期望 %v", c.in, ok, c.ok)
		}
		if ok && got != c.want {
			t.Fatalf("ParseEntityType(%q) = %q，期望 %q", c.in, got, c.want)
		}
	}

	// 反向一致性：每个类别的中文名解析回来必须还是它自己
	all := []EntityType{
		TypePhone, TypeIDCard, TypeBankCard, TypeEmail, TypeIP,
		TypeSecret, TypePassword, TypeName, TypeAddress, TypeMedical,
	}
	for _, typ := range all {
		got, ok := ParseEntityType(typ.Label())
		if !ok || got != typ {
			t.Fatalf("Label(%q)=%q 解析回 (%q,%v)", typ, typ.Label(), got, ok)
		}
	}
}
