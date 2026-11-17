(function () {
  const article = document.querySelector('#draft');
  if (!article) return;
  const renderer = new window.ReaderMarkdownVendor.MarkdownIt({html: false, breaks: true});
  const original = article.textContent;
  const media = JSON.parse(document.querySelector('#verified-media').textContent);
  const defaultImage = renderer.renderer.rules.image;
  renderer.renderer.rules.image = (tokens, i, options, env, self) => {
    const token = tokens[i];
    const path = media[token.attrGet('src')];
    if (!path || !/^assets\/[a-f0-9]{64}\.png$/.test(path)) {
      return '<em>原稿引用的图像当前无法读取</em>';
    }
    token.attrSet('src', location.pathname.replace(/\/$/, '') + '/' + path);
    return defaultImage(tokens, i, options, env, self);
  };
  // Render display math as literal text so Markdown cannot consume TeX delimiters.
  renderer.block.ruler.before('paragraph', 'reader_display_math', (state, start, end, silent) => {
    const line = n => state.src.slice(state.bMarks[n] + state.tShift[n], state.eMarks[n]).trim();
    const first = line(start);
    const closer = first === '\\[' ? '\\]' : first === '$$' ? '$$' : '';
    if (!closer) return false;
    let last = start + 1;
    while (last < end && line(last) !== closer) last++;
    if (last === end) return false;
    if (silent) return true;
    const token = state.push('reader_display_math', '', 0);
    token.content = state.getLines(start, last + 1, state.blkIndent, false);
    state.line = last + 1;
    return true;
  });
  renderer.renderer.rules.reader_display_math = (tokens, i) =>
    '<pre class="math">' + renderer.utils.escapeHtml(tokens[i].content) + '</pre>';
  for (const [before, after] of [['\\(', '\\)'], ['$', '$']]) {
    renderer.inline.ruler.before('escape', 'reader_math_' + before, (state, silent) => {
      if (!state.src.startsWith(before, state.pos)) return false;
      const stop = state.src.indexOf(after, state.pos + before.length);
      if (stop <= state.pos + before.length || /\n/.test(state.src.slice(state.pos, stop))) return false;
      if (!silent) state.push('reader_inline_math','',0).content = state.src.slice(state.pos,stop+after.length);
      state.pos = stop + after.length;
      return true;
    });
  }
  renderer.renderer.rules.reader_inline_math = (tokens, i) =>
    '<code class="math">' + renderer.utils.escapeHtml(tokens[i].content) + '</code>';
  article.innerHTML = renderer.render(original);
  article.classList.add('rendered');
})();
