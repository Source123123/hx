---
description: 保存这个项目的 Agent 环境（配置、记忆、会话摘要）
allowed-tools: Bash
---

运行下面的命令保存当前项目的 Agent 环境：

```
"${CLAUDE_PLUGIN_ROOT}/bin/hx" save $ARGUMENTS
```

然后用用户的语言、一两句话告诉用户结果：是否保存成功、快照 ID。命令输出里如果有"提醒"，原样转告，并问用户要不要你帮忙处理（例如把还没提交的文件 git commit）。不要自行 git push，除非用户要求。

如果输出里有"第一次保存"，再问用户一句：要不要开启"团队一键接入"（同事打开这个项目时会自动被提示安装 hx）。用户同意后运行：

```
"${CLAUDE_PLUGIN_ROOT}/bin/hx" team on
```

保存前后还有三件事，按需要做：

- **外部状态**：如果这次会话的工作依赖了项目之外的状态（数据库里的某些数据、远端服务的配置、本机装的某个工具、某个不在仓库里的文件），在保存之前把每一条记下来，接手的人和 Agent 才不会把它当成现成的事实：

  ```
  "${CLAUDE_PLUGIN_ROOT}/bin/hx" claim add "测试库里已经导入了 2024 年的订单样例" 
  "${CLAUDE_PLUGIN_ROOT}/bin/hx" claim add "本机装了 ffmpeg" --command ffmpeg
  "${CLAUDE_PLUGIN_ROOT}/bin/hx" claim add "模型权重已下载" --path models/weights.bin
  ```

- **向量库和索引**：如果项目里有本地向量库、检索索引这类由源文件生成的数据，不要让它进仓库，登记它的重建方法：

  ```
  "${CLAUDE_PLUGIN_ROOT}/bin/hx" resource add docs-index --path .rag/index --sources "docs/**/*.md" --build "python tools/build_index.py"
  ```

- **分享前检查**：用户要把项目推送或发给别人之前，运行 `"${CLAUDE_PLUGIN_ROOT}/bin/hx" audit`，把列出的内容用大白话告诉用户，由用户决定哪些需要隐去。名称类的（客户名、内部域名）用 `hx private add 真实的词 代称` 处理后重新保存。
