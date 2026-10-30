# 1 语音输入怎样运行

试点使用 [sherpa-onnx 的 SenseVoice ONNX 接口](https://k2-fsa.github.io/sherpa/onnx/sense-voice/index.html)，模型来自 [SenseVoice](https://github.com/QwenAudio/SenseVoice)

浏览器录音后，把短音频交给自己电脑或 VPS 上的阅读器，阅读器再转发到同机的语音服务；没有调用云端转写 API，服务不保存音频文件

这是结束录音后一次转写，不是逐字流式字幕；麦克风需要浏览器授权，远程页面需要 HTTPS

# 2 怎样安装服务

需要 FFmpeg、Python 独立环境和 `requirements-voice.txt`，从上游下载 `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17` 模型包，并保留模型附带的许可文件

```sh
# 在仓库目录创建语音专用环境，不向系统环境安装依赖
python3 -m venv voice-env
voice-env/bin/python -m pip install -r requirements-voice.txt

# 把参数替换成已解压模型的实际目录
voice-env/bin/python reader_speech.py --model-dir models/sensevoice --port 18972 --threads 2
```

Windows 使用 `voice-env/Scripts/python.exe` 代替上面的解释器路径；`model-dir` 内需要有 `model.int8.onnx` 和 `tokens.txt`

在阅读器进程环境中设置 `MANUAL_READER_SPEECH_URL=http://127.0.0.1:18972`，然后重启阅读器；服务就绪时，问题框会显示麦克风

模型权重不包含在本仓库，下载需要联网，转写阶段可离线运行；上游模型和运行库的许可应分别核对

# 3 用户怎样提问

- 点击麦克风开始录音，再次点击结束，最长一次 45 秒
- 转写结果进入原来的问题框，沿用问题自动保存，不自动开始生成回答
- 转写文字仍可修改；如果暂时无法连接服务，页面保留当前录音供重试
- 切换项目后，不把旧项目的语音写进新项目；录音不作为材料保存

本次五段中文合成录音长约 7 至 17 秒，本机转写约 0.5 至 1.4 秒，实际 VPS 热运行约 0.8 至 1.9 秒，首次样本约 2.7 秒；这些结果不能代表真实麦克风、口音或嘈杂环境的准确率

专业英文标识仍会出错，例如 `quadprog` 在测试中识别错了；保留原句关联后可以说“这个函数为什么这样写”，必要时再手动修改英文名

# 4 服务怎样限制资源

语音服务只监听回环地址，每次最多接收 4 MB，最多转写 45 秒；FFmpeg 通过内存管道解码，不允许录音输入引用外部文件或网址

默认同时处理一段录音，忙时返回可重试提示；VPS 可以再用进程管理器限制 CPU 和内存，不把语音端口直接暴露到公网
