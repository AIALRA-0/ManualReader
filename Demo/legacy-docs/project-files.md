# 1 怎样同时读材料与查看项目文件

原文栏始终保留“主原文”标签，显示当前阅读映射；从左侧文件目录打开文件，会增加普通标签，原章节、译文和讲解保持原位。点击讲解或问题里的原文映射时，会回到主原文，普通文件标签继续保留

- 第一步，打开目录标题的下拉菜单，选择“文件目录”；也可以从原文栏的文件夹按钮进入，窄栏先打开“原文窗口工具”
- 第二步，展开项目文件夹，点击文件；隐藏文件也列在目录中，大文件夹分批加载，点击“继续显示文件”可继续浏览；系统把物理目录的变化推送到页面，已展开的目录会自动更新，不需要手动刷新
- 第三步，点击标签切换，拖动标签标题调整顺序，点击标签右侧的叉号关闭；主原文没有关闭按钮
- 第四步，需要并排保留阅读路线与文件树时，点击目录的拆分按钮；它依次切换单窗、上下两窗、上下三窗，每一窗都可独立选择阅读进度、练习进度、双环或文件目录，分隔条可以拖动

文件树显示真实物理路径；右键菜单支持新建、重命名、复制、剪切、粘贴、上传、文本编辑和回收站。可以把文件或文件夹拖入树中上传，也可以把现有文件拖到其他文件夹。删除先弹窗确认，再移入项目回收站；Agent 也必须先读取删除预览，再明确确认。已登记原件继续通过材料导入保存新版本

文件窗口右上角可放大，按 Escape 或还原按钮恢复；窄屏与手机通过底部栏目标签切换；标签宽度随窗口缩小，超出时可横向滚动

讲解和回答可直接引用项目文件，例如 `[查看示例代码](/api/project-files/raw?path=examples/example.py)`；点击后在原文栏打开文件标签，同一文件重复点击会复用原标签。Markdown 文件里的相对链接按该文件所在文件夹查找；中文和空格按 URL 编码。外部网页链接仍在浏览器打开；原文映射仍返回不可关闭的主标签

Agent 应先保存文件，再引用当前项目根目录内的相对路径；链接中不写机器物理路径、连接令牌或其他项目编号。完整文件引用用于查阅，段落与页码映射继续使用真实来源编号

# 2 文件默认放在哪里

系统为每个项目创建独立的文件根目录，默认放在阅读器数据目录下的 `project-files/<material_id>`。设置页可以填写新的默认父目录，路径属于运行阅读器的电脑或 VPS；已有项目继续保留原位置，新项目使用新位置

既有原件按稳定文件编号登记到 `sources` 中，上传的 ZIP 和仓库保留展开后的文件，包括隐藏文件。原始数据库、笔记、连接令牌与学习记录留在阅读器的数据目录中。项目文件目录不能设为这些记录所在的材料目录

`sources` 中已登记的原件保留版本；需要修改原件时使用材料导入接口保存新版本。普通项目文件可通过文件接口写入，查看它们不会切换当前章节；要加入原句定位和翻译流程，再通过材料导入登记

# 3 哪些文件怎样显示

<div align="center">

表 3.1 当前文件预览方式

| 文件类型 | 阅读器怎样显示 |
| --- | --- |
| 文本、代码、JSON、HTML、配置与日志 | 显示源代码；HTML 不执行脚本；较大的文本先显示开头，可下载完整原件 |
| Markdown | 使用现有 Markdown 与公式渲染器，保留表格与相对图片路径 |
| CSV、TSV | 显示表格 |
| 图片、音频、视频 | 使用浏览器查看与播放；图片可以单独放大，媒体格式以浏览器支持的编码为准 |
| PDF、EPUB、XPS 等文档 | 使用 PyMuPDF 分页渲染，支持翻页、缩放和整页适配 |
| Word、PowerPoint、Excel、OpenDocument、RTF | 有 LibreOffice 时在本机转为分页预览；没有转换器时，DOCX、PPTX、XLSX 可显示结构化内容 |
| ZIP | 列出归档中的文件名与大小，包括隐藏文件 |
| 其他二进制与专用格式 | 显示文件信息和字节预览，保留原件下载入口 |

</div>

完整办公版式需要 LibreOffice；Linux 上可以安装 Writer、Calc、Impress 三个组件，安装后重启阅读器。macOS 和 Windows 可安装桌面版 LibreOffice；程序不在标准位置时，通过 `MANUAL_READER_LIBREOFFICE` 指定执行文件

```sh
# Ubuntu / Debian：安装 Word、表格和幻灯片读取组件
sudo apt-get install --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress
```

转换使用独立临时配置并关闭宏，不使用外部在线文档服务；缓存按文件位置、大小和修改版本区分。分页能力来自 [PyMuPDF 文件支持说明](https://pymupdf.readthedocs.io/en/latest/how-to-open-a-file.html)，办公转换使用 [LibreOffice 命令行转换](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html)

# 4 Agent 怎样读取和保存文件

Agent 沿用当前项目的配对令牌；机器接口在 `/machine/v1` 下，浏览器接口在 `/api` 下；以下路径使用机器接口的相对路径

- `GET /project-files/events`：订阅当前项目的 SSE 文件事件；收到 `ready` 后重新核对已展开目录，收到 `changes` 后只更新受影响的目录；空闲时只发送连接心跳，不扫描文件树；授权撤销后停止推送
- `GET /project-files/trash`：读取当前项目文件回收站
- `POST /project-files/action`：`action` 使用 `mkdir`、`create`、`move`、`copy`、`trash-preview`、`trash` 或 `restore`；移动、复制带上当前 `expected_modified`；删除先读取预览，再提交 `review_id` 与 `confirmed=true`；恢复使用 `trash_id`
- `GET /project-files/tree?path=`：读取目录，返回 `entries`、`next_offset` 和当前项目的根目录信息；有下一批时继续传 `offset`
- `GET /project-files/preview?path=src/example.py`：读取预览；办公文档正在转换时返回 `kind=preparing`，稍后再读同一接口
- `GET /project-files/raw?path=src/example.py`：读取原始文件，支持字节范围；下载时追加 `download=1`
- `GET /project-files/page?path=docs/example.pdf&page=0`：读取从零开始的指定页图像
- `POST /project-files/file`：提交相对 `path` 和 `content_base64`；修改已有文件时，把预览返回的 `modified` 版本字符串放入 `expected_modified` 提交

接口拒绝上级路径、绝对路径、跨项目参数和指向根目录外部的符号链接；项目令牌不能读取全局设置或修改手动阅读进度。原件导入与文件预览分别保存，写文件不会覆盖问答、术语或已有练习

# 5 怎样复现文件查看演示

先启动本机阅读器，再从项目目录运行以下命令；脚本创建新 Demo，不覆盖已有项目，不调用模型

```sh
# 创建包含代码、隐藏配置、Markdown、CSV 和三页 PDF 的独立演示
python Demo/load_project_files.py --base http://127.0.0.1:8939
```

打开命令返回的链接；如果启动器显示了其他端口，使用它显示的端口。功能当前位于主分支，下载旧发行包时请按对应版本的说明使用

实时同步使用 [watchdog 的系统文件事件](https://python-watchdog.readthedocs.io/en/stable/quickstart.html)；服务在有浏览器订阅时监听该项目，最后一个订阅退出后释放监听。窗口回到前台或连接重建时核对已展开目录，批量写入合并通知，避免反复重画整个页面

English: Every project has its own file root. The pinned source tab follows reading mappings; ordinary tabs inspect files without changing the lesson. The outline supports one, two, or three vertically resizable panes. Hidden files are included, and large folders are paginated. Office layout previews use local LibreOffice; PyMuPDF renders document pages. Other files retain source, metadata, byte-preview, or download access according to their format. Project tokens remain scoped to their paired project; source versions, notes, exercise answers, and manual reading progress stay separate
