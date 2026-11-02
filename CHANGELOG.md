## v0.3.2

- 网页附属内容可明确排除和恢复，原文保留，目录不再自动补回；新项目一次规划一个首层大章，子节可一起规划，既有完整目录保留


- 讲解任务按项目、连接与目标章持久保存，页面换章不再误报写回权限错误；旧页面切章增加版本核对，服务重启后继续使用原任务
- 切章和完成勾选按版本返回必要变化，内容更新时回退完整读取；复用未变化的只读工作区，减少术语展示的整份历史复制与重复覆盖率计算
- 目录通过一个按钮选择单项进度或阅读／练习双环；练习新增默认关闭的自动核对
- GitHub 首页与图册加入用户提供的真实硬件阅读页面、官网只读分区截图与手机视图；完整合成示例仍可独立装载

# v0.3.1

- 使用完整六区阅读场景展示产品，并提供分区操作图册与可装载的合成 Demo
- 章节进度改为累计计数，复用同一请求中的章节计划，减少历史返回时重复重建译文
- 移除运行入口不再使用的旧界面副本与拼接脚本，当前渲染器和第三方许可保留
- 新增五项性能行为回归，与现有测试合计三十六项；测量条件与结果见 docs/performance.md

The README now demonstrates a complete six-area reading workflow with reproducible synthetic content. Chapter counting and plan reuse reduce repeated work, history navigation reconstructs translations once, and obsolete distribution copies are removed

# v0.3.0

## 1 阅读和提问有什么变化

- 较慢的初始化与章节读取显示轻微模糊、转圈和真实处理阶段，完成或出错后恢复
- 回答三角直接区分黄色未读与绿色已读，新版回答重新等待用户展开，刷新后保留状态
- 删除回答后修改问题仍进入默认待答池，问题编号、来源、追问和历史继续保留
- 删除操作使用统一弹窗，问题编辑自动保存后的状态与收起按钮对齐
- 同步当前版本的连续翻译、来源映射、章节与材料责任范围、项目凭据恢复和导入处理代码

## 2 Agent 删除怎样防止误操作

Agent 先读取删除预览并核对原话、版本和追问范围，再使用限定当前项目与调用者的一次性确认；内容变化、超时或重复确认均会被拒绝，自动保存不能绕过删除流程

## 3 怎样使用与复查

下载本版本 ZIP，解压到独立目录，按 [README](README.md) 运行系统对应的启动脚本；首次安装依赖需要联网。升级已有安装时先备份数据，只更新程序，不用演示材料覆盖个人资料

三十一项合成单元测试、机器删除限制、慢请求恢复和浏览器问答完整流程已验证；手机检查使用浏览器视口模拟。截图均为合成材料，本轮回归没有调用写作模型。详情见[功能图册](docs/features.md)与[测试记录](docs/validation.md)

## 4 What changed

Slow reads now show a subtle blur and a real-stage spinner. Answer-reading state survives reloads and resets when the answer changes. Deleting an answer and editing its question returns the same question to the default pending pool without losing follow-up relationships or history

Agent deletion requires a scoped, expiring, single-use preview and confirmation. The release also synchronizes the current reading, translation, import, and credential-recovery code, with synthetic screenshots and regression checks. This release does not include private reading data, credentials, model weights, or new model benchmarks
