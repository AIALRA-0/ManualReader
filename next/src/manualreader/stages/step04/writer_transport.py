"""Reuse the authenticated Responses transport with plain Markdown output."""

from base64 import b64encode
import json
import os
import subprocess

from ...transport import PROFILE, Transport, save


class MarkdownTransport(Transport):
    def __init__(self, folder, raw, system, images):
        super().__init__(folder,raw,system,{}, {},list(enumerate(images)))
        self.images = images
        self.first_text = b''
        self.partial_text = []

    def prepare(self, body):
        with self.lock:
            earlier = self.receipts[:-1]
            if len(earlier) > 1 or any(r.get('status') not in (429, 503)
                                       or 'response' in r for r in earlier):
                raise ValueError('Writer already dispatched; keeping its first output')
            request, _ = self.prepare_vision(body) if self.images else super().prepare(body)
            request.pop('text', None)
            if len(request['input']) != 1:
                raise ValueError('Writer accepts only one fresh turn')
            return request, list(self.images)

    def prepare_vision(self, body):
        messages = list(body['input'])
        for index, item in enumerate(messages):
            if item.get('role')!='user' or not any(
                value.get('type')=='input_text' and value.get('text','').strip()==self.text.strip()
                for value in item.get('content',[])
            ):
                continue
            parts = [{'type':'input_image','image_url':'data:image/png;base64,'+b64encode(raw).decode('ascii')}
                     for raw in self.images.values()]
            messages[index] = {**item,'content':item['content']+parts}
            break
        return super().prepare({**body,'input':messages})

    def start(self):
        super().start()
        outer = self
        handler = self.server.RequestHandlerClass

        class CapturedHandler(handler):
            def do_POST(self):
                stream = self.wfile
                class Witness:
                    def flush(self):
                        return stream.flush()
                    def write(self, raw):
                        if raw.startswith(b'data: '):
                            try:
                                event = json.loads(raw[6:])
                                kind = event.get('type')
                                if kind=='response.output_text.delta':
                                    outer.partial_text.append(event['delta'])
                                if kind in ('response.completed','response.incomplete'):
                                    messages = event.get('response',{}).get('output',[])
                                    output = ''.join(part.get('text','') for item in messages
                                                     if item.get('type')=='message'
                                                     for part in item.get('content',[])
                                                     if part.get('type')=='output_text')
                                    if output:
                                        outer.first_text = output.encode('utf8')
                            except (ValueError,KeyError,TypeError):
                                pass
                        return stream.write(raw)
                self.wfile = Witness()
                try:
                    return super().do_POST()
                finally:
                    self.wfile = stream
        self.server.RequestHandlerClass = CapturedHandler
        return self


def invoke(folder, system, raw, images, cli):
    if not cli:
        raise RuntimeError('未找到已登录的本机 Codex 命令')
    image_root = folder/'assets'
    image_root.mkdir(exist_ok=True)
    for digest, image in images.items():
        path = image_root/f'{digest}.png'
        path.write_bytes(image)
    system_path = folder/'writer-prompt.txt'
    system_path.write_bytes(system)
    transport = MarkdownTransport(folder,raw,system.decode('utf8'),images).start()
    args = [cli,'exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check','--json','--color','never',
            '-m',PROFILE['model'],'-c','model_reasoning_effort="high"','-c','service_tier="default"',
            '-c','web_search="disabled"','-c','approval_policy="never"','-s','read-only','-C',str(folder),
            '-c','model_instructions_file='+json.dumps(str(system_path)),
            '-c','openai_base_url='+json.dumps(f'http://127.0.0.1:{transport.server.server_port}'),
            '-o',str(folder/'first-message.md')]
    for flag in ('apps','plugins','multi_agent','shell_tool','browser_use','computer_use',
                 'workspace_dependencies','hooks','fast_mode','image_generation'):
        args += ['--disable',flag]
    env = {k:v for k,v in os.environ.items() if k not in ('CODEX_THREAD_ID','CODEX_SESSION_ID','PYTHONPATH')
           and not k.startswith(('MANUALREADER_TOKEN','MANUAL_READER_TOKEN','MANUAL_READER_PAIRING'))}
    error = None
    with (folder/'model-events.jsonl').open('wb') as out, (folder/'stderr.txt').open('wb') as err:
        try:
            result = subprocess.run(args+['-'],input=raw.encode('utf8'),stdout=out,stderr=err,env=env,
                                    timeout=900,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:
                error = f'Codex CLI returned {result.returncode}'
        except (OSError,subprocess.TimeoutExpired) as exc:
            error = type(exc).__name__
        finally:
            transport.stop()
    markdown = folder/'first-message.md'
    body = markdown.read_bytes() if markdown.exists() else b''
    # The JSONL final assistant item preserves content without CLI-added line endings.
    for line in (folder/'model-events.jsonl').read_bytes().splitlines():
        try:
            packet = json.loads(line)
        except ValueError:
            continue
        item = packet.get('item') or {}
        if packet.get('type')=='item.completed' and item.get('type')=='agent_message':
            body = item['text'].encode('utf8')
            break
    body = transport.first_text or body or ''.join(transport.partial_text).encode('utf8')
    response = next((row['response'] for row in transport.receipts if row.get('response')), {})
    receipt = {'provider_responses':transport.receipts, 'response':response, 'cli_error':error,
               'model_profile':PROFILE}
    save(folder/'model-receipt.json',receipt)
    return body,receipt
