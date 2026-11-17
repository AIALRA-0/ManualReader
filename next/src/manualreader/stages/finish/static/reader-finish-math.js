(() => {
  const renderer=window.ReaderMarkdown;
  const render=renderer.render;
  function normalizeMathSource(source) {
    if (!/\\\\[\[(]/.test(source)) return source;
    const code=window.LessonMath.scan(source).protected.filter(range=>range.code);
    return source.replace(/(?<!\\)\\\\(?:\[([\s\S]*?)(?<!\\)\\\\\]|\(([\s\S]*?)(?<!\\)\\\\\))/g,
      (raw,display,inline,index)=>{
        if (code.some(({start,end})=>index<end&&index+raw.length>start)) return raw;
        let tex=display ?? inline;
        if (!/(?<!\\)\\[A-Za-z]/.test(tex) && /(?<!\\)\\\\[A-Za-z]/.test(tex)) {
          tex=tex.replace(/\\{2,}/g,slashes=>slashes.length%2?slashes:slashes.slice(slashes.length/2));
        }
        return display===undefined?'\\('+tex+'\\)':'\\['+tex+'\\]';
      });
  }
  renderer.normalizeMathSource=normalizeMathSource;
  renderer.render=(node,source,...rest)=>render(node,normalizeMathSource(String(source ?? '')),...rest);
})();
