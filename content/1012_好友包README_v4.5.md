# 好友专用包 v4.5：只用于加入，不能开服

适用 Windows 10/11 64位。包含客户端、资源、补丁工具和 Radmin VPN 安装器，没有服务端、Python或角色数据库。存档保存在房主电脑；房主需要下载完整包并先开服。

## 下载和解压

从 [好友包 Release](https://github.com/yushen100/xxtjd-lan-play/releases/tag/play-friend-2026-10-12-v4.5) 下载：

- `1012_rea_friend_v4.5_001.zip` 至 `1012_rea_friend_v4.5_026.zip`，共26卷，约734MiB。
- `1012_friend_parts_v4.5.json`。
- `1012_extract_friend_v4.5.ps1` 和 `1012_extract_friend_v4.5.bat`。

保持原文件名，放在数据盘同一个目录，预留至少5GB空间。双击 BAT，等待校验和解压完成，生成 `REA好友端_v4.5`。缺卷或损坏时重下明确报错的文件；目标目录已有内容时使用新目录。不要手动逐卷解压，不混用完整包或旧版附件。Source code 不是运行包；公开仓库可直接下载。

## 加入朋友的服务器

1. 请房主先开服，并提供他的局域网 IPv4 或 Radmin 26.x地址。
2. 同一路由器网络可直接连接；异地运行 `一键启动/0_安装RadminVPN(异地联机).bat`，由本人确认安装与许可，然后加入房主提供的网络，确认双方在线。网络密码私下传递。
3. 双击 `一键启动/2_好友加入.bat`，填写**房主的 IP**，不要填自己的地址或127.0.0.1。
4. 使用自己的2–16位字母、数字、下划线账号名，不与别人重名。首次复制客户端和打补丁可能需要几分钟，等待完成再操作。
5. 进入房主房间，准备、选人、打一局。双方核对结算，退出重进确认存档。更换房主地址前关闭本工具启动的客户端。

## 连不上时

先核对房主已开服、双方网络在线和 IP 正确。好友 PowerShell 可执行 `Test-NetConnection 房主IP -Port 18799` 检查登录连接；战斗连接另查18800，服务连接另查18798。端口不通请房主检查防火墙和网卡范围，不要反复重装客户端。

详细故障表和参考步骤见 [玩家联机指南 v4.5](https://github.com/yushen100/xxtjd-lan-play/blob/main/content/1012_玩家联机指南_v4.5.md)，解压后的包内也有指南。

请等附件上传完整后再下载，不要下载不完整的包。完整依据：下载下面对应套装全部文件，运行BAT，全部分卷大小和SHA-256及解压文件校验通过。Source code不算部署附件。

### friend 完整下载清单

共26个ZIP，编号001至026连续无缺号，合计约734.1MiB。

- `1012_rea_friend_v4.5_001.zip`
- `1012_rea_friend_v4.5_002.zip`
- `1012_rea_friend_v4.5_003.zip`
- `1012_rea_friend_v4.5_004.zip`
- `1012_rea_friend_v4.5_005.zip`
- `1012_rea_friend_v4.5_006.zip`
- `1012_rea_friend_v4.5_007.zip`
- `1012_rea_friend_v4.5_008.zip`
- `1012_rea_friend_v4.5_009.zip`
- `1012_rea_friend_v4.5_010.zip`
- `1012_rea_friend_v4.5_011.zip`
- `1012_rea_friend_v4.5_012.zip`
- `1012_rea_friend_v4.5_013.zip`
- `1012_rea_friend_v4.5_014.zip`
- `1012_rea_friend_v4.5_015.zip`
- `1012_rea_friend_v4.5_016.zip`
- `1012_rea_friend_v4.5_017.zip`
- `1012_rea_friend_v4.5_018.zip`
- `1012_rea_friend_v4.5_019.zip`
- `1012_rea_friend_v4.5_020.zip`
- `1012_rea_friend_v4.5_021.zip`
- `1012_rea_friend_v4.5_022.zip`
- `1012_rea_friend_v4.5_023.zip`
- `1012_rea_friend_v4.5_024.zip`
- `1012_rea_friend_v4.5_025.zip`
- `1012_rea_friend_v4.5_026.zip`
- `1012_friend_parts_v4.5.json`
- `1012_extract_friend_v4.5.ps1`
- `1012_extract_friend_v4.5.bat`

说明附件：`1012_friend_README_v4.5.md`、`1012_release_notes_v4.5.md`，供阅读，不替代上述必需文件。
