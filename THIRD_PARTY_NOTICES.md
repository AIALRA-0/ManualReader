# 1 前端依赖来自哪里

本仓库保留所附组件的原有许可文件；项目源码尚未另行指定许可证

- Markdown 渲染使用 markdown-it，许可及相关依赖说明见 `static/markdown-vendor.LICENSE.txt`
- 公式显示使用 KaTeX，MIT 许可见 `vendor/media-vendor/katex/LICENSE`
- 流程图使用 Mermaid，MIT 许可见 `vendor/media-vendor/LICENSE-mermaid.txt`

解析器依赖通过 `requirements.txt` 安装，各组件遵循各自许可；例如 PyMuPDF 的许可条件应由部署者按自己的使用方式核对，不应把整个依赖集合视为同一个许可证

# 2 语音模型是否包含在仓库里

语音运行库通过 `requirements-voice.txt` 选装，SenseVoice 模型权重需要从上游单独下载，仓库没有重新分发这些权重

请保留并遵守模型包与运行库附带的许可说明，参见 [SenseVoice 上游](https://github.com/QwenAudio/SenseVoice)和 [sherpa-onnx 上游](https://github.com/k2-fsa/sherpa-onnx)

# 3 演示材料是否是真实用户记录

README 截图和默认缓存材料由项目测试创建，是合成内容，没有使用真实用户的文章、问题、笔记或身份信息
