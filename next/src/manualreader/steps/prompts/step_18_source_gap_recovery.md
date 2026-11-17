# STEP-18 · 用真实原文恢复讲解关联（Sol High）

本次只检查给出的原文条目。原文可能已经讲清却只登记了导航，也可能是文中真正漏讲的内容、纯页码、重复页眉或整页图像载体。逐条选择一种处理，并说明具体依据。

- 已经讲清时，使用 already_explained，引用真实 block_id，从已给正文逐字摘一小段 body_excerpt，指向能够实际解释原文含义的文字。original_excerpt 逐字取自当前原文。中文与外文之间可以按含义关联。
- 确实漏讲且对本章必要时，使用 add，给出最合适的真实 block_id，逐字提供 original_excerpt，再写一小段 addition 补清这一处。保留来源的事实、条件和语气，引文片段必须能实际支撑补充。不要重写已经讲清的内容。
- 确认是重复页眉、页码、整页导航载体或不承担本章教学目标的内容时，使用 reference_only。仍保留原定位。
- 当内容含义或证据不足时，使用 unresolved，并说清缺什么。不要猜测图像里的细节、写入假来源或抬高覆盖率。

逐条使用输入中的 source_id。输出 JSON，items 中每个项目包含 source_id、block_id、disposition、original_excerpt、body_excerpt、addition 和 reason。没有使用的文本字段填空字符串。
