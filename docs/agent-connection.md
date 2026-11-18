# 1 怎样让 Agent 连接当前项目

打开 Reader 的「连接 Codex」，按当前页面说明完成配对

客户端从当前站点下载；不要从历史 ZIP 中混用旧版脚本

每次 bootstrap 返回当前项目身份、权限、来源和客户端清单

默认读取权限只覆盖所连接项目

用户可分别授权新增、修改和删除；已有连接的下一次请求采用最新选项

撤销令牌会使连接失效

浏览器登录与 Agent 项目令牌是不同的认证方式

## 1.1 哪些接口承载当前合同

<div align="center">

<p>表 1 · 当前连接与内容接口</p>

| 入口 | 作用 | 合同位置 |
| --- | --- | --- |
| `GET /machine/v1/guide?view=bootstrap` | 读取当前项目、权限、规则、客户端清单与下一步 | `reader/reader_server.py` |
| `GET /api/agent/<文件名>` | 下载允许分发的当前客户端、冻结 Writer 提示词与运行包 | `reader/reader_agent_contract.py` |
| `POST /machine/v1/content/import` | 将当前项目目录里的真实文件登记为原件 | `reader/reader_server.py` |
| `POST /machine/v1/project-files/file` | 在拥有新增权限时上传到项目范围内 | `reader/reader_file_actions.py` |
| `POST /api/content/presentation` | 选择当前章展示的主原文 | `reader/reader_server.py` |
| `reader_bridge.py teach` | 使用当前 Next 规划、写作及后处理 | `reader/reader_next_teach.py` |
| `reader_bridge.py daily` | 处理当前项目日常问题，保留原生问答上下文 | `reader/reader_bridge.py` |

</div>

接口的请求字段以 bootstrap 和当前源码为准

项目相对路径、修改版本和权限在服务端核对；网页或客户端发出请求并不代表它有权跨项目操作

## 1.2 原件与讲解保存在哪里

新项目使用 `source/` 放原件，已有 `sources/` 继续纳入

Agent 导入之前应先查看文件树，再登记真实路径，保留来源顺序和稳定编号

完成的讲解在 Reader 讲解栏显示，同时保存到 `explanations/`

文件名使用实际章节编号与标题；`.raw.md` 保存原样首稿，媒体进入 `explanations/media/`

不要只把一份 Markdown 放入文件目录就声称章节已经装配完成

首次章节应为 INTRO 导读

之后按当前任务与材料顺序生成普通章节；用户明确需要综合时使用 FINALE

ANSWER 的问题上下文来自当前问题和必要父问题，不自动挪用上一章的通用问法

## 1.3 “记住连接”会记在哪里

用户勾选后，连接说明要求客户端以 `--remember connect` 保存令牌

真正保存的是运行客户端的那台电脑：Windows 凭据管理器、macOS 钥匙串或 Linux Secret Service

网页复选框本身不会写入另一台电脑

令牌不应出现在任务卡、Markdown、截图或 Git 中

用户可从 Reader 撤销授权；这不会删除项目材料与学习记录

## 1.4 怎样确认用的是当前流程

核对 bootstrap 的 `client_files`，下载清单中的当前文件并放在同一客户端目录

当前分发保留必要的连接脚本，运行包提供 Next 引擎

服务端下载路径保持稳定，仓库内部文件位置不影响客户端 URL

当前实现分别位于 `reader/reader_next_teach.py`、`next/src/manualreader/connected.py` 和 `next/src/manualreader/stages/finish/`

实际模型、token、耗时及缺项来自本轮回执；默认配置和历史测试数字不能替代本轮记录
