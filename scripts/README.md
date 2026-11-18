# 1 这些脚本分别做什么

从仓库根目录运行下列命令

用户阅读与 Agent 连接使用同一个 Reader，不需要选择新旧模式

<div align="center">

<p>表 1 · 当前维护脚本</p>

| 文件 | 什么时候运行 | 得到什么 |
| --- | --- | --- |
| `bootstrap_reader.py` | 第一次在本机启动 | 创建独立虚拟环境、安装依赖，再启动 Reader |
| `launch_reader.py` | 需要自动打开浏览器并管理本机进程 | 使用仓库的 Reader 源码，把运行数据放到 `instance/` |
| `serve_reader.py` | 希望在前台运行或使用指定数据目录 | 只在回环地址启动装配好 Next 的 Reader |
| `Start-Reader.*` | 使用对应系统的快捷入口 | 调用同目录的 bootstrap，不复制另一套服务 |
| `code-highlight/build.cjs` | 修改代码着色语言或样式后 | 从 npm 包构建 `reader/vendor/` 下的浏览器资源 |
| `render_readme_diagrams.py` | 修改 `docs/diagrams/*.mmd` 后 | 使用本地 Mermaid 渲染 README 的 SVG |

</div>

## 1.1 怎样重建文档图

安装 Playwright，并让它能启动 Microsoft Edge

文档工具读取仓库附带的 Mermaid，不会将架构文本发给外部制图服务

```sh
python -m pip install playwright
python scripts/render_readme_diagrams.py
```

输出位于 `docs/assets/readme/next/`

修改图后应在明暗背景和窄屏检查文字是否可读，图中的职责也需要与实际执行路径相符

## 1.2 怎样重建代码着色

```sh
cd scripts/code-highlight
npm install --ignore-scripts
node build.cjs
```

运行依赖的许可文件保留在 `reader/vendor/`

不要把 `node_modules/`、模型权重、连接令牌或运行数据库加入 Git

## 1.3 批量测试工具在哪里

`next/tools/` 放引擎执行、隔离验收与历史结果回放工具

`Demo/` 放 Reader 回归、示例和旧文档

批量白板工具会调用真实模型并产生费用，不能当作启动 Reader 的必要步骤
