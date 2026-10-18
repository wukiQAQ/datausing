//go:build !windows

package main

// registerPlatformCapabilities 在非 Windows 平台没有需要运行时探测的能力。
// Linux 的本机抓包能力（多队列 / CPU 绑定 / 内核剪枝）是静态能力，
// 已在 buildinfo.Features 里声明为 capture.multiqueue / capture.cpupin / capture.filter。
func registerPlatformCapabilities() {}
