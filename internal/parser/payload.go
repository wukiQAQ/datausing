package parser

import "encoding/binary"

// 本文件提供"载荷定位"能力：在保持 Parse 的既有契约（只出元数据、不出字节）不变的前提下，
// 给内容级分析（DLP 敏感信息识别）提供传输层载荷切片。
//
// 为什么不直接改 Parse 的返回结构：Info 已被流表、聚合、过滤、API 与大量测试引用，
// 加字段会波及 40+ 处调用；单独暴露 Payload/PayloadSpan 属于纯增量，零回归风险。

// PayloadSpan 返回帧内传输层载荷的区间 [start, end)。
// 边界处理与 Parse 保持一致：
//   - 跳过单层 VLAN 与 QinQ 标签；
//   - IPv4 按总长度字段截断，去掉以太网填充（padding），避免把填充字节当内容；
//   - IPv6 按载荷长度字段截断，并跳过最多 4 个扩展头（逐跳/路由/分片/目的选项）；
//   - TCP 按数据偏移跳过选项字段，UDP 跳 8 字节头。
//
// ok=false 表示帧过短、非 IPv4/IPv6、非 TCP/UDP（如 ICMP、ARP），或载荷为空。
// 返回的切片与 frame 共享底层数组，调用方如需长期持有必须自行复制。
func PayloadSpan(frame []byte) (start, end int, ok bool) {
	etype, l3, ok := etherTypeAt(frame)
	if !ok {
		return 0, 0, false
	}
	switch etype {
	case EthTypeIPv4:
		return spanIPv4(frame, l3)
	case EthTypeIPv6:
		return spanIPv6(frame, l3)
	default:
		return 0, 0, false
	}
}

// Payload 返回帧的传输层载荷切片（与 frame 共享底层数组）。
// 只用于内容级分析；需要保留时请 copy 一份。
func Payload(frame []byte) ([]byte, bool) {
	start, end, ok := PayloadSpan(frame)
	if !ok || start <= 0 || end <= start || end > len(frame) {
		return nil, false
	}
	return frame[start:end], true
}

// spanIPv4 定位 IPv4 报文的传输层载荷。
func spanIPv4(frame []byte, l3 int) (int, int, bool) {
	if len(frame) < l3+ipv4HdrMin {
		return 0, 0, false
	}
	verIHL := frame[l3]
	if verIHL>>4 != 4 {
		return 0, 0, false
	}
	ihl := int(verIHL&0x0f) * 4
	if ihl < ipv4HdrMin || len(frame) < l3+ihl {
		return 0, 0, false
	}
	// 总长度字段是"IP 头 + IP 载荷"的长度；小于 IHL 说明报文损坏。
	total := int(binary.BigEndian.Uint16(frame[l3+2:]))
	end := l3 + total
	if total < ihl || end > len(frame) {
		// 抓包被截断时按实际帧长作为上界，保证能取到已捕获的部分
		end = len(frame)
	}
	return spanL4(frame, l3+ihl, end, frame[l3+9])
}

// spanIPv6 定位 IPv6 报文的传输层载荷（含扩展头跳过）。
func spanIPv6(frame []byte, l3 int) (int, int, bool) {
	if len(frame) < l3+ipv6HdrLen {
		return 0, 0, false
	}
	if frame[l3]>>4 != 6 {
		return 0, 0, false
	}
	payloadLen := int(binary.BigEndian.Uint16(frame[l3+4:]))
	end := l3 + ipv6HdrLen + payloadLen
	if payloadLen == 0 || end > len(frame) {
		// 载荷长度为 0（jumbogram 或截断）时按实际帧长兜底
		end = len(frame)
	}
	next := frame[l3+6]
	l4 := l3 + ipv6HdrLen
	for i := 0; i < 4 && extHeaders[next]; i++ {
		if len(frame) < l4+2 {
			return 0, 0, false
		}
		hdrNext := frame[l4]
		hlen := (int(frame[l4+1]) + 1) * 8
		if len(frame) < l4+hlen {
			return 0, 0, false
		}
		l4 += hlen
		next = hdrNext
	}
	return spanL4(frame, l4, end, next)
}

// spanL4 在传输层头之后定位载荷；end 为 IP 载荷上界（不含以太网填充）。
func spanL4(frame []byte, l4, end int, proto uint8) (int, int, bool) {
	switch proto {
	case ProtoTCP:
		if end < l4+tcpHdrMin || len(frame) < l4+tcpHdrMin {
			return 0, 0, false
		}
		doff := int(frame[l4+12]>>4) * 4
		if doff < tcpHdrMin {
			doff = tcpHdrMin
		}
		start := l4 + doff
		if start > end {
			// 头部被截断，或数据偏移字段异常
			return 0, 0, false
		}
		return start, end, true
	case ProtoUDP:
		if end < l4+udpHdrMin || len(frame) < l4+udpHdrMin {
			return 0, 0, false
		}
		return l4 + udpHdrMin, end, true
	default:
		return 0, 0, false
	}
}
