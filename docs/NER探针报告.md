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

主评测集（`server/eval/dataset.jsonl`，75 条）以「带字段名上下文」的结构化数据为主，规则分支本来就能命中
姓名/地址；把自由叙述样本并进去反而会把「级别准确率」的语义搅乱。所以 NER 的增益用独立探针量化，
主评测集继续保持 98.7% 的稳定口径。

## 口径提醒

- 探针只比**类型召回**，不参与分级准确率；
- 默认关闭 NER：结构化数据上它会引入多余类别（同一开关在 75 条评测集上类别 Micro-F1 0.928 → 0.904）；
- 打开后代价约 +60–70 ms/条（CPU 前向），需要 GPU 或更高吞吐时再考虑量化。
