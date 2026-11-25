# 河道开放风险证据与公告排期

这是一个保存合成业务资料的 Python 项目。资料加载层只负责读取原始记录和检查输入契约，业务分析、解释和资源安排能力需要在项目中继续建设。

资料位于 data/，所有记录都带有合成资料标记。不同文件之间通过稳定标识和时间字段关联，缺失、部分质量和待处理状态都是资料含义的一部分，不能在读取时静默填充。字段说明见 docs/data-dictionary.md。

运行基线测试：python3 -m unittest discover -s tests -v
检查 Python 文件：python3 -m compileall src tests
