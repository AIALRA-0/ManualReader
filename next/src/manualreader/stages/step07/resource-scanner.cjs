'use strict';

const fs = require('node:fs');
const vm = require('node:vm');

const vendors = new Map();

function parserFor(vendor, options = {}) {
  if (!vendors.has(vendor)) {
    const sandbox = {window: {}};
    vm.runInNewContext(fs.readFileSync(vendor, 'utf8'), sandbox, {filename: vendor});
    vendors.set(vendor, sandbox.window.ReaderMarkdownVendor);
  }
  const V = vendors.get(vendor);
  return new V.MarkdownIt({html: true, linkify: true, breaks: true, typographer: false, ...options})
    .use(V.footnote).use(V.tasks, {enabled: false}).use(V.deflist);
}

const blank = text => text.replace(/[^\r\n]/g, ' ');
const literal = text => text.replace(/[.*+?^$(){}|[\]\\]/g, '\\$&');
const environments = 'equation\\*?|align\\*?|aligned|alignedat|gather\\*?|gathered|multline\\*?|split|[pbBvV]?matrix|cases|array';
const opening = new RegExp('\\\\(?:[([]|begin\\{(' + environments + ')\\})|\\$+', 'g');
const overlap = (a, b) => a.start < b.end && b.start < a.end;

function lineIndex(text) {
  const starts = [0], ends = [];
  for (const eol of text.matchAll(/\r\n|\n|\r/g)) {
    ends.push(eol.index);
    starts.push(eol.index + eol[0].length);
  }
  ends.push(text.length);
  return {
    starts, ends,
    span(map) { return {start: starts[map[0]], end: ends[map[1] - 1]}; },
    containing(offset) {
      let lo = 0, hi = starts.length;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (starts[mid] <= offset) lo = mid; else hi = mid;
      }
      return lo;
    }
  };
}

function mask(text, spans, placeholder = '') {
  let at = 0, result = '';
  for (const {start, end} of [...spans].sort((a, b) => a.start - b.start)) {
    if (end <= at) continue;
    const from = Math.max(start, at), hidden = blank(text.slice(from, end));
    result += text.slice(at, from) + (from === start && placeholder ? placeholder + hidden.slice(1) : hidden);
    at = end;
  }
  return result + text.slice(at);
}

function escaped(text, at) {
  let count = 0;
  while (at > 0 && text[--at] === '\\') ++count;
  return count % 2 !== 0;
}

// Reader's parser supplies Markdown boundaries; the frozen text supplies exact offsets.
function markdown(text, parser, lines) {
  const opaque = [], images = [], mermaid = [], code = [];
  const rule = (stage, name) => stage.ruler.__rules__.find(r => r.name === name).fn;
  const reference = rule(parser.block, 'reference');
  parser.block.ruler.at('reference', (state, start, end, silent) => {
    const yes = reference(state, start, end, silent);
    if (yes && !silent) opaque.push(lines.span([start, state.line]));
    return yes;
  });
  const env = {};
  for (const token of parser.parse(text, env)) {
    if (!['code_block', 'fence', 'html_block'].includes(token.type)) continue;
    const span = lines.span(token.map);
    if (token.type !== 'html_block' || /^\s*(?:>\s*)*<(?:pre|code|script|style)\b|^\s*(?:>\s*)*<!--/i.test(text.slice(span.start))) {
      opaque.push(span);
    }
    if (token.type === 'fence') {
      const close = new RegExp('^\\s*(?:>\\s*)*' + literal(token.markup[0]) + '{' + token.markup.length + ',}\\s*$');
      const last = token.map[1] - 1;
      const closed = last > token.map[0] && close.test(text.slice(lines.starts[last], lines.ends[last]));
      const body = token.content.trim();
      const kind = token.info.trim().split(/\s+/)[0].toLowerCase() === 'mermaid' ? 'mermaid' : 'code';
      (kind === 'mermaid' ? mermaid : code).push({...span, kind,
        key: kind === 'code' ? token.info.trim() + '\n' + body : body,
        status: closed && body ? 'ok' : 'parse_error'});
    }
  }
  let active;
  for (const name of ['backticks', 'autolink', 'html_inline', 'image', 'link']) {
    const native = rule(parser.inline, name);
    parser.inline.ruler.at(name, (state, silent) => {
      const start = state.pos, count = state.tokens.length;
      const capture = !silent && active && active.text === state.src;
      const labelEnd = name === 'link' && capture ? parser.helpers.parseLinkLabel(state, start, true) : -1;
      const yes = native(state, silent);
      if (!capture || !yes) return yes;
      const span = {start: active.at + start, end: active.at + state.pos};
      if (name === 'image') {
        const token = state.tokens.slice(count).find(t => t.type === 'image');
        if (token) images.push({...span, kind: 'image', media_path: token.attrGet('src'), status: 'ok'});
      } else if (name === 'link') {
        // The link label may itself contain an image or a formula.
        const destination = active.at + labelEnd + 1;
        if (labelEnd >= 0 && destination < span.end) opaque.push({start: destination, end: span.end});
      } else opaque.push(span);
      return yes;
    });
  }
  const prose = mask(text, opaque);
  for (const chunk of prose.matchAll(/[^\r\n]+(?:(?:\r\n|\r|\n)(?![ \t]*(?:\r\n|\r|\n|$))[^\r\n]+)*/g)) {
    active = {text: chunk[0], at: chunk.index};
    parser.inline.parse(active.text, parser, env, []);
  }
  for (const m of prose.matchAll(/https?:\/\/[^\s<>]+|<(pre|code)\b[^>]*>[\s\S]*?<\/\1\s*>/gi)) {
    if (!m[1] && images.some(i => i.start <= m.index && m.index < i.end)) continue;
    opaque.push({start: m.index, end: m.index + m[0].length});
  }
  return {opaque, images: images.filter(i => !opaque.some(o => overlap(o, i))), mermaid, code};
}

function math(text, opaque, lines) {
  const prose = mask(text, opaque), resources = [];
  let cursor = 0;
  for (const m of prose.matchAll(opening)) {
    const start = m.index, token = m[0], from = start + token.length;
    if (start < cursor || escaped(text, start) || (token[0] === '$' && token.length > 2)) continue;
    const dollar = token === '$', env = m[1];
    const blockDelimiter = token === '$$' || token === '\\[' || !!env;
    const line = lines.containing(start);
    if (dollar && (!prose[from] || /\s|\$/.test(prose[from]))) continue;
    const closer = env ? '\\end{' + env + '}' : ({'\\(': '\\)', '\\[': '\\]', '$': '$', '$$': '$$'})[token];
    const candidates = new RegExp(literal(closer), 'g');
    candidates.lastIndex = from;
    let end = null, hit;
    while ((hit = candidates.exec(prose))) {
      if (dollar && hit.index >= lines.ends[line]) break;
      const finish = hit.index + closer.length;
      if (escaped(text, hit.index) || token[0] === '$' && (prose[hit.index - 1] === '$' || prose[finish] === '$')) continue;
      if (dollar && (/\s/.test(prose[hit.index - 1] || '') || /[\w]/.test(prose[finish] || ''))) break;
      if ((token === '\\(' || token === '\\[') &&
          new RegExp('(?<!\\\\)' + literal(token)).test(prose.slice(from, hit.index))) break;
      if (opaque.some(p => overlap(p, {start, end: finish}))) break;
      end = finish;
      break;
    }
    const isolatedStart = /^\s*(?:>\s*)*$/.test(text.slice(lines.starts[line], start));
    if (end === null) {
      if (!dollar) {
        const stop = Math.max(from, lines.ends[line]);
        resources.push({start, end: stop, kind: blockDelimiter && isolatedStart ? 'formula_display' : 'formula_inline',
          key: text.slice(start, stop), status: 'parse_error'});
        cursor = stop;
      }
      continue;
    }
    const isolated = isolatedStart && !text.slice(end, lines.ends[lines.containing(end - 1)]).trim();
    const body = env ? text.slice(start, end) : text.slice(from, end - closer.length);
    resources.push({start, end, kind: blockDelimiter && isolated ? 'formula_display' : 'formula_inline',
      key: body.trim(), status: body.trim() ? 'ok' : 'parse_error'});
    cursor = end;
  }
  return resources;
}

function scan(text, vendor, details = false) {
  const lines = lineIndex(text);
  const parser = parserFor(vendor);
  const first = markdown(text, parser, lines);
  const formulas = math(text, [...first.opaque, ...first.images], lines);
  const found = [...first.mermaid, ...first.images, ...formulas, ...(details ? first.code : [])], headings = [], blocks = [];
  // Hide TeX pipes when asking Reader's Markdown parser for table boundaries.
  const structure = parserFor(vendor).parse(mask(text, formulas.filter(r => r.status === 'ok'), 'x'), {});
  for (const [i, token] of structure.entries()) {
    if (details && token.level === 0 && token.map && token.type !== 'inline') {
      blocks.push({...lines.span(token.map), type: token.type});
    }
    if (token.type === 'table_open') {
      const span = lines.span(token.map);
      found.push({...span, kind: 'table', key: text.slice(span.start, span.end).trim(), status: 'ok'});
    }
    if (token.type === 'heading_open') {
      const {start} = lines.span(token.map), lineEnd = lines.ends[token.map[0]];
      const name = formulas.some(f => f.start >= start && f.start < lineEnd) ?
        text.slice(start, lineEnd).replace(/^\s*(?:>\s*)*#{1,6}(?:[ \t]+|$)/, '').replace(/[ \t]+#+[ \t]*$/, '').trim() :
        (structure[i + 1]?.content || '').trim();
      headings.push({start, name});
    }
  }
  const masked = mask(text, [...first.opaque, ...first.images, ...formulas]);
  for (const m of masked.matchAll(/!\[[^\]\r\n]*\]\([^\r\n]*/g)) {
    if (!escaped(text, m.index)) {
      const end = m.index + m[0].trimEnd().length;
      found.push({start: m.index, end, kind: 'image', key: text.slice(m.index, end), status: 'parse_error'});
    }
  }
  const codepoints = new Map([[0, 0]]);
  let utf16 = 0, cp = 0;
  for (const char of text) {
    utf16 += char.length;
    codepoints.set(utf16, ++cp);
  }
  for (const row of found) {
    row.heading = headings.findLast(h => h.start <= row.start)?.name ?? null;
    row.start = codepoints.get(row.start);
    row.end = codepoints.get(row.end);
    if (row.start === undefined || row.end === undefined) throw Error('Invalid Unicode boundary');
  }
  found.sort((a, b) => a.start - b.start || b.end - a.end || a.kind.localeCompare(b.kind));
  const links = [];
  if (details) {
    const plain = parserFor(vendor, {html: false}).parse(mask(text, formulas.filter(r => r.status === 'ok')), {});
    for (const block of plain) {
      for (const token of block.children || []) {
        if (token.type === 'link_open') links.push(token.attrGet('href'));
      }
    }
  }
  return details ? {resources: found, links, blocks: blocks.map(b => ({...b, start: codepoints.get(b.start),
    end: codepoints.get(b.end)})), headings: headings.map(h => ({...h, start: codepoints.get(h.start)}))} : found;
}

if (require.main === module) {
  const source = fs.readFileSync(0, 'utf8');
  const output = process.argv[3] === '--batch'
    ? JSON.parse(source).map(text => scan(text, process.argv[2], true))
    : scan(source, process.argv[2], process.argv[3] === '--details');
  process.stdout.write(JSON.stringify(output));
}
module.exports = {scan};
