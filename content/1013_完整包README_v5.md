# 完整联机包 v5：包含房主端和好友端

适用 Windows 10/11 64位。这个包可以自己开服，也可以加入朋友的服务器；包含客户端、资源、服务端、Python、补丁工具和 Radmin VPN 安装器。无需另装 Git 或 Codex。

## 下载和解压

从 [完整包 Release](https://github.com/yushen100/xxtjd-lan-play/releases/tag/play-full-2026-10-12-v5) 下载：

- `1013_rea_full_v5_001.zip` 至 `1013_rea_full_v5_026.zip`，共26卷，约746MiB。
- `1013_full_parts_v5.json`。
- `1013_extract_full_v5.ps1` 和 `1013_extract_full_v5.bat`。

所有文件保持原名，放在数据盘同一个目录。预留至少5GB空间，双击 BAT，等待全部分卷和解压文件校验通过，生成 `REA完整联机版_v5`。目标目录已有文件时入口会停止，请改用新目录，不覆盖已有存档。不要手动逐卷解压，不混用好友包或旧版附件。GitHub 的 Source code 不是运行包；公开仓库可直接下载。

## 房主操作

1. 同一局域网使用房主当前网卡的 IPv4；异地双方通过包内 `一键启动/0_安装RadminVPN(异地联机).bat` 安装 Radmin VPN，由本人确认许可，然后房主建网络、好友加入，确认双方在线。使用房主的26.x地址。
2. 运行 `一键启动/0_检查部署.bat`。
3. 同一网络运行 `一键启动/1_房主开服(局域网).bat`；异地运行 `一键启动/1b_房主开服(虚拟局域网).bat`。填写房主地址和账号名。
4. 房主防火墙允许 TCP 18798、18799、18800 入站，范围覆盖实际局域网或 Radmin 网卡。不要做公网端口映射。
5. 告诉好友房主 IP，进入游戏建房，等待好友准备。开服期间保持电脑和网络运行。

账号名为2–16位字母、数字、下划线，每人不同。账号没有独立密码，只与可信朋友联机。已有存档先备份再换版本；不要同时开两份服务器。

## 作为好友加入

房主先开服，再运行 `一键启动/2_好友加入.bat`，输入房主 IP 和自己的账号名。首次复制客户端与打补丁可能需要几分钟。更换房主地址前关闭本工具启动的客户端再运行入口。

## 参考指导与验收

详细步骤与故障表见 [玩家联机指南 v5](https://github.com/yushen100/xxtjd-lan-play/blob/main/content/1013_玩家联机指南_v5.md)，解压后的包内也有该指南。

请等附件上传完整后再下载，不要下载不完整的包。完整依据：下载下面对应套装全部文件，运行BAT，全部分卷大小和SHA-256及解压文件校验通过。Source code不算部署附件。

### full 完整下载清单

共26个ZIP，编号001至026连续无缺号，合计约746.4MiB。

- `1013_rea_full_v5_001.zip`
- `1013_rea_full_v5_002.zip`
- `1013_rea_full_v5_003.zip`
- `1013_rea_full_v5_004.zip`
- `1013_rea_full_v5_005.zip`
- `1013_rea_full_v5_006.zip`
- `1013_rea_full_v5_007.zip`
- `1013_rea_full_v5_008.zip`
- `1013_rea_full_v5_009.zip`
- `1013_rea_full_v5_010.zip`
- `1013_rea_full_v5_011.zip`
- `1013_rea_full_v5_012.zip`
- `1013_rea_full_v5_013.zip`
- `1013_rea_full_v5_014.zip`
- `1013_rea_full_v5_015.zip`
- `1013_rea_full_v5_016.zip`
- `1013_rea_full_v5_017.zip`
- `1013_rea_full_v5_018.zip`
- `1013_rea_full_v5_019.zip`
- `1013_rea_full_v5_020.zip`
- `1013_rea_full_v5_021.zip`
- `1013_rea_full_v5_022.zip`
- `1013_rea_full_v5_023.zip`
- `1013_rea_full_v5_024.zip`
- `1013_rea_full_v5_025.zip`
- `1013_rea_full_v5_026.zip`
- `1013_full_parts_v5.json`
- `1013_extract_full_v5.ps1`
- `1013_extract_full_v5.bat`

说明附件：`1013_full_README_v5.md`、`1013_release_notes_v5.md`，供阅读，不替代上述必需文件。


采用新目录升级：[存档备份、迁移及回退指南](https://github.com/yushen100/xxtjd-lan-play/blob/main/content/1013_新目录升级指南_v5.md)。正式入口28组、两端补丁、重复开服及合成存档迁移已通过；真实多电脑联机、资源下载压力和长时间运行仍待验。
