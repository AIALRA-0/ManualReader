# ManualReader Next

ManualReader Next 在隔离项目中运行 STEP-01 到 STEP-20，从核对原件版本、生成讲解，到预览资源详解和提交核对过的交稿。项目数据、访问密钥与回执保存在各自的实例目录。

## 怎样从零安装和启动

Windows 和 Python 3.12（64 位）：

    powershell -File tools/install.ps1 -IncludeTests
    .\.venv\Scripts\python.exe -I -m manualreader.server --data instance/data --token-file instance/reader-token.txt --port 0

如果要离线安装，先准备锁文件对应的 wheel，再运行安装脚本并传入 Wheelhouse。访问密钥由服务在指定文件首次生成。服务仅绑定 127.0.0.1；健康检查和独立客户端是公开的本机资源，划入项目的原件和问答需要密钥。

客户端示例：

    .\.venv\Scripts\manualreader-next.exe --base http://127.0.0.1:<端口> --token-file instance/reader-token.txt --project <项目ID> --mode NORMAL --out <新输出目录>

INTRO 规划导读。FINALE 必须添加 --confirm-finale。ANSWER 每次读取一条真实问题，使用 --question；只有明确要求重答或比较时才用 --revise-existing-answer。四种模式共用独立客户端和服务。真实模型需要本机已登录的 Codex CLI，使用已批准的 Sol High 普通档。

## 怎样导入隔离项目

运行 tools/import_acceptance.py 时显式提供历史工作树、项目路径和历史首稿；它只读取旧材料，将已登记原件、章节、来源和问题存为独立投影。源码不依赖旧项目的路径。其他普通启动可直接提供符合 store.Project 合同的隔离项目，不需要旧工作树。

可重建源码包只提供程序、测试和规则。启动隔离服务时另行指定实例数据和访问密钥。历史首稿用作测试夹具时，收束首稿会带有测试标记；正式学习状态由用户在原 Reader 中管理。

## 怎样运行验收

    .\.venv\Scripts\python.exe -I -m pytest -n auto tests

tools/live_acceptance.py 启动独立服务、下载客户端并跑 40 个任务。旧版 84 项使用旧测试环境重跑，新版适用的规划、文件、API 和传输检查在 tests 中独立运行。

## 任务卡怎样生成

STEP-02 保存模型首轮结果，再由程序拆开明显独立的列举项，并补齐有明确起止符的 Markdown 代码或公式。原件、任务格式和已登记来源 ID 核对完成后，直接输出 `task-card.json` 和 `task-card.md`。传入 `--cache` 可复用相同输入的首轮结果。

`tools/replay_step02.py` 用已保存的首轮缓存走同一条出卡流程。回放始终读取原件和已登记来源，缓存缺项会让验收失败，避免触发模型请求。

## 怎样读取 STEP-03 原件

`manualreader-step03-server` 在独立端口提供 STEP-01/02 的原有接口和 STEP-03。启动时传入隔离数据目录、可写缓存目录和密钥文件。

    manualreader-step03-server --data instance/data --cache instance/step03-cache --token-file instance/reader-token.txt --port 0
    manualreader-step03 --base http://127.0.0.1:<端口> --token-file instance/reader-token.txt --project <项目> --round <M1轮次> --file <文件ID>

对 PDF 可重复传入 `--page`。指定 `--preview <路径.html>` 会导出可在本机打开的原页和裁图预览。要并行运行规划和原件预热，使用 `--plan NORMAL --out <空目录> --warm-file <文件ID>`，可重复指定实际候选文件。ANSWER 另传 `--question`，FINALE 另传 `--confirm-finale`。正式任务卡和 STEP-03 来源旁路文件分别保存。

图像、原生文字和可以核对的结构分别交付。公式只有原图时可沿视觉路径读取。Office 档案支持直接提取成员、关系、表格与图片，实际版式需要原版式载体。网页图片从同项目原件、内嵌数据或核对过哈希的历史附件中读取。

## 怎样运行 STEP-04 写作

先准备已完成 M2 核对的任务卡和对应的隔离项目。启动服务时指定用于保存首稿和回执的目录。

    manualreader-step04-server --data instance/data --cache instance/step03-cache --runs instance/step04-runs --token-file instance/step04-token.txt --port 0
    manualreader-step04 --base http://127.0.0.1:<端口> --token-file instance/step04-token.txt --project <项目> --round <M1轮次> --mode NORMAL --card <task-card.json> --out <chapter.md>

模式可选 NORMAL、INTRO、FINALE 或 ANSWER。FINALE 需明确传入 `--confirm-finale`。ANSWER 按卡片中的真实问题独立写作，复答时用 `--revise-existing-answer` 提交包含旧答案的任务卡。

在 `/step04` 页面输入本机服务密钥后，可以查看原稿和下载 Markdown。重复提交相同的卡片和轮次会读回既有任务。若程序确认此前连模型请求都没有发出，可以加上 `--resume-before-dispatch` 继续同一个任务。模型回执、原件位置和图片哈希保存在每个任务的运行目录。

## 怎样查看来源映射和单独的补充

为已验收的 STEP-04 任务准备隔离目录。目录中的 `inputs` 保存每份原稿、已登记来源和原件哈希，`media` 按哈希保存原图，`isolated-data` 保存前序任务所用的数据副本。

    python tools/prepare_step56.py <STEP-04 验收目录> <隔离目录>
    manualreader-step56-server --root <隔离目录> --models 36 --port 0
    manualreader-step56 --base http://127.0.0.1:<端口> --token-file <隔离目录>/token.txt --all --wait

准备命令从 STEP-04 的隔离数据及原项目校验过的材料快照读取来源。它用既有 Source ID 找到每条原文。隔离服务启动后从准备好的目录读取，运行时无需连接旧 Reader。服务地址保存在 `service-ready.json`。打开地址下的 `/step56`，登录后选择一个任务，即可阅读原稿、查看可跳转的来源并逐项打开补充。

同一个任务重复提交会读回已有结果。可以用 `--sample` 指定任务，或者用 `--all` 选中全部。添加 `--action download --output <目录>` 会下载映射和 Markdown。使用 `--action cancel` 可以停止等待中的模型工作，已发出的请求保留返回结果。重启服务后，`--resume` 会继续尚未提交的部分，并读回已经保存的模型结果。



## 怎样验证完整交稿和问答

`tools/run_isolated_material.py` 用独立目录接收一份 HTML、PDF 或 DOCX 新材料，登记原件、启动同一套 20 步任务，保留各步骤的模型回执和可供 Reader 读取的实际正文。带 `--help` 可查看参数。完整 Reader 使用 `python -m manualreader.stepfinal_server --run <隔离运行目录> --legacy <旧版Reader源码> --original-projects <隔离运行目录>/original-inputs/projects --port <空闲端口> --live`。旧版源码只提供现有的 Reader 组件和接口。

打开隔离 Reader 后，用「交稿」确认当前版本。系统写入隔离项目后才把有明确证据的文件位置计入进度，同一版重复提交会读回同一条记录。PDF 按已核对的原页内容流计入原件字节，DOCX 在正文成员的段落都能核对后计入该成员。原文、译文和讲解的关联率另行记录。质检页读取本次真实事件和模型回执。可将 `MANUAL_READER_SPEECH_URL` 指向已经启动的本地语音服务。问答继续使用 Reader 原生保存和执行接口，`tools/demo_reader_answers.py` 可用真实资源验证这条路径。

`tools/stress_resource_explanations.py` 从既有正文和已确认的资源索引读取资源，不生成或改写正文。初次执行为实际请求，使用空的压测输出目录。以同一记录目录再次执行并加 `--replay`，只读已有请求的回执，重新检查资源和正文位置，禁止发送新模型请求。

`tools/verify_delivery.py <隔离运行目录>` 会借助浏览器完成一次隔离交稿，核对重开和同版重放后留下的回执。STEP 日志对外使用统一编号与名称，旧标识由兼容表读取。
