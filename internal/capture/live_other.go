//go:build !linux && !windows

package capture

import "errors"

// errLiveUnsupported 是不支持实时抓包的平台统一错误信息。
//
// 支持矩阵：Linux 用 AF_PACKET（live_linux.go），Windows 用原始套接字 + SIO_RCVALL
// （live_windows.go）；其它平台没有实时数据源实现，只能退回 synthetic/replay。
const errLiveUnsupported = "live 抓包仅支持 Linux（AF_PACKET）与 Windows（原始套接字）：当前平台请改用 synthetic/replay 数据源"

// 说明：非 Linux/Windows 平台没有 Live 实现，因此也不存在 AttachKernelFilter 方法 ——
// 过滤装饰器会跳过内核剪枝，只用用户态过滤（行为一致，只是少了内核侧剪枝）。

// NewLive 在不支持的平台返回错误。
func NewLive(_ string) (Source, error) {
	return nil, errors.New(errLiveUnsupported)
}

// NewLiveWithReadersPinned 在不支持的平台同样返回错误（CPU 绑定依赖 Linux 的 sched_setaffinity）。
func NewLiveWithReadersPinned(_ string, _ int, _ bool) (Source, error) {
	return nil, errors.New(errLiveUnsupported)
}

// NewLiveWithReaders 在不支持的平台同样返回错误。
// 多队列收包基于 Linux AF_PACKET 的 PACKET_FANOUT，其他系统没有等价实现；
// 这里保留同名函数是为了让上层的调用点在其它平台也能编译通过。
func NewLiveWithReaders(_ string, _ int) (Source, error) {
	return nil, errors.New(errLiveUnsupported)
}
