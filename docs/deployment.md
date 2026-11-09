# 1 怎样在自己的电脑启动

需要 Python 3.10 或以上版本，先运行仓库根目录的启动脚本

- Windows 使用 `Start-Reader.ps1`
- Linux 和 macOS 使用 `sh Start-Reader.sh`
- 启动脚本会在本目录创建 `.reader-venv`，首次安装解析器依赖，以后直接复用
- 终端显示实际使用的地址，默认从 `http://localhost:8939` 开始寻找空闲端口，不关闭其他程序

Windows 如果尚未允许运行本地 PowerShell 脚本，可以直接运行 `python bootstrap_reader.py`，无需修改全局执行策略

本仓库带一份合成缓存材料，可以先阅读和提问；翻译、讲解和回答仍由用户自己的 Codex 对话触发

# 2 数据保存在哪里

本地启动时，主材料记录保存在 `reader.sqlite3`，其他项目保存在 `library/`，上传的原件、章节讲解和版本保存在各自项目目录

关闭服务后复制整个运行目录即可迁移；升级代码时保留数据库、`library/`、配对文件和设置，不用空演示数据覆盖它们

数据库和运行数据已列入 `.gitignore`，但忽略规则不能代替备份和检查

# 3 怎样自行部署到服务器

先在服务器创建独立环境并安装 `requirements.txt`，然后用进程管理器启动 `reader_server.py`；不要把开发机的数据库当作示例发布

常用环境变量如下

- `MANUAL_READER_PORT` 指定监听端口
- `MANUAL_READER_DB` 指定主材料数据库
- `MANUAL_READER_MATERIAL` 指定主材料 JSON
- `MANUAL_READER_LIBRARY` 指定其他项目的保存目录
- `MANUAL_READER_STATIC` 指向仓库内的 `static/`
- `MANUAL_READER_VENDOR` 指向仓库内的 `vendor/`
- `MANUAL_READER_PUBLIC_ORIGIN` 设置公开 HTTPS 地址，启用远程鉴权流程
- `MANUAL_READER_PROXY_SECRET` 由管理员设置，供可信回环反向代理验证身份，不写进仓库或前端

默认服务绑定回环地址；对外开放前，在 HTTPS 反向代理上接好身份验证，浏览器请求需要经过验证后再传给阅读器

可信代理必须清除外部传来的身份头，再自行填写 `X-Aialra-Authenticated` 和 `X-Reader-Proxy-Secret`；不可直接信任用户提供的同名请求头

机器接口使用单独的 `/machine/v1/` 路径和项目配对令牌，不把浏览器登录跳转当作机器鉴权；每个令牌仅访问配对时的项目，不能修改用户已读进度

这份仓库没有附带某个组织的登录配置或生产密码，部署者需要接好自己的身份验证；接口规则见 [Agent 接口说明](agent-api.md)

# 4 怎样启用语音

语音服务单独运行在回环地址，阅读器通过 `MANUAL_READER_SPEECH_URL` 连接它；浏览器只访问阅读器本身，不直接访问语音端口

安装和限制见[离线语音说明](offline-voice.md)

# 5 怎样在重启客户端后恢复项目连接

永久授权表示服务器不按时间自动到期，不代表进程内存会永久存在；用户选择记住连接后，客户端按 HTTPS 网站和项目编号在操作系统凭据库保存凭据，下一次读取同一项目时恢复

Windows 使用系统凭据管理器，macOS 使用钥匙串，Linux 需要可用的 Secret Service 与 `secret-tool`；没有可用凭据库时仍可本轮使用连接，但不能声称已经永久保存。令牌不进入聊天、项目文件、日志或 Git，用户撤销授权后恢复请求也会被服务器拒绝


## 怎样记录请求进入后端前的等待

在已有的 Nginx 阅读器转发配置中添加下面这一行，再检查配置并重载：

~~~nginx
proxy_set_header X-Reader-Proxy-Started $msec;
~~~

时间由代理服务器写入。代理与后端在同一台机器上时，响应头的 proxy_wait 会显示转发到处理函数开始之间的等待；不使用这项配置时，后端继续提供原有耗时，权限和业务行为不变。
