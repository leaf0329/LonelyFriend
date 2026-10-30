# Windows Internet 快捷方式

- 生成 `.url` 时使用 ASCII、无 BOM、CRLF 换行；URL 中的非 ASCII 字符先做 URL 编码。不要用 PowerShell 的 `-Encoding utf8` 写这类文件，以免 Windows 将目标读成空值。
- 验证 `.url` 时，用 `WScript.Shell.CreateShortcut(path).TargetPath` 确认 Windows 能读出预期目标，再检查页面可达；无需打开浏览器或前台窗口。
- 含管理 token 的快捷方式和登录资料只放在被 Git 忽略的私人目录或用户明确指定的位置，不提交到公开仓库。

# 测试阶段的人格切换

- 当前仍处于测试阶段。每次切换人格卡都必须初始化旧上下文、长期记忆、学习状态和本地内容缓存；使用 `scripts/switch_persona.py`，不要仅在管理面板改默认人格。
- 切换前停止本项目聊天服务，切换后启动并检查新人格和空白记忆；QQ 登录、账号凭据、聊天名单、管理员权限、模型与静态图片资源保留。
- 相同人格的措辞打磨不算切卡，不因此自动删除聊天。进入正式使用阶段后，由用户重新确认切换时的记忆规则。
