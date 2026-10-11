"""保留基础分卷，只打包变化文件；部署到新目录，不覆盖真实存档。"""
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
BASE=ROOT/'drafts/1011_部署分卷_v1'
OUT=ROOT/'drafts/1011_模块更新包_v2'
OUT.mkdir(parents=True,exist_ok=True)
target=OUT/'1011_rea_modules_v2.zip'
if target.exists():
    raise SystemExit('更新包已存在，禁止覆盖')
old=json.loads((BASE/'1011_deploy_parts_v1.json').read_text(encoding='utf-8'))
entries={}
original={}
for part in old['parts']:
    path=BASE/part['file']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=part['sha256']:
        raise RuntimeError('基础卷校验失败:'+part['file'])
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.filename=='content/1011_部署文件清单_v1.json':
                continue
            entries[info.filename]=archive.read(info)
            original[info.filename]=hashlib.sha256(entries[info.filename]).hexdigest()
for name in list(entries):
    if name=='README.md' or name.startswith('scripts/') or name.startswith('content/'):
        path=ROOT/name
        if path.is_file():
            entries[name]=path.read_bytes()
for path in (ROOT/'scripts/1010_本地私服_v1').glob('*.py'):
    entries[path.relative_to(ROOT).as_posix()]=path.read_bytes()
for name in ('scripts/1011_构建模块更新_v2.py','content/1011_模块拆分与复验_v1.md','content/1011_大厅拆分与部署_v2.md','content/1011_模块边界验收_v1.json','content/1011_大厅拆分验收_v1.json','content/1011_部署复验_v2.json'):
    entries[name]=(ROOT/name).read_bytes()
final=[{'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in sorted(entries.items())]
updates={name:data for name,data in entries.items() if hashlib.sha256(data).hexdigest()!=original.get(name)}
updates['content/1011_部署文件清单_v1.json']=json.dumps(final,ensure_ascii=False,indent=2).encode('utf-8')
with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for name,data in updates.items():
        if re.search(rb'(?:github_pat_|gh[pousr]_|sk-proj-)[A-Za-z0-9_]{16,}',data):
            raise RuntimeError('疑似凭据:'+name)
        archive.writestr(name,data)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
result={'base_parts':old['parts'],'update':{'file':target.name,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()},'update_paths':sorted(updates),'file_count':len(final)+1}
(OUT/'1011_deploy_manifest_v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
source=(ROOT/'scripts/1011_install_modules_v2.ps1').read_text(encoding='utf-8-sig')
(OUT/'1011_install_modules_v2.ps1').write_text(source,encoding='utf-8-sig',newline='\r\n')
(OUT/'1011_install_modules_v2.bat').write_bytes(b'@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp01011_install_modules_v2.ps1"\r\npause\r\n')
(ROOT/'content/1011_模块部署清单_v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'update_bytes':target.stat().st_size,'changed_files':len(updates),'total_files':len(final)+1},ensure_ascii=False))
