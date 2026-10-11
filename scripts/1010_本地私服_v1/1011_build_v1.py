"""可复验的代码版本指纹；不启动服务、不访问角色数据库。"""
import hashlib
from pathlib import Path

def build_id(root):
    root = Path(root)
    folder = root / 'scripts/1010_本地私服_v1'
    files = [path for path in folder.glob('*.py') if '验收' not in path.name and '测试' not in path.name]
    files.append(root / 'scripts/1011_网络规则_v1.json')
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.relative_to(root).as_posix().encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()
