"""部署只读检查和版本指纹，避免 PowerShell 的内联 Python 引号差异。"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--client', action='store_true')
    parser.add_argument('--build-only', action='store_true')
    args = parser.parse_args()
    root = Path(args.root)
    spec = importlib.util.spec_from_file_location('deploy_build', Path(__file__).with_name('1011_build_v1.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if args.build_only:
        print(mod.build_id(root))
        return
    if sys.version_info < (3, 10):
        raise SystemExit('需要 Python 3.10 及以上；部署包已带 Python 3.12')
    sys.path.insert(0, str(root / 'tools/python-libs'))
    import google.protobuf
    proto_spec = importlib.util.spec_from_file_location('deploy_pb', root / 'scripts/1010_本地私服_v1/1010_native_v1_pb2.py')
    proto = importlib.util.module_from_spec(proto_spec)
    proto_spec.loader.exec_module(proto)
    assert proto.Message.FromString(proto.Message(type=3069).SerializeToString()).type == 3069
    required = ['content/1010_微端完整数据_v1.json', 'content/1010_原生资源清单_v1.json', 'scripts/1011_网络规则_v1.json']
    if args.client:
        required += ['assets/1010_本地客户端_v1/hdjt.exe', 'assets/1010_本地客户端_v1/hdjt_Data/Managed/Assembly-CSharp.dll', 'assets/1010_本地客户端_v1/config_wd.json', 'tools/mono-cecil-0.11.6/package/lib/netstandard2.0/Mono.Cecil.dll']
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise SystemExit('缺少文件：'+', '.join(missing))
    resources_missing = []
    if args.client:
        names = json.loads((root / 'content/1010_原生资源清单_v1.json').read_text(encoding='utf-8'))['AssetBundleNames']
        resources_missing = [name for _, name in names if not (root / 'assets/1010_本地客户端_v1/res' / name).is_file()]
    if resources_missing:
        raise SystemExit(f'客户端缺少 {len(resources_missing)} 个资源，请重新解压完整部署包')
    print(json.dumps({'python_version': sys.version.split()[0], 'protobuf_version': google.protobuf.__version__, 'build_id': mod.build_id(root), 'resources_missing': len(resources_missing)}))

if __name__ == '__main__':
    main()
