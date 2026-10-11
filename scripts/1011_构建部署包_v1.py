"""按显式清单构建干净部署 ZIP；不包含真实存档、凭据、日志和本机配置。"""
import hashlib
import importlib.util
import json
import os
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'drafts/1011_部署包_v1'
OUT.mkdir(parents=True, exist_ok=True)
TARGET = OUT / '1011_rea_deploy_v1.zip'
if TARGET.exists():
    raise SystemExit('同名部署包已经存在；请使用新的版本，禁止覆盖已经校验的包')
files = {}

def add(source, target=None):
    source = ROOT / source
    name = target or source.relative_to(ROOT).as_posix()
    if not source.is_file():
        raise FileNotFoundError(source)
    if any(part.lower() in ('localstorage', '.git', '.codex', 'chrome-profile', '__pycache__') for part in Path(name).parts) or source.suffix.lower() in ('.pyc', '.log', '.sqlite3') or source.name.lower() in ('cookies.dat', 'config.cfg', 'output_log.txt'):
        return
    if name in files:
        raise ValueError('部署路径重复：'+name)
    files[name] = source

def tree(folder):
    for directory, children, names in os.walk(ROOT / folder, followlinks=False):
        children[:] = [name for name in children if not (Path(directory)/name).is_junction() and name not in ('__pycache__', 'LocalStorage')]
        for name in names:
            add((Path(directory)/name).relative_to(ROOT))

add('README.md')
tree('scripts/1010_本地私服_v1')
for name in ('1010_启动本地私服_v1.ps1', '1010_局域网开服_v1.ps1', '1010_好友加入_v1.ps1', '1010_切换本地启动_v1.ps1', '1011_窗口可调整大小_v1.ps1', '1011_部署检查_v1.ps1', '1011_运行配置示例_v1.json', '1011_网络规则_v1.json', '1011_网络规则_v1.ps1', '1011_客户端检查_v1.ps1', '1011_启动复用验收_v1.ps1', '1011_执行验收_v1.ps1', '1011_备份存档_v1.py', '1011_停止本项目服务_v1.ps1', '1011_构建部署包_v1.py'):
    add('scripts/'+name)
for name in ('0_检查部署.bat', '0_安装RadminVPN(异地联机).bat', '1_房主开服(局域网).bat', '1b_房主开服(虚拟局域网).bat', '2_好友加入.bat', '工具_运行隔离验收.bat', '工具_百人压测.bat', '工具_停止本项目服务.bat', '工具_备份角色存档.bat', '说明_1011部署_v1.txt'):
    add('一键启动/'+name)
for name in ('1010_微端完整数据_v1.json', '1010_原生资源清单_v1.json', '1011_部署与复验_v1.md', '1011_规则与功能总览_v2.md', '1011_可靠性验收_v1.json', '1011_百人承载验收_v1.json', '1011_启动复用验收_v1.json', '1010_本地经济验收_v1.json', '1010_成长验收_v1.json', '1010_本地房间验收_v1.json', '1010_帧调度验收_v1.json', '1010_原生战斗协议验收_v1.json', '1010_双端TCP战斗验收_v1.json'):
    add('content/'+name)
tree('tools/python')
tree('tools/python-libs/google')
add('tools/python-libs/protobuf-7.36.2.dist-info/LICENSE')
tree('tools/mono-cecil-0.11.6/package/lib/netstandard2.0')
add('tools/mono-cecil-0.11.6/package/Mono.Cecil.nuspec')
for name in ('Radmin_VPN_2.1.4951.1.exe', 'Radmin_VPN_EULA.html', 'Mono.Cecil_LICENSE.txt'):
    add('assets/1011_部署依赖_v1/'+name)
add('4399小小突击队.exe', 'assets/1011_部署依赖_v1/4399小小突击队.exe')
client = 'assets/1010_本地客户端_v1'
for name in ('hdjt.exe', 'dbghelp.dll', 'libcurl_ssl_r.dll', 'msvcr100.dll', 'node.dll', 'config_wd.json'):
    add(client+'/'+name)
tree(client+'/hdjt_Data')
tree(client+'/res')
# 包里放已核验的原始 DLL，首次开服 / 加入时按实际地址打补丁。
original = ROOT / 'drafts/1010_客户端补丁_v1/1010_Assembly-CSharp原件_v1.dll'
if hashlib.sha256(original.read_bytes()).hexdigest() != '5b3946c24b00131740fd69237956fa8b4d0f2eeca3fb1deb951d585287939af4':
    raise SystemExit('客户端原件 SHA256 不匹配')
files[client+'/hdjt_Data/Managed/Assembly-CSharp.dll'] = original

manifest = []
with zipfile.ZipFile(TARGET, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
    for index, (name, source) in enumerate(sorted(files.items()), 1):
        before = source.stat()
        data = source.read_bytes()
        if source.suffix.lower() in ('.py', '.ps1', '.json', '.md', '.txt', '.bat') and 'tools/python' not in name:
            if re.search(rb'(?:github_pat_|gh[pousr]_|sk-proj-)[A-Za-z0-9_]{16,}', data):
                raise ValueError('发现疑似凭据，停止构建：'+name)
        after = source.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError('构建期间文件有变化：'+name)
        archive.writestr(name, data)
        manifest.append({'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        if index % 500 == 0:
            print(f'已打包 {index}/{len(files)} 文件', flush=True)
    archive.writestr('content/1011_部署文件清单_v1.json', json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(TARGET) as archive:
    bad = archive.testzip()
    if bad:
        raise RuntimeError('ZIP 校验失败：'+bad)
digest = hashlib.file_digest(TARGET.open('rb'), 'sha256').hexdigest()
result = {'file': TARGET.name, 'bytes': TARGET.stat().st_size, 'sha256': digest, 'file_count': len(manifest)+1, 'uncompressed_bytes': sum(item['bytes'] for item in manifest), 'clean_database': True, 'zip_crc_verified': True}
(OUT / '1011_deploy_checksums_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
(OUT / '1011_deploy_sha256_v1.txt').write_text(digest+'  '+TARGET.name+'\n', encoding='utf-8')
(ROOT / 'content/1011_部署包清单_v1.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
