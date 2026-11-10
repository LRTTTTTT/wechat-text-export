# 微信文字聊天静默导出

Windows 10/11 x64、微信 4.x 的本机文字聊天归档工具。安装后在 Windows 登录时启动无窗口程序；每次微信账号登录，读取此电脑已有的聊天记录并导出一次。安装时微信已经登录，会立即补齐。登录期间不持续同步；新消息等下次登录保存。

## 安装到另一台电脑

1. 将 `release/v0.2.0/WeChatTextExport-Setup-0.2.0.exe` 复制到目标电脑，双击安装。安装包离线可用，目标电脑不需要 Python。
2. 可在微信登录前安装。程序会在后台等待首次登录。若微信已登录而安装器无法识别数据目录，安装期间会要求选择包含微信账号目录的 `xwechat_files` 文件夹。运行期间不会弹出选择窗口。
3. 文件保存到自动发现的 `<微信数据目录>\txt_chat\<账号ID>\`。每个联系人或群聊一个 UTF-8 TXT，文件名含稳定会话标识。更改昵称不会产生第二份文件。索引在账号目录的 `_index.sqlite`；密钥缓存位于 `txt_chat\_state\keys`，使用当前 Windows 用户的 DPAPI 加密。

例如微信数据目录为 `D:\聊天记录\xwechat_files`，输出便位于其下的 `txt_chat`。另一台电脑会使用其自己的微信数据位置和账号，不需要输入用户名、微信 ID、数据库路径或数据库密钥。只有自动发现失败时才需要手工选择数据目录。

## 内容与限制

TXT 逐条记录时间、发送者和内容；图片、语音、视频、文件等写类型占位符。包含文件传输助手，跳过公众号及系统通知。按消息 ID 去重；微信后来删除或撤回的已导出内容会保留，补入较早消息时按时间重新排列。更新 TXT 使用临时文件替换，失败时保留原文件。日志不写聊天正文。

只导出该电脑已保存的消息；手机独有记录需要先迁移到电脑。微信数据库格式未公开，微信升级后可能需要适配。导出目录与原始微信数据在同一盘，不能替代异盘备份。程序只读访问微信进程和数据库；微信需以普通用户权限运行。

## 维护

- 任务计划程序任务名：`WeChatTextExport`，当前用户登录时自动启动。
- 程序与无正文日志：`%LOCALAPPDATA%\WeChatTextExport\versions\0.2.0\` 和 `%LOCALAPPDATA%\WeChatTextExport\export.log`。
- 手动修复数据目录：在安装目录运行 `WeChatTextExport.exe repair`，仅此显式操作可能打开文件夹选择窗口。
- 停止或启动：`Stop-ScheduledTask -TaskName WeChatTextExport` / `Start-ScheduledTask -TaskName WeChatTextExport`。
- 取消自启动：`Unregister-ScheduledTask -TaskName WeChatTextExport -Confirm:$false`。这不会删除 TXT 和索引。

已安装程序在每次微信登录时检查 `LRTTTTTT/wechat-text-export` 的 GitHub Releases。仅当发布清单的 Ed25519 签名及程序 ZIP 的 SHA-256 均通过验证时，才会静默切换到新版本；启动失败会恢复旧任务。网络不可用时继续使用本地版本。离线更新可直接运行新版安装包。更新请求不上传聊天内容、密钥或本机路径。发布前需先建立并公布该仓库；仓库未就绪时检查失败仅写日志。

## 开发与发布

本仓库包含公开源代码及经审核的 `wechatauto-replica` 1.2.10 数据库读取模块，启动时核对其 SHA-256。上游许可在 `vendor/LICENSE.wechatauto-replica`。不提交聊天记录、索引、密钥或签名私钥。

在构建电脑的 `.venv` 安装 `requirements.txt`、PyInstaller 6.22.0，并安装 Inno Setup 6.7.3。运行 `build.ps1` 执行测试、生成冻结程序、签名更新清单，并生成离线安装包。签名私钥是 `%USERPROFILE%\Documents\WeChatTextExportPublisher\signing-key.dpapi`，受当前 Windows 用户 DPAPI 保护；发布用的加密离线备份可执行 `python tools\release.py backup --destination <离线介质路径>`，密码至少 16 字符，备份文件不可覆盖。

每个 GitHub Release 的标签是 `v<版本>`，上传 `release/v<版本>/` 中的程序 ZIP、`update.json`、`update.sig` 和安装包。更新源码中的版本号与 Inno Setup 版本号后重新构建。发布前在本机验证安装和回滚。
