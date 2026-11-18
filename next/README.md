# 1 Next 引擎怎样参与 Reader
+
+Next 当前版本为 **0.8.1**

它在 `src/manualreader/` 实现规划、原件解析、Writer、补充融合、附件生成和版本化交稿；`../reader/` 提供项目接口、原生阅读组件和用户数据保存
+
+用户从根目录的 [启动说明](../README.md#2-怎样在本机开始阅读) 打开同一个 Reader

当前连接客户端的 `teach` 会下载匹配的 Next 运行包，不需要再启动一套只做规划的用户界面
+
+## 1.1 怎样开发和运行测试
+
+使用 Python 3.12，在仓库根目录运行：
+
+```sh
+python -m pip install -r requirements.txt
+python -m pip install pytest
+python -m pip install -e ./next
+python -m pytest Demo next/tests -q
+```
+
+需要只检查引擎时可以运行 `python -m pytest next/tests -q`

根目录 `pytest.ini` 同时配置 Reader 与 Next 的模块路径
+
+## 1.2 源码如何划分
+
+`stages/step01/` 到 `stages/step07/` 保留已验收的规划、解析、写作与融合职责；`stages/finish/` 完成结构、资源、翻译和附件装配；`steps/` 维护统一步骤合同、事件及最终提交

`connected.py` 接入 Reader 当前章节，`production.py` 与 `stepfinal_server.py` 装配实际服务
+
+四模式为 INTRO、NORMAL、FINALE、ANSWER

冻结 Writer 提示词在 `stages/step04/`，其他提示词按职责存放

修改路径或打包方式时应验证提示词哈希，不应自动改写其中内容
+
+## 1.3 工具如何使用
+
+`tools/run_isolated_material.py` 接收一份 HTML、PDF 或 DOCX，在新目录执行真实流程，会调用模型并产生费用

`tools/run_whiteboard.py` 管理批量真实任务

`tools/replay_20step.py` 用于已有运行结果的回放

其他 `tools/check_*.py`、`verify_*.py` 与 `package_*.py` 分别检查、验收和打包对应阶段，参数以各文件的 `--help` 为准
+
+这些工具保留历史 CLI 的 `--legacy` 参数名时，应传入当前 `reader/` 源码目录；名称只是兼容参数，不表示需要另外部署旧版 Reader

不要把历史测试回执当作新的模型生成记录
+
+## 1.4 部署时需要什么
+
+生产服务使用 `python -m manualreader.production --reader reader`，静态资源从 `reader/static/` 与 `reader/vendor/` 构建

实例目录、模型连接和登录代理配置与源码分开
+
+生成过程保留首稿、补丁、资源和真实用量

正式交稿经用户确认后提交，Agent 不会自动改变手动学习记录

网页使用原生问答、术语和练习组件，不注入额外的“术语表”“练习”或“交稿”正文按钮
+
+完整职责、并行边界与服务命令见 [架构说明](../docs/architecture.md)、[连接说明](../docs/agent-connection.md) 和 [部署说明](../docs/next-deployment.md)
+
