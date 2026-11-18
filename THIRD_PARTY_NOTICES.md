# 1 前端依赖来自哪里

本仓库保留所附组件的原有许可文件；项目源码尚未另行指定许可证

- Markdown 渲染使用 markdown-it，许可及相关依赖说明见 `reader/static/markdown-vendor.LICENSE.txt`
- 代码着色使用 highlight.js 11.12.0，BSD 3-Clause 许可见 `reader/vendor/code-highlight/LICENSE`；重建方式见 [脚本说明](scripts/README.md#12-怎样重建代码着色)
- 公式显示使用 KaTeX，MIT 许可见 `reader/vendor/media-vendor/katex/LICENSE`
- 流程图使用 Mermaid，MIT 许可见 `reader/vendor/media-vendor/LICENSE-mermaid.txt`

解析器依赖通过 `requirements.txt` 安装，各组件遵循各自许可；例如 PyMuPDF 的许可条件应由部署者按自己的使用方式核对，不应把整个依赖集合视为同一个许可证

# 2 语音模型是否包含在仓库里

语音运行库通过 `requirements-voice.txt` 选装，SenseVoice 模型权重需要从上游单独下载，仓库没有重新分发这些权重

请保留并遵守模型包与运行库附带的许可说明，参见 [SenseVoice 上游](https://github.com/QwenAudio/SenseVoice)和 [sherpa-onnx 上游](https://github.com/k2-fsa/sherpa-onnx)

# 3 演示材料是否是真实用户记录

默认缓存材料是项目测试创建的合成内容。当前 README 的网站截图来自实际 Reader，展示公开技术材料的阅读与测试项目；截图已裁去账户、项目身份、文件路径和连接信息。截图不是生成模型制作的界面，素材来源见 [README 素材说明](docs/assets/readme/next/README.md)
