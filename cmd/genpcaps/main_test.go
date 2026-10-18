package main

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/dlp"
	"github.com/wukiQAQ/datausing/internal/parser"
)

// TestScenarioWantMatchesAnalyze 校验每个场景声明的预期分级与识别结果一致：
// 场景写错或规则漂移时，这里会立刻失败，避免演示数据与实现脱节。
func TestScenarioWantMatchesAnalyze(t *testing.T) {
	for _, sc := range scenarios() {
		t.Run(sc.Name, func(t *testing.T) {
			res := dlp.Analyze(string(sc.Payload))
			if res.Level != sc.Want {
				t.Fatalf("分级=%s(score=%.3f)，want %s；命中=%v", res.Level, res.Score, sc.Want, res.TypeNames())
			}
			if sc.Want != dlp.LevelSafe && len(res.Hits) == 0 {
				t.Fatalf("非 SAFE 场景必须至少有一条命中")
			}
		})
	}
}

// TestScenariosCoverage 保证演示数据覆盖需求里的关键类别，避免只演示单一场景。
func TestScenariosCoverage(t *testing.T) {
	want := map[dlp.EntityType]bool{
		dlp.TypePhone:    true,
		dlp.TypeIDCard:   true,
		dlp.TypeBankCard: true,
		dlp.TypeEmail:    true,
		dlp.TypeAddress:  true,
		dlp.TypeSecret:   true,
	}
	got := map[dlp.EntityType]bool{}
	for _, sc := range scenarios() {
		for _, typ := range dlp.Analyze(string(sc.Payload)).Types {
			got[typ] = true
		}
	}
	for typ := range want {
		if !got[typ] {
			t.Errorf("演示场景未覆盖类别 %s", typ.Label())
		}
	}
}

// TestGeneratePCAPRoundtrip 生成 pcap 后用采集层自带的读取器回放，
// 逐帧抽出载荷交给识别层，验证"生成 → 落盘 → 回放 → 抽载荷 → 分级"整条链路。
func TestGeneratePCAPRoundtrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "demo-dlp.pcap")
	scs := scenarios()
	if err := capture.WritePCAP(path, packets(scs, baseTime())); err != nil {
		t.Fatalf("WritePCAP: %v", err)
	}

	st, err := os.Stat(path)
	if err != nil || st.Size() <= 24 {
		t.Fatalf("pcap 文件异常：err=%v size=%d", err, st.Size())
	}

	f, err := capture.OpenPcapFile(path)
	if err != nil {
		t.Fatalf("OpenPcapFile: %v", err)
	}
	defer f.Close()

	wantPayloads := make(map[string]bool, len(scs))
	for _, sc := range scs {
		wantPayloads[string(sc.Payload)] = true
	}

	matched := 0
	sawHigh, sawSafe := false, false
	var highMasked string
	frames := 0
	for {
		pkt, err := f.Next(context.Background())
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatalf("读取 pcap 失败: %v", err)
		}
		frames++
		payload, ok := parser.Payload(pkt.Raw)
		if !ok || len(payload) == 0 {
			continue
		}
		text := string(payload)
		if !wantPayloads[text] {
			t.Errorf("回放出现未预期的载荷（帧 %d）：%q", frames, text)
			continue
		}
		matched++
		res := dlp.Analyze(text)
		switch res.Level {
		case dlp.LevelHigh:
			sawHigh = true
			highMasked = dlp.Mask(text, res)
		case dlp.LevelSafe:
			sawSafe = true
		}
	}

	if frames != len(scs)*2 {
		t.Errorf("报文数=%d，want %d", frames, len(scs)*2)
	}
	if matched != len(scs) {
		t.Errorf("带载荷的帧数=%d，want %d", matched, len(scs))
	}
	if !sawHigh || !sawSafe {
		t.Errorf("需同时出现高危与安全样本：sawHigh=%v sawSafe=%v", sawHigh, sawSafe)
	}
	if !strings.Contains(highMasked, "[已屏蔽:") {
		t.Errorf("高危样本脱敏结果不含屏蔽标记：%q", highMasked)
	}
}

// TestPacketsTimestampsAscending 校验报文时间戳严格递增，便于按时间回放与画时间线。
func TestPacketsTimestampsAscending(t *testing.T) {
	pkts := packets(scenarios(), baseTime())
	if len(pkts) == 0 {
		t.Fatal("未生成任何报文")
	}
	for i := 1; i < len(pkts); i++ {
		if !pkts[i].Ts.After(pkts[i-1].Ts) {
			t.Fatalf("第 %d 个报文时间戳未递增：%v <= %v", i, pkts[i].Ts, pkts[i-1].Ts)
		}
	}
}
