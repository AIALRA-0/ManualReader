# 怎样运行 STEP-07 V2

这一步读取 0.6.2 已验收的 STEP-04 原稿、STEP-06 补丁和映射。四种模式使用同一份冻结的 V2 编辑提示词，并在每个请求中携带 STEP-02 已经确认的 5 条写作偏好与 12 条格式规则。部署包与每次运行的输出分开保存。

## 怎样安装和重放

需要 Python 3.12 与 Node.js。浏览器验收用本机 Edge 和 Playwright。下面的路径由使用者指定。

```powershell
python -m venv .venv
$python = ".venv/Scripts/python.exe"
& $python -m pip install -r requirements.lock -r tests.lock
& $python -m pip install --no-deps .
& $python -m pytest -n auto -q -p no:cacheprovider tests
& $python tools/stage_step07_inputs.py $acceptedStep56 $newOutput --replies $unpackedRuns
& $python -m manualreader.stages.step07.fusion $newOutput --parallel 20
& $python -m manualreader.step07_server --root $newOutput
```

- $acceptedStep56 指向包含 inputs、jobs 与 media 的 0.6.2 验收输出，部署源码与隔离测试数据用于基线复核。
- $newOutput 使用独立目录。命令不会在上一轮目录落盘。
- $unpackedRuns 指向验收包解压后的 runs 目录。正式重新融合时，在准备输入的命令里去掉 --replies，再执行下面的模型调用命令。

```powershell
& $python -m manualreader.stages.step07.inference $newOutput --parallel 40
```

模型调用使用现有本机认证的 Codex。已有回执的任务读取首轮回执。融合请求传递日常正文、必要原文、图像引用与锚点，40 篇各提交一次，包括没有补丁的篇章。模型返回局部改句、补丁融合决策，以及必要的前后过渡。

## 怎样查看预览和下载

服务启动后会在输出目录写入 service-ready.json。用其中的 base 打开 /step07，在页面输入同一目录 token.txt 保存的访问密钥。客户端从 /v1/client 下载。

```powershell
& $python -m manualreader.step07_client --base $base --token-file $tokenFile --action download --output $downloads
```

客户端核对每次下载的正文哈希。服务只读取已落盘的结果。验收包里的浏览器脚本验证 40 篇预览、实际媒体渲染和定位。

## 各文件怎样衔接

- body.fused.md 保存完整派生稿。原稿保留在 inputs。
- fusion-edits.json 记录原稿及补丁可逆编辑、前后过渡的插入或替换位置，以及未采用的模型建议。
- anchor-remap.json 保留 STEP-06 的关系和身份，用字符区间定位当前正文或延伸补充。
- resources.fused.json 登记每次资源的原始字节、坐标、渲染位置和可确认的媒体哈希。
- deferred.json 保存实际折叠的补充。来源或子问仍可跳到对应的折叠内容。
- run.json 保存每篇装配结果。model 保存模型请求、首轮输出及回执。

位置使用 Unicode 字符从零计数，结束位置不计入范围，保留原稿 CRLF 字符。撤销时从最后一项开始，把派生稿中的对应文本换回记录里的原文。

## 从历史实现复用了什么

历史 P1.5 V2.3 的 Markdown 扫描器迁入 resource-scanner.cjs 组件并加入批量输入。局部融合沿用唯一引文定位、保护原始资源、逆序撤销的做法。Reader 预览复用已有 Markdown 渲染器及本地 KaTeX 和 Mermaid。资源不依赖旧服务。旧稿专用改句未进入运行路径。
