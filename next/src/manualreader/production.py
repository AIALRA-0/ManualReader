"""Serve Next chapters through the Reader's existing authenticated website."""

from argparse import ArgumentParser
import os
from pathlib import Path
import shutil
import sys

from .stages.finish.native_ui import install
from .stages.finish.preview import SAMPLE, project_native
from .stepfinal_server import server_handler


def configure_static(reader, static):
    shutil.copytree(reader/'static', static)
    script=install(static,reader)
    (static/'reader-postprocess-preview.js').write_bytes(script)


def load_backend(reader):
    if not os.environ.get('MANUAL_READER_PUBLIC_ORIGIN','').startswith('https://'):
        raise ValueError('线上 Reader 必须提供 HTTPS 地址')
    if not os.environ.get('MANUAL_READER_PROXY_SECRET') or not os.environ.get('MANUAL_READER_OWNERS'):
        raise ValueError('线上 Reader 需要现有登录代理的验证配置')
    sys.path.insert(0,str(reader))
    import reader_server

    return reader_server


def project(backend, run, sample):
    if not SAMPLE.fullmatch(sample):
        raise ValueError('Reader 项目编号无效')
    # This path installs a new generated project. Existing projects remain
    # managed by the Reader's normal workspace and save routes.
    identity='next-final-'+sample
    if backend.mpath(identity).exists():
        raise FileExistsError('请为新材料运行指定一个未使用的样本编号')
    result=project_native(run,sample,backend.LIBRARY.parent,backend,run/'original-inputs'/'projects',
                          destination_library=backend.LIBRARY)
    from reader_library import folder_for_new_project
    with backend.CATALOG_LOCK:
        result['folder_id']=folder_for_new_project(backend.LIBRARY/'library-index.json',
                                                   backend.library_sources(names_only=True),identity)
    return result


def main(argv=None):
    parser=ArgumentParser(description=__doc__)
    parser.add_argument('--reader',type=Path,required=True)
    commands=parser.add_subparsers(dest='command',required=True)
    assets=commands.add_parser('assets')
    assets.add_argument('--output',type=Path,required=True)
    serve=commands.add_parser('serve')
    serve.add_argument('--runs',type=Path,required=True)
    ingest=commands.add_parser('project')
    ingest.add_argument('--run',type=Path,required=True)
    ingest.add_argument('--sample',required=True)
    args=parser.parse_args(argv)
    reader=args.reader.resolve(strict=True)
    if args.command=='assets':
        configure_static(reader,args.output.resolve())
        return
    backend=load_backend(reader)
    if args.command=='project':
        result=project(backend,args.run.resolve(strict=True),args.sample)
        print(result['material_id'],flush=True)
        return
    script=(backend.STATIC/'reader-postprocess-preview.js').read_bytes()
    backend.core.read_state()
    server=backend.ReaderHTTPServer(('127.0.0.1',backend.core.PORT),
                                    server_handler(backend,script,production_runs=args.runs.resolve()))
    with server:
        server.serve_forever()


if __name__=='__main__':
    main()
