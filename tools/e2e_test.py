# -*- coding: utf-8 -*-
"""隔离端到端回归入口。

所有运行数据由 regression_test 在 TemporaryDirectory 中创建；本脚本不会启动或连接
真实服务，也不会读取、删除、覆盖项目 data/ 下的配置、账号、作业或历史记录。
"""

from regression_test import main


if __name__ == "__main__":
    main()
