package parser

import (
	"encoding/binary"
	"net/netip"
	"testing"
)

// withPadding 在帧尾追加以太网填充字节，模拟真实链路层最小帧长填充。
func withPadding(frame []byte, n int) []byte {
	return append(frame, make([]byte, n)...)
}

// withVLAN 在 MAC 之后插入一个 4 字节 VLAN 标签（TPID=0x8100），保持后续 EtherType 不变。
func withVLAN(frame []byte) []byte {
	out := make([]byte, 0, len(frame)+4)
	out = append(out, frame[:12]...)
	out = append(out, 0x81, 0x00, 0x00, 0x64)
	return append(out, frame[12:]...)
}

// buildEthIPv4TCPOptions 构造带 TCP 选项的帧，验证数据偏移字段（doff）能被正确跳过。
func buildEthIPv4TCPOptions(payload []byte, optLen int) []byte {
	doff := (tcpHdrMin + optLen) / 4
	tcp := make([]byte, tcpHdrMin+optLen)
	binary.BigEndian.PutUint16(tcp[0:], 12345)
	binary.BigEndian.PutUint16(tcp[2:], 8443)
	tcp[12] = byte(doff) << 4
	tcp[13] = 0x18
	tcp = append(tcp, payload...)
	return buildEthIPv4(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), ProtoTCP, tcp)
}

// buildEthIPv6TCP 构造以太网 + IPv6 + TCP 帧；ext 为该帧可选的扩展头（nil 表示无）。
func buildEthIPv6TCP(payload []byte, ext []byte) []byte {
	tcp := make([]byte, tcpHdrMin)
	binary.BigEndian.PutUint16(tcp[0:], 55555)
	binary.BigEndian.PutUint16(tcp[2:], 443)
	tcp[12] = 0x50
	tcp[13] = 0x18
	l4 := append(tcp, payload...)

	next := uint8(ProtoTCP)
	if len(ext) > 0 {
		next = 0 // 逐跳选项头
	}
	ip := make([]byte, ipv6HdrLen)
	ip[0] = 0x60
	binary.BigEndian.PutUint16(ip[4:], uint16(len(ext)+len(l4)))
	ip[6] = next
	ip[7] = 64
	copy(ip[8:24], netip.MustParseAddr("fd00::1").AsSlice())
	copy(ip[24:40], netip.MustParseAddr("fd00::2").AsSlice())

	if len(ext) > 0 {
		ext[0] = ProtoTCP // 扩展头指向 TCP
	}

	frame := []byte{
		0x00, 0x11, 0x22, 0x33, 0x44, 0x55,
		0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
		0x86, 0xdd,
	}
	frame = append(frame, ip...)
	frame = append(frame, ext...)
	return append(frame, l4...)
}

func TestPayloadTCP(t *testing.T) {
	raw := BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("8.8.8.8"), 12345, 80, []byte("hello"), false)
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "hello" {
		t.Errorf("Payload()=%q, want %q", got, "hello")
	}
}

func TestPayloadIgnoresPadding(t *testing.T) {
	raw := withPadding(BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("8.8.8.8"), 12345, 80, []byte("hello"), false), 11)
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "hello" {
		t.Errorf("Payload()=%q, want %q（不应包含以太网填充）", got, "hello")
	}
}

func TestPayloadVLAN(t *testing.T) {
	raw := withVLAN(BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("8.8.8.8"), 12345, 80, []byte(`{"phone":"13800138000"}`), false))
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != `{"phone":"13800138000"}` {
		t.Errorf("Payload()=%q，want JSON（VLAN 标签未被跳过）", got)
	}
}

func TestPayloadUDP(t *testing.T) {
	raw := BuildEthIPv4UDP(netip.MustParseAddr("10.0.0.2"), netip.MustParseAddr("10.0.0.3"), 5353, 53, []byte("dns-query"))
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "dns-query" {
		t.Errorf("Payload()=%q, want %q", got, "dns-query")
	}
}

func TestPayloadTCPOptions(t *testing.T) {
	raw := buildEthIPv4TCPOptions([]byte("secret=abc"), 12)
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "secret=abc" {
		t.Errorf("Payload()=%q, want %q（TCP 选项未跳过）", got, "secret=abc")
	}
}

func TestPayloadIPv6TCP(t *testing.T) {
	raw := buildEthIPv6TCP([]byte("v6-payload"), nil)
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "v6-payload" {
		t.Errorf("Payload()=%q, want %q", got, "v6-payload")
	}
}

func TestPayloadIPv6ExtHeader(t *testing.T) {
	ext := make([]byte, 8) // 逐跳选项头：next + hdrExtLen + 6 字节选项
	ext[1] = 0             // hdrExtLen=0 ⇒ 8 字节
	raw := buildEthIPv6TCP([]byte("after-ext"), ext)
	got, ok := Payload(raw)
	if !ok {
		t.Fatal("Payload() ok=false，want true")
	}
	if string(got) != "after-ext" {
		t.Errorf("Payload()=%q, want %q（IPv6 扩展头未跳过）", got, "after-ext")
	}
}

func TestPayloadEmptyAndNonTCP(t *testing.T) {
	syn := BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 12345, 80, nil, true)
	udpEmpty := BuildEthIPv4UDP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 1234, 53, nil)

	// ICMP 帧：复用 IPv4 头构造器，改协议号
	icmp := buildEthIPv4(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), ProtoICMP, []byte{8, 0, 0, 0, 0, 0, 0, 0})
	// ARP 帧：只有以太网头 + 28 字节 ARP
	arp := append([]byte{
		0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
		0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff,
		0x08, 0x06,
	}, make([]byte, 28)...)

	cases := []struct {
		name  string
		frame []byte
	}{
		{"SYN 无载荷", syn},
		{"UDP 无载荷", udpEmpty},
		{"ICMP", icmp},
		{"ARP", arp},
		{"空帧", nil},
		{"仅以太网头", make([]byte, ethHdrLen)},
	}
	for _, c := range cases {
		if p, ok := Payload(c.frame); ok {
			t.Errorf("%s: Payload()=(%q,true)，want ok=false", c.name, p)
		}
	}
}

// TestPayloadSpanConsistentWithParse 保证载荷定位与 Parse 的载荷长度口径一致（无填充时）。
func TestPayloadSpanConsistentWithParse(t *testing.T) {
	frames := [][]byte{
		BuildEthIPv4TCP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 12345, 80, []byte("0123456789"), false),
		BuildEthIPv4UDP(netip.MustParseAddr("10.0.0.1"), netip.MustParseAddr("10.0.0.2"), 1234, 53, []byte("abc")),
		buildEthIPv4TCPOptions([]byte("xy"), 8),
		buildEthIPv6TCP([]byte("v6"), nil),
	}
	for _, raw := range frames {
		info, err := Parse(raw)
		if err != nil {
			t.Fatalf("Parse: %v", err)
		}
		payload, ok := Payload(raw)
		if !ok {
			t.Fatalf("Payload ok=false, frame len=%d", len(raw))
		}
		if len(payload) != info.L4Payload {
			t.Errorf("len(Payload)=%d, Info.L4Payload=%d", len(payload), info.L4Payload)
		}
	}
}
