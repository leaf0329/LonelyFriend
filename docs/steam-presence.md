# Steam 活动监测

通过 Steam 客户端协议读取指定玩家的在线状态、当前游戏和 Rich Presence 活动文字，再由 AstrBot 向单个指定群通知。连接与本地化使用 [steam-user](https://github.com/DoctorMcKay/node-steam-user)，扫码登录使用 [steam-session](https://github.com/DoctorMcKay/node-steam-session)。依赖固定在 `services/steam-presence/package-lock.json`，不调用语言模型。

普通 Steam Web API 状态监控无法提供完整的游戏内活动描述。本功能使用 `getPersonas` 和 `requestRichPresence` 获取 Steam 返回的数据；游戏不提供活动、隐私设置不允许查看或读取失败时，不能保证获得客户端第二行文字。不会推测未上报的动作。

## 行为

- 默认每60秒检查一次。短于检查间隔且已恢复的变化可能遗漏。
- 比较在线状态、游戏ID和已成功读取的活动文字；同一次检测多个字段变化只发一条，连续相同状态不重复发送。
- 首次启动或重连后的首轮读取仅建立基线，不补发停机期间的旧变化。
- 不设置夜间静默；Steam监测的变化通知不属于每日热点审核流程。
- Steam断连和读取失败不作为目标玩家下线；活动暂不可读时保留原比较基线，不虚构“退出活动”。
- 发送前保存新观察状态，结果不明确时不自动重发。因此网络故障可能丢失一条通知。
- 只发送到配置中的目标群，且该群必须在QQ开放名单内。其他群不能查询该玩家活动；管理员可以私聊查询。

## 安装与登录

1. 准备 Node.js 22或更新的受支持版本。服务器部署可使用独立的 `runtime/node/bin/node`，不替换其他程序的运行环境。
2. 在 `services/steam-presence` 执行 `npm ci --ignore-scripts --no-audit --no-fund`。
3. 复制 `config/steam-presence.example.json` 到私有 `data/steam-presence.json`，填入17位SteamID、目标群、QQ平台ID与Node路径。目标群应事先加入 `data/cuteq-access.json`。
   服务器必须能访问Steam。直连不通时可在私有配置的 `proxy` 填写可用的HTTP或SOCKS5代理；它只作用于Steam组件，不修改其他服务的代理。含认证信息的代理地址不得公开提交。
4. 将 `plugins/astrbot_plugin_lonelyfriend_steam` 复制到 `data/plugins/`。首次安装可以先不重启聊天服务，完成独立登录与数据检查后再启用。
5. 在服务器项目目录启动登录：

```bash
runtime/node/bin/node --max-old-space-size=192 services/steam-presence/presence.cjs login data/steam-presence.json
```

终端只输出状态；二维码地址保存至受限的 `data/steam-presence/login-qr.json`，可在本机生成二维码供Steam手机应用扫码。不会要求在QQ或聊天里发送密码。授权后的客户端登录凭据保存在 `data/steam-presence/login.json`，仅供后台监听，不能公开上传或转发。

6. 重启聊天服务，或在已加载插件后由管理员发送 `/Steam重连`。扫码账号就是后台监听账号；不要在未验证活动读取前声称游戏细节监测已经可用。

### 可选的机场出口

用户授权后，可使用现有Clash/Mihomo订阅作为Steam组件的出口。服务器单独运行Mihomo，只在 `127.0.0.1:17789` 提供代理；不开TUN、不修改系统代理，不改变QQ、模型和其他插件的线路。订阅链接、节点与密码只放在 `data/steam-proxy/`，不提交Git；仅在私有 `data/steam-presence.json` 将 `proxy` 设置为 `http://127.0.0.1:17789`。

服务模板为 `deploy/linux/lonelyfriend-steam-proxy.service`，使用 `runtime/mihomo/mihomo` 与私有配置。订阅通过Mihomo的HTTP代理集合每日更新，使用已缓存节点启动；按Steam公开接口的可访问性选择节点，每小时检查一次。该流量消耗现有机场套餐，不需要在服务器安装Steam图形客户端。

回退时停止并禁用 `lonelyfriend-steam-proxy`，清空监测配置中的 `proxy` 后重新连接Steam组件即可。原有QQ和模型的网络配置不需要恢复。机场节点不可用或无法从服务器使用时，详细监测仍不能启用。

## 查询与维护

| 命令 | 用途 |
| --- | --- |
| `/Steam状态` | 在指定群或管理员私聊中查看监测健康、最近成功状态和读取时间 |
| `/Steam重连` | 管理员重新启动后台监听，首轮重新建立基线 |
| `/菜单 Steam` | 在聚合菜单中查找相关命令 |

`data/steam-presence.json` 的 `enabled` 设为 `false` 后重连可停止监听。令牌失效时需要重新扫码。停用插件会关闭它创建的监听子进程，QQ登录与聊天不随之退出。

监听进程的V8堆上限为192 MiB，该限制不等于整个进程的内存上限；Steam库与原生缓冲区还会占用额外内存。依赖加载时测得约175–185 MiB进程内存，长期连接的实际占用仍需登录后观察。

配置、登录凭据、最近状态库和缓存均属于私人运行数据，留在被Git忽略的 `data/` 与 `runtime/`。公开仓库仅保存代码、依赖锁定与空白示例。
