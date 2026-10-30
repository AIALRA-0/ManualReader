/* 点击录音，把转写文字放回当前提问框；不自动发起回答，也不保存录音文件。 */
window.ReaderSpeech = (() => {
  let capability, pending, active;
  const attached=new WeakSet();
  const status=request=>pending ||= request('/api/speech/status').then(v=>capability=v).catch(()=>({enabled:false}));
  function append(input,text) {
    const at=input.selectionStart??input.value.length,end=input.selectionEnd??at;
    input.value=input.value.slice(0,at)+text+input.value.slice(end);input.dispatchEvent(new Event('input',{bubbles:true}));input.focus({preventScroll:true});input.setSelectionRange(at+text.length,at+text.length);
  }
  function attach(input,wrapper,materialId,request) {
    if(attached.has(input))return;attached.add(input);
    const tools=document.createElement('div');tools.className='speech-input-tools';
    const button=ReaderUI.decorate(document.createElement('button'),'mic','语音输入'),notice=document.createElement('span');button.type='button';button.className='icon-btn speech-button';tools.hidden=true;notice.className='speech-input-status';notice.setAttribute('role','status');tools.append(notice,button);wrapper.append(tools);
    const originMaterial=materialId();
    let recorder,stream,chunks=[],recorded=null,timer,startAt=0,cancelled=false;
    const release=()=>{clearTimeout(timer);stream?.getTracks().forEach(track=>track.stop());stream=null;if(active===stop)active=null;};
    const show=(text,busy=false)=>{notice.textContent=text;button.disabled=busy;button.setAttribute('aria-pressed',String(!!recorder&&recorder.state==='recording'));};
    const submit=async()=>{if(!recorded)return;if(!input.isConnected||materialId()!==originMaterial){recorded=null;release();return;}show('正在转写',true);try{
      const bytes=new Uint8Array(await recorded.arrayBuffer());let raw='';for(let i=0;i<bytes.length;i+=8192)raw+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const result=await request('/api/speech/transcribe',{method:'POST',headers:{'X-Reader-Material':originMaterial},body:JSON.stringify({audio_base64:btoa(raw),mime_type:recorded.type})});
      if(result.text&&input.isConnected&&materialId()===originMaterial){append(input,result.text);recorded=null;show('')}else show(result.message||'没有听到清楚的语音');
    }catch(error){show(error.message+'，点击重试')}finally{button.disabled=false;button.setAttribute('aria-label',recorded?'重试语音转写':'语音输入');button.dataset.tooltip=button.getAttribute('aria-label');}};
    const stop=()=>{if(recorder?.state==='recording'){show('正在结束录音');recorder.stop();}release();};
    button.onclick=async event=>{event.preventDefault();event.stopPropagation();if(recorder?.state==='recording')return stop();if(recorded)return submit();try{
      active?.();cancelled=false;stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,channelCount:1}});
      const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t=>MediaRecorder.isTypeSupported(t));recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});chunks=[];
      recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};recorder.onerror=()=>{cancelled=true;release();show('录音中断，请重试')};
      recorder.onstop=()=>{release();if(cancelled)return;recorded=new Blob(chunks,{type:recorder.mimeType});submit()};recorder.start(500);active=stop;startAt=Date.now();show('正在录音，再点一次结束');button.setAttribute('aria-pressed','true');timer=setTimeout(stop,44000);
    }catch(error){release();show(error.name==='NotAllowedError'?'请允许这个网站使用麦克风':'麦克风暂时无法使用');}};
    status(request).then(value=>{tools.hidden=!value.enabled||!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder});
    window.addEventListener('pagehide',()=>{cancelled=true;stop();recorded=null},{once:true});
  }
  return {attach,status};
})();
