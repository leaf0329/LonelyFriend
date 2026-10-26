# Windows Internet 快捷方式

- 生成 `.url` 时使用 ASCII、无 BOM、CRLF 换行；URL 中的非 ASCII 字符先做 URL 编码。不要用 PowerShell 的 `-Encoding utf8` 写这类文件，以免 Windows 将目标读成空值。
- 验证 `.url` 时，用 `WScript.Shell.CreateShortcut(path).TargetPath` 确认 Windows 能读出预期目标，再检查页面可达；无需打开浏览器或前台窗口。
- 含管理 token 的快捷方式和登录资料只放在被 Git 忽略的私人目录或用户明确指定的位置，不提交到公开仓库。
