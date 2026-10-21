# L1.5 NER 自由文本探针

> 样本 8 条，标注类别 18 个；类型召回 **22.2% → 100.0%**（4/18 → 18/18），被 NER 提升的样本 8/8。

| # | 文本（截断） | 标注类别 | 无 NER | 有 NER |
| --- | --- | --- | --- | --- |
| 1 | 张三昨天从福州赶过来，住在鼓楼区五四路附近，商量… | address, name | — | address, name |
| 2 | 李四让我转告王五，下周三在厦门大学开会，材料先发… | address, email, name | email | address, email, name |
| 3 | 客户王小明来电反馈，说他在泉州市丰泽区东海街道的… | address, name | — | address, name |
| 4 | 这份病历属于陈建国，诊断结果是 2 型糖尿病，联… | medical, name, phone | medical, phone | medical, name, phone |
| 5 | 赵敏的身份证复印件落在家了，地址写的是南京市鼓楼… | address, name | — | address, name |
| 6 | 周末和老刘约在福州鼓山爬山，他手机 139001… | name, phone | phone | address, name, phone |
| 7 | 快递收件人是周杰，地址福建省厦门市思明区环岛东路… | address, name | — | address, name |
| 8 | 王芳在上海浦东新区张江高科上班，工号 8848。 | address, name | — | address, name |

> 结论：字段名上下文缺失时，规则几乎抓不到人名/地址；NER 补的就是这块。
> 默认仍关闭 NER（`FLOWMASK_ENABLE_NER=0`）：结构化/带字段名的数据上它只会引入多余类别。

## 复现方式

```powershell
cd server
$env:FLOWMASK_ENABLE_NER = "1"
$env:FLOWMASK_NER_MODEL_DIR = "models/bert4ner"   # shibing624/bert4ner-base-chinese，406 MB，不入库
python -m ner_probe --model-dir models/bert4ner --markdown-out ../docs/NER探针报告.md
```

## 为什么单独做探针

主评测集（`server/eval/dataset.jsonl`，**351 条**）以「带字段名上下文」的结构化数据为主，规则分支本来就能命中
姓名/地址；把自由叙述样本并进去反而会把「级别准确率」的语义搅乱。所以 NER 的增益用独立探针 + 压力集量化，
主评测集继续保持 100.0% 的稳定口径。

## 公开语料压力集（`eval/ner_stress.jsonl`，200 条）

探针之外，另从公开语料 `weibo_ner/dev` 里抽 200 条自由文本句子（人名/地名，噪声已用黑名单 + 姓氏启发式过滤），
把实体嵌进「张三昨天从福州赶过来…」这类没有字段名的叙述里——L1 自由文本规则能兜住一部分人名（见下），
但地名/无动作词的人名只能靠 NER：

| 口径 | 分级准确率 | 类别 Micro-F1 | p95 时延 |
| --- | ---: | ---: | ---: |
| 纯 L1（自由文本规则 + 地名库） | **61.0%** | **0.709** | 0.05 ms |
| L1 + NER | **90.0%** | **0.942** | 29.7 ms |

剩余 27 个漏检类别分布在 22 条样本上，基本都是语料标注噪声（「电器城/游乐园/宿舍」这类非标准地点 NER 也不认）与极短人名，属数据问题不调参掩盖。
复现：`python -m app.evaluate --dataset eval/ner_stress.jsonl`（加 `$env:FLOWMASK_ENABLE_NER="1"` 对比开关）。

## 口径提醒

- 探针只比**类型召回**，不参与分级准确率；
- 默认关闭 NER：结构化数据上它主要补召回、不改变分级（351 条评测集上准确率仍 100.0%，类别 Micro-F1 0.888 → 0.955，但多出若干 FP 类别）；
- 打开后代价约 +71 ms/条（CPU 前向，L1 列 p95 0.07 → 71.4 ms），需要 GPU 或更高吞吐时再考虑量化；
- 自由文本仍是它的主场：压力集准确率纯 L1 61.0%（含地名库）→ 开 NER 90.0%，因此做成**请求级开关**而不是全局默认。
