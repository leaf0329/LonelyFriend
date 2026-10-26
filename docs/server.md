# Linux 服务器部署

部署目标为 Ubuntu 24.04 LTS amd64、Python 3.12，目录固定为 `/opt/lonelyfriend`。AstrBot 和 NapCat 由两个 systemd 服务管理，开机启动，进程异常退出后自动重启。服务使用独立的 `lonelyfriend` 系统用户。

2 核 2 GB 可作为少量私聊和群聊的试运行配置。建议保留 Swap，并观察内存、交换活动和回复延迟；Swap 只能缓冲偶发峰值。记忆向量模型在线程数上遵循 `min(4, CPU 数)`，在这类服务器上使用两条线程。

## 从本机迁移

先停止本机 LonelyFriend，再创建迁移包：

```powershell
.\scripts\manage.ps1 -Action Stop
.\.venv\Scripts\python.exe scripts/build_linux_bundle.py --output .cache/deploy-linux/lonelyfriend.tar.gz
```

迁移包包含角色、名单、模型凭据、SQLite 聊天和记忆快照、模型文件，以及四个聊天和空间插件。SQLite 使用备份接口并检查快照完整性，不复制正在变化的 WAL 文件。Windows 虚拟环境、QQ 运行包、历史测试数据库、B 站和小黑盒插件不进入迁移包。

**迁移包包含私人数据，不能上传到公开仓库或分享给他人。** 输出位置限制在被 Git 忽略的 `.cache/`。原本机数据保留，可用于回退。

通过 SFTP 将迁移包和以下两个固定版本安装包传到服务器。Linux QQ 下载地址和 NapCat 版本记录于 `sources.lock.json`：

- `NapCat.Shell.zip`，NapCat 4.18.41。
- `QQ.deb`，Linux QQ 3.2.34-53644，amd64。

复制仓库中的 `deploy/linux/install.sh` 到服务器，使用三个安装包的绝对路径执行：

```bash
sudo bash install.sh /path/lonelyfriend.tar.gz /path/NapCat.Shell.zip /path/QQ.deb
```

若允许角色自主公开发说说，显式追加 `--enable-qzone-publish`。这会设置北京时间每天 12:00、21:00 各一次，前后随机浮动 45 分钟。自动评论仍关闭，自动发布不采集私聊或群聊原文。未传入此选项时自动发布关闭。

安装器用于首次迁移，发现现有 `data/cmd_config.json` 后会停止，避免覆盖在线数据。它只安装聊天、记忆、主动聊天和空间相关插件；AstrBot 自身声明的公共运行依赖仍正常安装。

## 管理与 QQ 登录

服务端的管理面板、QQ 登录页面和 OneBot 接口只监听 `127.0.0.1`，无需将 6185、6099、6199 端口向公网开放。使用 SSH 本地转发访问：

```bash
ssh -N -L 16185:127.0.0.1:6185 -L 16099:127.0.0.1:6099 -i /path/server-key.pem root@SERVER_IP
```

连接保持期间，管理面板为 `http://127.0.0.1:16185`，QQ 登录管理为 `http://127.0.0.1:16099/webui/`。QQ 登录令牌和面板密码沿用迁移前的 `data/cuteq-private.json`，不要把该文件或令牌放进公开文档。

Windows SSH 要求私钥仅供当前账户等受信任主体访问。如果因文件权限拒绝读取密钥，需要先配置文件权限，或使用支持密钥登录的 SSH 客户端。无需更改服务器 SSH 权限或开放更多端口。

首次在 Linux 上运行需要为机器人 QQ 扫码确认。登录成功后空间插件复用同一个 QQ 的登录状态。通过管理面板聊天可以先验证模型，但这不能代替真实 QQ 收发和空间发布验证。

## 维护

```bash
sudo systemctl status lonelyfriend lonelyfriend-napcat
sudo systemctl restart lonelyfriend
sudo systemctl restart lonelyfriend-napcat
sudo journalctl -u lonelyfriend -n 80 --no-pager
sudo journalctl -u lonelyfriend-napcat -n 80 --no-pager
```

日志可能包含账号、聊天和管理入口，不要直接公开粘贴。查看实时内存和交换活动可使用 `free -h`、`vmstat 1`；长期出现频繁换入换出时，应评估资源占用，而不是继续扩大 Swap。

运行 `.venv/bin/python scripts/verify_server.py --model` 可检查管理鉴权、插件范围、管理员空间命令、QQ 登录状态、原文数据库完整性，并实际调用模型和本地语义模型；它不会发送 QQ 消息或发布说说。`--model` 会消耗少量模型 API 额度。回归测试使用迁移包附带的历史配置测试样本，无需在服务器保留完整 Git 历史。

迁移后修改名单使用 `data/cuteq-access.json`，执行 `.venv/bin/python scripts/set_access.py`；修改后重启聊天服务。完整停止两个服务使用 `sudo systemctl stop lonelyfriend lonelyfriend-napcat`。

恢复本机前，先停止服务器上的两个服务，再启动本机版本，避免同一个 QQ 账号在两处重复运行。此部署不提供持续的双向记忆同步。
