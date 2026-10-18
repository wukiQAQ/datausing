//go:build windows

package main

import (
	"log"

	"github.com/wukiQAQ/datausing/internal/buildinfo"
	"github.com/wukiQAQ/datausing/internal/capture"
)

// registerPlatformCapabilities 在启动阶段声明"需要平台 + 权限同时满足"的能力。
//
// 本机实时抓包（Windows 原始套接字 + SIO_RCVALL）要求管理员权限：
// 这里用一次真实的套接字探测决定是否声明 source.local。
// 探测失败只提示、不退出——非管理员启动仍然可以跑 synthetic/replay（离线 pcap 分析零权限可用）。
func registerPlatformCapabilities() {
	if capture.CanLocalCapture() {
		buildinfo.Enable("source.local")
		log.Printf("[capture] 本机实时抓包可用（本进程已有管理员权限，source=live 可直接使用）")
		return
	}
	log.Printf("[capture] 【提示】本机实时抓包需要管理员权限：当前进程未提权，source=live 不可用，source.local 能力未声明")
}
