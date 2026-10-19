"""FlowMask 服务端（FastAPI 一体机）。

包结构对应《需求分析》第 3、4 章的能力分层：

- ``app.core``      配置、SQLite、公共依赖
- ``app.identify``  三级协同识别：L1 规则 / 评分分级 / 四级脱敏 / L2·L3 预留接口
- ``app.parsers``   多格式文件接入：text / json / csv / sql（docx、pdf 后续接入）
- ``app.tasks``     批量脱敏任务：建任务、跑识别脱敏、导出副本
- ``app.audit``     审计哈希链：链接、签名、验签
- ``app.api``       REST 路由
"""

__version__ = "0.1.0"
