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
