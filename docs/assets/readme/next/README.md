# 1 这些图片来自哪里

本目录为中英文 README 共用素材，更新日期为 2026-11-17

## 1.1 网站图片怎样取得

`reading-light.png`、`reading-dark.png`、`terms.png`、`practice.png`、`resource.png`、`phone-reading.png` 和 `permissions.png` 来自本轮实际部署的 Reader

内容为公开技术材料的阅读与测试项目，未用生成图片冒充产品界面

问答图复用上一级目录的 `actual-questions.png`，来自 2026-11-02 的实际公开技术访谈项目，原始采集说明见上一级的 `production-capture.md`

截图只保留阅读面板或权限控件，裁去站点账户、项目编号、文件路径、连接码和令牌

拍摄只读页面，未提交答案、问题、完成记录或交稿

明暗两图通过网站本身的主题按钮切换，窄屏图通过改变浏览器视口取得

## 1.2 架构图怎样维护

`workflow.svg`、`source-relations.svg` 和 `permissions.svg` 分别说明步骤依赖、来源关系和项目权限

源文件在 `docs/diagrams/`，可使用 `scripts/render_readme_diagrams.py` 重建

图由仓库本地 Mermaid 生成 SVG，再保留在 Git 中，GitHub 显示图片时无需访问图表服务

图中不含凭据、内部主机地址或用户项目数据
