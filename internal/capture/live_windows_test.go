//go:build windows

package capture

import (
	"context"
	"errors"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestSynthEthernet 校验合成以太网头：14 字节头 + ethertype 0x0800 + IP 报文原样保留。
// Windows 原始套接字交付纯 IP 报文，而解析层按以太网帧解析，这一步是两种表示的桥。
func TestSynthEthernet(t *testing.T) {
	ip := []byte{0x45, 0x00, 0x00, 0x3c, 0x1c, 0x46, 0x40, 0x00, 0x40, 0x06}
	got := synthEthernet(ip)
	if len(got) != 14+len(ip) {
		t.Fatalf("长度 = %d，期望 %d", len(got), 14+len(ip))
	}
	if got[12] != 0x08 || got[13] != 0x00 {
		t.Errorf("ethertype = %02x%02x，期望 0800", got[12], got[13])
	}
	if string(got[14:]) != string(ip) {
		t.Error("IP 报文未被原样保留")
	}
}

// TestResolveLocalIPv4Literal 校验 iface 直接写 IPv4 时按原文绑定（界面选网卡即此路径）。
func TestResolveLocalIPv4Literal(t *testing.T) {
	ip, desc, err := resolveLocalIPv4(" 10.1.2.3 ")
	if err != nil {
		t.Fatalf("解析失败: %v", err)
	}
	if ip.String() != "10.1.2.3" || desc != "10.1.2.3" {
		t.Fatalf("got ip=%s desc=%s", ip, desc)
	}
}

// TestResolveLocalIPv4IPv6Rejected 校验 IPv6 给出明确错误（当前实现只支持 IPv4）。
func TestResolveLocalIPv4IPv6Rejected(t *testing.T) {
	if _, _, err := resolveLocalIPv4("::1"); err == nil {
		t.Fatal("IPv6 地址应返回错误")
	}
}

// TestResolveLocalIPv4Auto 校验 iface 为空或沿用 Linux 网卡名（eth0）时能自动选中本机 IPv4。
func TestResolveLocalIPv4Auto(t *testing.T) {
	for _, iface := range []string{"", "eth0"} {
		ip, desc, err := resolveLocalIPv4(iface)
		if err != nil {
			t.Fatalf("iface=%q 自动选择失败: %v", iface, err)
		}
		if ip.To4() == nil || desc == "" {
			t.Fatalf("iface=%q 得到非法结果 ip=%v desc=%q", iface, ip, desc)
		}
	}
}

// TestNewLivePermissionBoundary 校验权限边界：
// 未提权时必须给出"需要管理员权限"的可读错误（而不是跑起来却抓不到包）；
// 已提权时能建立数据源，且 Close() 后 Next 立刻返回而不是永久阻塞。
func TestNewLivePermissionBoundary(t *testing.T) {
	src, err := NewLive("")
	if err != nil {
		if CanLocalCapture() {
			t.Fatalf("已具备管理员权限却仍创建失败: %v", err)
		}
		if !strings.Contains(err.Error(), "管理员") {
			t.Fatalf("权限不足时必须给出可操作提示，实际错误: %v", err)
		}
		return
	}
	defer src.Close()
	if src.Stats().Packets != 0 {
		t.Fatal("刚创建的数据源不应已有收包计数")
	}
	done := make(chan error, 1)
	go func() { _, err := src.Next(context.Background()); done <- err }()
	select {
	case err := <-done:
		if err == nil {
			t.Error("Close 后 Next 应返回错误")
		}
	case <-time.After(3 * time.Second):
		t.Error("Close 后 Next 超时未返回（Close 没能唤醒阻塞的 recv）")
	}
}

// TestRecvDisposition 锁定一个真机踩出来的坑：
// recv 返回失败但 WSAGetLastError 读到 0 时，绝不能当成致命错误——
// 否则本机抓包会在第一次静默超时后就结束，日志表现为
// "[app] 采集源结束: The operation completed successfully."，看起来像正常退出，极难排查。
func TestRecvDisposition(t *testing.T) {
	cases := []struct {
		name string
		err  error
		want int
	}{
		{"接收超时按可重试处理", wsaETimedout, recvRetry},
		{"截断计入丢包", wsaEMsgsize, recvDrop},
		{"取不到错误码按可重试处理", syscall.Errno(0), recvRetryNoCode},
		{"权限不足属于致命错误", wsaEAcces, recvFatal},
		{"其它错误属于致命错误", errors.New("boom"), recvFatal},
	}
	for _, c := range cases {
		if got := recvDisposition(c.err); got != c.want {
			t.Errorf("%s: recvDisposition = %d，期望 %d", c.name, got, c.want)
		}
	}
}
