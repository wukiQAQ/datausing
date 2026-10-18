//go:build windows

// live 数据源（Windows）：原始套接字 + SIO_RCVALL 实时抓本机网卡流量。
//
// 与 Linux 的 AF_PACKET 实现（live_linux.go）相比，这条路的边界是明确的：
//   - 需要管理员权限：没有提权时连 SOCK_RAW 都建不出来（WSAEACCES），
//     因此权限提示必须在建套接字这一步给出，不能"跑起来但抓不到包"；
//   - 抓不到本机回环流量（127.0.0.1）：走本地代理（Clash/v2ray 等）的流量看不见；
//   - 没有内核 BPF：capture.filter 只在用户态过滤，filter_kernel 恒为 false；
//   - 没有多队列（PACKET_FANOUT）与 CPU 绑定（sched_setaffinity），配置项保留但被忽略。
//
// 两个实现细节值得注意（真机验证过）：
//  1. 必须先 WSAStartup：直接用 syscall 建套接字时，未初始化 Winsock 会报
//     "Either the application has not called WSAStartup"；
//  2. 不能用标准库的 syscall.Recvfrom：SIO_RCVALL 启用成功后它立刻返回
//     "not supported by windows"，一包都收不到，必须直接调用 ws2_32!recv。
package capture

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"
)

const (
	// sioRcvAll 让套接字进入混杂接收：收到该接口上的全部 IP 报文。
	sioRcvAll = 0x98000001
	// SOL_SOCKET / SO_RCVTIMEO：接收超时，保证 Close() 后阻塞的 recv 能及时返回。
	solSocket  = 0xFFFF
	soRcvTimeo = 0x1006
	// recvTimeoutMS 是单次 recv 最长阻塞时间（毫秒）。
	recvTimeoutMS = 200
	// rcvBufSize 是单帧缓冲上限：以太网 MTU 1500，64KB 足够容纳正常 IP 报文。
	rcvBufSize = 65536
	// minIPHeaderLen 是 IP 头最小长度，短于它的数据不是有效报文。
	minIPHeaderLen = 20

	// Winsock 错误码
	wsaETimedout = syscall.Errno(10060) // 接收超时（无数据时的正常返回）
	wsaEMsgsize  = syscall.Errno(10040) // 缓冲不足导致截断
	wsaEAcces    = syscall.Errno(10013) // 权限不足
)

var (
	ws2DLL              = syscall.NewLazyDLL("ws2_32.dll")
	procRecv            = ws2DLL.NewProc("recv")
	procWSAGetLastError = ws2DLL.NewProc("WSAGetLastError")

	wsastartupOnce sync.Once
	wsastartupErr  error
)

// errSourceClosed 表示数据源已关闭。
// 与 live_linux.go 中的同名变量语义一致；两个文件由 build tag 互斥编译，不会重复定义。
var errSourceClosed = errors.New("数据源已关闭")

// errAdminRequired 表示进程没有管理员权限，Windows 原始套接字建不出来。
var errAdminRequired = errors.New("本机实时抓包需要管理员权限：请以管理员身份运行 netmon.exe（Windows 原始套接字 SIO_RCVALL 要求提权）")

// ensureWinsock 保证 WSAStartup 已被调用。
// Go 的 net 包会自己初始化 Winsock，但入口装配数据源时并不保证跑过 net 包。
func ensureWinsock() error {
	wsastartupOnce.Do(func() {
		var data syscall.WSAData
		if err := syscall.WSAStartup(0x0202, &data); err != nil {
			wsastartupErr = fmt.Errorf("WSAStartup 失败: %w", err)
			return
		}
		// 预解析导出符号，避开懒解析与"读错误码"的竞态：
		// LazyProc 首次调用会先执行 GetProcAddress，而它会覆盖当前线程的 last-error；
		// 若这次解析正好发生在 recv 失败之后，WSAGetLastError 就会读到 0
		// （表现为 "The operation completed successfully."），把"接收超时"误判成致命错误。
		if err := procRecv.Find(); err != nil {
			wsastartupErr = fmt.Errorf("解析 ws2_32!recv 失败: %w", err)
			return
		}
		if err := procWSAGetLastError.Find(); err != nil {
			wsastartupErr = fmt.Errorf("解析 ws2_32!WSAGetLastError 失败: %w", err)
			return
		}
	})
	return wsastartupErr
}

// CanLocalCapture 探测当前进程能否创建原始套接字（等价于是否具备管理员权限）。
// 采集端启动时用它决定是否对外声明 source.local 能力。
func CanLocalCapture() bool {
	if err := ensureWinsock(); err != nil {
		return false
	}
	fd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_RAW, syscall.IPPROTO_IP)
	if err != nil {
		return false
	}
	_ = syscall.Closesocket(fd)
	return true
}

// Live 是 Windows 原始套接字抓包实现（单队列、无内核剪枝）。
type Live struct {
	fd     syscall.Handle
	desc   string // 采集接口描述（本机 IPv4 地址），用于日志与 Packet.Iface
	closed atomic.Bool
	stats  atomicStats
	// noCode 统计"recv 失败但读不到 Winsock 错误码"的次数（见 recvDisposition）。
	noCode atomic.Uint64
	buf    []byte
}

// NewLive 在本机接口上开始实时抓包；iface 为本机 IPv4 地址，留空表示自动选择。
func NewLive(iface string) (*Live, error) { return NewLiveWithReadersPinned(iface, 1, false) }

// NewLiveWithReaders 与 NewLive 等价：Windows 没有 PACKET_FANOUT，多队列被忽略。
func NewLiveWithReaders(iface string, readers int) (*Live, error) {
	return NewLiveWithReadersPinned(iface, readers, false)
}

// NewLiveWithReadersPinned 创建 Windows 实时数据源。
// readers / cpuPin 属 Linux 专有能力：这里保留可解析，但只记一条日志说明被忽略。
func NewLiveWithReadersPinned(iface string, readers int, cpuPin bool) (*Live, error) {
	if readers > 1 || cpuPin {
		log.Printf("[capture] Windows 本机抓包不支持多队列/CPU 绑定（readers=%d cpu_pin=%v）：已忽略该项配置", readers, cpuPin)
	}
	if err := ensureWinsock(); err != nil {
		return nil, fmt.Errorf("初始化 Winsock 失败: %w", err)
	}
	ip, desc, err := resolveLocalIPv4(iface)
	if err != nil {
		return nil, err
	}
	fd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_RAW, syscall.IPPROTO_IP)
	if err != nil {
		if isAccessDenied(err) {
			return nil, errAdminRequired
		}
		return nil, fmt.Errorf("创建原始套接字失败: %w", err)
	}
	l := &Live{fd: fd, desc: desc, buf: make([]byte, rcvBufSize)}
	var addr [4]byte
	copy(addr[:], ip)
	if err := syscall.Bind(fd, &syscall.SockaddrInet4{Port: 0, Addr: addr}); err != nil {
		_ = syscall.Closesocket(fd)
		return nil, fmt.Errorf("绑定本机地址 %s 失败: %w", desc, err)
	}
	if err := syscall.SetsockoptInt(fd, solSocket, soRcvTimeo, recvTimeoutMS); err != nil {
		log.Printf("[capture] 设置接收超时失败（不影响抓包，仅影响关闭响应速度）: %v", err)
	}
	one := int32(1)
	var returned uint32
	if err := syscall.WSAIoctl(fd, sioRcvAll, (*byte)(unsafe.Pointer(&one)), 4, nil, 0, &returned, nil, 0); err != nil {
		_ = syscall.Closesocket(fd)
		if isAccessDenied(err) {
			return nil, errAdminRequired
		}
		return nil, fmt.Errorf("启用 SIO_RCVALL 失败: %w", err)
	}
	log.Printf("[capture] 本机实时抓包已启动: %s（仅 IPv4；无内核剪枝、无多队列；抓不到 127.0.0.1 回环流量）", desc)
	return l, nil
}

// Next 阻塞返回下一个报文；ctx 取消返回 ctx.Err()，Close() 后返回 errSourceClosed。
// Next 阻塞返回下一个报文；ctx 取消返回 ctx.Err()，Close() 后返回 errSourceClosed。
func (l *Live) Next(ctx context.Context) (*Packet, error) {
	for {
		if l.closed.Load() {
			return nil, errSourceClosed
		}
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n, err := l.recv()
		if err != nil {
			switch recvDisposition(err) {
			case recvRetry:
				continue // 接收超时等可重试情形：回到循环顶部检查关闭/取消
			case recvDrop:
				l.stats.addDrop() // 缓冲不足导致的截断：计入丢包
				continue
			case recvRetryNoCode:
				if c := l.noCode.Add(1); c == 1 {
					log.Printf("[capture] recv 失败但取不到 Winsock 错误码，按可重试处理（后续同类情况只计数）")
				}
				continue
			}
			return nil, err
		}
		if n < minIPHeaderLen {
			continue // 长度不足一个 IP 头，不是有效报文
		}
		l.stats.addPacket()
		return &Packet{Ts: time.Now(), Raw: synthEthernet(l.buf[:n]), Iface: l.desc}, nil
	}
}

// recv 直接调用 ws2_32!recv：标准库的 syscall.Recvfrom 在 SIO_RCVALL 套接字上不可用。
func (l *Live) recv() (int, error) {
	r1, _, _ := procRecv.Call(uintptr(l.fd), uintptr(unsafe.Pointer(&l.buf[0])), uintptr(len(l.buf)), 0)
	n := int32(r1)
	if n < 0 {
		e, _, _ := procWSAGetLastError.Call()
		return 0, syscall.Errno(e)
	}
	return int(n), nil
}

// Close 关闭套接字；重复调用安全。
func (l *Live) Close() error {
	if l.closed.Swap(true) {
		return nil
	}
	return syscall.Closesocket(l.fd)
}

// Stats 返回收包计数。
// Windows 原始套接字没有内核侧统计（Linux 的 PACKET_STATISTICS 没有等价物），
// 因此 Drops 只统计"缓冲不足导致的截断"，不代表网卡丢弃。
func (l *Live) Stats() Stats { return l.stats.snapshot() }

// ethDst / ethSrc 是合成以太网头使用的占位 MAC（本地管理位 + 固定值）。
var (
	ethDst = [6]byte{0x02, 0x00, 0x00, 0x00, 0x00, 0x01}
	ethSrc = [6]byte{0x02, 0x00, 0x00, 0x00, 0x00, 0x02}
)

// synthEthernet 给 IP 报文补 14 字节以太网头（ethertype 0x0800 = IPv4）。
// Windows 原始套接字交付的是纯 IP 报文，没有链路层头，而解析层按以太网帧解析；
// 补一个固定头即可复用整条链路。代价：本机模式拿不到真实 MAC 地址。
func synthEthernet(ip []byte) []byte {
	out := make([]byte, 14+len(ip))
	copy(out[0:6], ethDst[:])
	copy(out[6:12], ethSrc[:])
	out[12], out[13] = 0x08, 0x00
	copy(out[14:], ip)
	return out
}

// resolveLocalIPv4 把配置里的 iface 解析成本机 IPv4 与描述文本。
// SIO_RCVALL 必须绑定到具体接口地址，因此：
//   - iface 是 IPv4 字面量 → 直接使用（界面选网卡就是把该地址填进来）；
//   - 空串或其它（例如从 Linux 配置沿用过来的 "eth0"）→ 自动取默认路由出口地址，
//     退化时取第一个已启用、非回环、带 IPv4 的接口。
func resolveLocalIPv4(iface string) (net.IP, string, error) {
	trimmed := strings.TrimSpace(iface)
	if trimmed != "" {
		if ip := net.ParseIP(trimmed); ip != nil {
			if v4 := ip.To4(); v4 != nil {
				return v4, v4.String(), nil
			}
			return nil, "", fmt.Errorf("iface=%q 是 IPv6 地址：本机实时抓包当前只支持 IPv4", trimmed)
		}
		// 非 IP 字面量：Windows 上没有"按网卡名绑定"这回事（那是 Linux AF_PACKET 的用法），
		// 这里退回自动选择默认路由出口。但必须留一条日志——静默换网卡会让用户以为配的网卡生效了。
		log.Printf("[capture] iface=%q 不是 IPv4 字面量：Windows 实时抓包按 IP 绑定、不接受网卡名，已改为自动选择默认路由出口网卡", trimmed)
	}
	// 默认路由出口地址：UDP "连接"不发送任何报文，只做一次路由查询。
	if conn, err := net.Dial("udp4", "203.0.113.1:9"); err == nil {
		defer conn.Close()
		if ua, ok := conn.LocalAddr().(*net.UDPAddr); ok {
			if v4 := ua.IP.To4(); v4 != nil && !v4.IsLoopback() {
				return v4, v4.String(), nil
			}
		}
	}
	ifs, err := net.Interfaces()
	if err != nil {
		return nil, "", fmt.Errorf("无法枚举本机网卡（请在 [capture] iface 里直接写明本机 IPv4）: %w", err)
	}
	for _, it := range ifs {
		if it.Flags&net.FlagUp == 0 || it.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, err := it.Addrs()
		if err != nil {
			continue
		}
		for _, a := range addrs {
			ipnet, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			if v4 := ipnet.IP.To4(); v4 != nil && !v4.IsLoopback() {
				return v4, v4.String(), nil
			}
		}
	}
	return nil, "", errors.New("找不到可用的本机 IPv4 网卡：请用 [capture] iface 指定本机 IPv4 地址")
}

// recvDisposition 把 recv 返回的错误分类成可重试 / 计入丢包 / 取不到码 / 致命。
//
// errno==0 这一档是真机踩出来的：recv 返回失败、而 WSAGetLastError 读到 0，
// 早期实现把它当致命错误，结果是本机抓包在第一次静默超时后就结束，
// 日志表现为 "[app] 采集源结束: The operation completed successfully."，
// 看起来像正常结束，极难排查。
// recv 结果分类：决定收到该错误后如何处理。
const (
	recvRetry       = iota // 可重试（例如接收超时）
	recvDrop               // 计入丢包后重试（缓冲不足导致的截断）
	recvRetryNoCode        // 取不到 Winsock 错误码，按可重试处理
	recvFatal              // 致命错误：结束采集
)

func recvDisposition(err error) int {
	errno, ok := err.(syscall.Errno)
	if !ok {
		return recvFatal
	}
	switch errno {
	case wsaETimedout:
		return recvRetry
	case wsaEMsgsize:
		return recvDrop
	case 0:
		return recvRetryNoCode
	}
	return recvFatal
}

// isAccessDenied 判断错误是否为"权限不足"（WSAEACCES）。
func isAccessDenied(err error) bool {
	var errno syscall.Errno
	if errors.As(err, &errno) {
		return errno == wsaEAcces
	}
	return false
}
