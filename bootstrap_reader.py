"""首次启动时在部署目录安装依赖，之后复用独立环境。"""
from pathlib import Path
import os
import subprocess
import sys
import urllib.request
import venv

ROOT=Path(__file__).resolve().parent


def main():
    if sys.version_info<(3,10):raise SystemExit('请使用 Python 3.10 或以上版本')
    environment=ROOT/'.reader-venv';python=environment/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    if not python.exists():
        print('正在本目录创建独立 Python 环境',flush=True)
        try:venv.EnvBuilder(with_pip=True).create(environment)
        except subprocess.CalledProcessError:
            # 部分 Linux 发行版省略 ensurepip，改用 PyPA 的 pip 执行文件
            venv.EnvBuilder(with_pip=False).create(environment)
    requirements=ROOT/'requirements.txt';marker=environment/'.reader-requirements.txt'
    if not marker.exists() or marker.read_bytes()!=requirements.read_bytes():
        print('首次启动需要联网安装解析器依赖，后续启动直接复用',flush=True)
        has_pip=subprocess.run([str(python),'-m','pip','--version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        pip=[str(python),'-m','pip']
        if not has_pip:
            support=ROOT/'.reader-bootstrap';support.mkdir(exist_ok=True);archive=support/'pip.pyz'
            if not archive.exists():urllib.request.urlretrieve('https://bootstrap.pypa.io/pip/pip.pyz',archive)
            pip=[str(python),str(archive)]
        subprocess.run([*pip,'install','--disable-pip-version-check','-r',str(requirements)],check=True)
        marker.write_bytes(requirements.read_bytes())
    os.execv(str(python),[str(python),'-X','utf8',str(ROOT/'launch_reader.py'),*sys.argv[1:]])


if __name__=='__main__':main()
