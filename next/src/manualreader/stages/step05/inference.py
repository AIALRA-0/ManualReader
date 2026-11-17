"""Submit one structured request through the verified local Codex provider."""

from importlib.resources import files
import json
import os
import subprocess

from ...artifacts import read_json as read, write_json as write
from ...transport import PROFILE
from ..step04.writer_transport import MarkdownTransport
from .contracts import SCHEMAS, digest
from ..step03.cache import atomic


PROMPTS={
    '05': ('step05','STEP05-map.txt','132ba79f9e00e243a3fb7f59176cbf4deea5f762b74959cc3b758c274cc584de'),
    '06A':('step06','STEP06A-gap.txt','fe1a7e1cd75fd989052611169191df81a749ca64cd35e076c3c8f28c6a45719d'),
    '06B':('step06','STEP06B-depth.txt','cf8848d70a7c16ae8295351caf2b0ff287c0184cde5a9b97f252df0959b54932'),
}


def prompt(stage):
    module,name,expected=PROMPTS[stage]
    value=files(f'manualreader.stages.{module}').joinpath(name).read_bytes()
    if digest(value)!=expected:
        raise ValueError(f'{stage}：冻结提示词哈希不一致')
    return value


class StructuredTransport(MarkdownTransport):
    def __init__(self, folder, raw, system, images, stage):
        super().__init__(folder,raw,system,images)
        self.stage=stage
        self.response_id=None

    def prepare(self, body):
        request, hashes=super().prepare(body)
        request['text']={'format':{'type':'json_schema','name':'reader_'+self.stage.lower(),
                                  'strict':True,'schema':SCHEMAS[self.stage]}}
        write(self.folder/'dispatch.json',{'stage':self.stage,'image_sha256':hashes})
        return request,hashes

    def start(self):
        super().start()
        transport=self
        base=self.server.RequestHandlerClass

        class ReceiptHandler(base):
            def do_POST(self):
                stream=self.wfile

                class Observe:
                    def write(self, raw):
                        if raw.startswith(b'data: ') and raw.find(b'response.created',0,72)>=0:
                            try:
                                identifier=json.loads(raw[6:])['response']['id']
                                transport.response_id=identifier
                                write(transport.folder/'provider-id.json',{'id':identifier})
                            except (ValueError,KeyError):
                                pass
                        return stream.write(raw)

                    def flush(self):
                        return stream.flush()

                self.wfile=Observe()
                try:
                    return super().do_POST()
                finally:
                    self.wfile=stream

        self.server.RequestHandlerClass=ReceiptHandler
        return self


def invoke(folder, stage, payload, images, cli):
    status=folder/'outcome.json'
    if status.exists():
        value=read(status)
        if value.get('state')=='complete' or (folder/'dispatch.json').exists():
            return value
    if (folder/'dispatch.json').exists():
        return {'state':'interrupted_after_dispatch','reply':{}}
    folder.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    atomic(folder/'request.json',raw.encode())
    system=prompt(stage)
    prompt_path=folder/'prompt.txt'
    atomic(prompt_path,system)
    if not cli:
        raise RuntimeError('找不到已登录的 Codex 命令')
    runner=StructuredTransport(folder,raw,system.decode(),images,stage).start()
    output=folder/'first-message.json'
    args=[cli,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json','--color','never',
          '-m',PROFILE['model'],'-c','model_reasoning_effort="high"','-c','service_tier="default"',
          '-c','web_search="disabled"','-c','approval_policy="never"','-s','read-only','-C',str(folder),
          '-c','model_instructions_file='+json.dumps(str(prompt_path)),
          '-c','openai_base_url='+json.dumps(f'http://127.0.0.1:{runner.server.server_port}'),
          '-o',str(output)]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use',
                 'workspace_dependencies','hooks','fast_mode','image_generation'):
        args+=['--disable',flag]
    env={k:v for k,v in os.environ.items() if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID','PYTHONPATH')
         and not k.startswith(('MANUALREADER_TOKEN','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING'))}
    error=None
    try:
        with (folder/'stderr.txt').open('wb') as err:
            cp=subprocess.run(args+['-'],input=raw.encode(),stdout=subprocess.DEVNULL,stderr=err,
                              env=env,timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if cp.returncode:
            error=f'CLI {cp.returncode}'
    except (OSError,subprocess.TimeoutExpired) as exc:
        error=str(exc)
    finally:
        runner.stop()
    response=next((r['response'] for r in runner.receipts if r.get('response')), {})
    actual=runner.first_text or (output.read_bytes() if output.exists() else b'') or ''.join(runner.partial_text).encode()
    atomic(folder/'raw-response.txt',actual)
    try:
        parsed=json.loads(actual)
    except (ValueError,UnicodeError):
        parsed={}
    valid=(isinstance(parsed,dict) and set(parsed)==set(SCHEMAS[stage]['required']) and
           all(isinstance(v,list) for v in parsed.values()))
    completed=(valid and response.get('status')=='completed' and response.get('model')==PROFILE['model'])
    result={'state':'complete' if completed else 'model_error','reply':parsed if completed else {},
            'provider_responses':runner.receipts,'response':response,'response_id':runner.response_id,
            'profile':PROFILE,'prompt_sha256':digest(system),'cli_error':error}
    write(status,result)
    return result
