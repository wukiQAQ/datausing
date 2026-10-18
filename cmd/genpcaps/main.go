// Command genpcaps 生成用于演示与自测的 pcap 文件。
//
// 合成的每条会话都是一段真实的 HTTP 报文（含 JSON / 表单 / CSV / 明文文本），
// 用于验证"采集 → 载荷抽取 → 敏感数据识别 → 分级脱敏"整条链路，
// 不依赖真实网络与管理员权限，答辩现场一条命令即可重放。
//
// 用法：
//
//	go run ./cmd/genpcaps                      # 生成 data/demo-dlp.pcap
//	go run ./cmd/genpcaps -out D:/demo.pcap    # 指定输出路径
//	go run ./cmd/genpcaps -list                # 只列出场景与预期分级，不写文件
package main

import (
	"flag"
	"fmt"
	"log"
	"net/netip"
	"os"
	"path/filepath"
	"time"

	"github.com/wukiQAQ/datausing/internal/capture"
	"github.com/wukiQAQ/datausing/internal/dlp"
	"github.com/wukiQAQ/datausing/internal/parser"
)

// scenario 是一条待生成的会话：一次 TCP 交互 + 一段应用层报文。
// Want 声明该场景的预期分级，测试会据此校验识别链路（同时避免场景与规则漂移）。
type scenario struct {
	Name    string
	Src     netip.Addr
	Dst     netip.Addr
	Sport   uint16
	Dport   uint16
	Payload []byte
	Want    dlp.Level
}

const (
	clientIP = "10.0.0.5"
	// 用文档保留网段作为服务端地址，避免与真实业务地址混淆。
	serverIP = "203.0.113.10"
)

// scenarios 返回全部演示场景。覆盖结构化（JSON/表单/CSV）与非结构化（明文文本）报文，
// 并保留一条不含敏感信息的正常请求用于误报检查。
func scenarios() []scenario {
	c := netip.MustParseAddr(clientIP)
	s := netip.MustParseAddr(serverIP)
	mk := func(name string, sport, dport uint16, payload string, want dlp.Level) scenario {
		return scenario{Name: name, Src: c, Dst: s, Sport: sport, Dport: dport, Payload: []byte(payload), Want: want}
	}
	return []scenario{
		mk("JSON 下单（姓名+手机+身份证）", 51001, 80, `POST /api/order HTTP/1.1
Host: shop.example.com
Content-Type: application/json
Content-Length: 118

{"name":"张三","phone":"13800138000","id_card":"11010519491231002X","note":"尽快发货"}`+"\r\n", dlp.LevelHigh),
		mk("注册表单（邮箱+地址）", 51002, 80, `POST /api/register HTTP/1.1
Host: portal.example.com
Content-Type: application/x-www-form-urlencoded
Content-Length: 96

username=zhangsan&email=zhang.san@example.com&address=福建省福州市鼓楼区五四路100号`+"\r\n", dlp.LevelMid),
		mk("接口带密钥查询", 51003, 80, `GET /api/query?api_key=sk-ABCDEFGH12345678&q=1 HTTP/1.1
Host: api.example.com

`+"\r\n", dlp.LevelHigh),
		mk("CSV 客户表上传（银行卡）", 51004, 80, `POST /api/upload HTTP/1.1
Host: crm.example.com
Content-Type: text/csv
Content-Length: 88

name,phone,bank_card
李四,13900139000,4111111111111111`+"\r\n", dlp.LevelHigh),
		mk("明文文本（电话改签）", 51005, 80, `POST /api/ticket/remark HTTP/1.1
Host: ops.example.com
Content-Type: text/plain

客户张伟，电话 13700137000，需要改签`+"\r\n", dlp.LevelMid),
		mk("正常健康检查（对照）", 51006, 80, `GET /api/v1/health HTTP/1.1
Host: monitor.example.com
User-Agent: curl/8.0

`+"\r\n", dlp.LevelSafe),
	}
}

// baseTime 是生成的报文起始时间戳；固定值保证同一份场景重复生成的结果可复现。
func baseTime() time.Time {
	return time.Date(2026, 10, 18, 10, 0, 0, 0, time.UTC)
}

// packets 为每条会话生成"SYN + 数据段"两个报文，时间戳按 50ms 递增。
func packets(scs []scenario, base time.Time) []capture.Packet {
	out := make([]capture.Packet, 0, len(scs)*2)
	ts := base
	for _, sc := range scs {
		syn := parser.BuildEthIPv4TCP(sc.Src, sc.Dst, sc.Sport, sc.Dport, nil, true)
		out = append(out, capture.Packet{Ts: ts, Raw: syn, Iface: "genpcaps"})
		ts = ts.Add(50 * time.Millisecond)
		data := parser.BuildEthIPv4TCP(sc.Src, sc.Dst, sc.Sport, sc.Dport, sc.Payload, false)
		out = append(out, capture.Packet{Ts: ts, Raw: data, Iface: "genpcaps"})
		ts = ts.Add(50 * time.Millisecond)
	}
	return out
}

func main() {
	out := flag.String("out", filepath.Join("data", "demo-dlp.pcap"), "输出 pcap 路径")
	list := flag.Bool("list", false, "只列出场景与预期分级，不写文件")
	flag.Parse()

	scs := scenarios()
	if *list {
		for _, sc := range scs {
			res := dlp.Analyze(string(sc.Payload))
			fmt.Printf("%-30s 预期=%-4s 实际=%-4s 类别=%v\n", sc.Name, sc.Want, res.Level, res.TypeNames())
		}
		return
	}

	if dir := filepath.Dir(*out); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			log.Fatalf("创建输出目录失败: %v", err)
		}
	}
	pkts := packets(scs, baseTime())
	if err := capture.WritePCAP(*out, pkts); err != nil {
		log.Fatalf("写 pcap 失败: %v", err)
	}

	fmt.Printf("已生成 %s：%d 条会话 / %d 个报文\n", *out, len(scs), len(pkts))
	for _, sc := range scs {
		res := dlp.Analyze(string(sc.Payload))
		fmt.Printf("  %-30s %-4s %v\n", sc.Name, res.Level, res.TypeNames())
	}
	fmt.Println("回放验证：go run ./cmd/netmon -source replay -replay-file " + *out)
}
