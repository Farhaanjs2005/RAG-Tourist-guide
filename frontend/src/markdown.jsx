import React from 'react';

// Small Markdown renderer for guide answers. It builds React elements
// (never innerHTML), so model or document text cannot inject HTML.
// Supports headings, bullet and numbered lists (one nesting level),
// paragraphs, **bold**, *italic*, `code`, and clickable [n] citations.

function inline(text, onCite, key = 'i') {
  const out = [];
  const pattern = /(\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*|_[^_\s][^_]*_|`[^`]+`|\[\d+\])/g;
  let last = 0, match, n = 0;
  while ((match = pattern.exec(text))) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const token = match[0], k = `${key}-${n++}`;
    if (token.startsWith('**') || token.startsWith('__')) out.push(<strong key={k}>{inline(token.slice(2, -2), onCite, k)}</strong>);
    else if (token.startsWith('`')) out.push(<code key={k}>{token.slice(1, -1)}</code>);
    else if (token.startsWith('[')) {
      const number = Number(token.slice(1, -1));
      out.push(onCite ? <button type="button" key={k} className="cite" onClick={() => onCite(number)} title={`Open source ${number}`}>{number}</button> : token);
    } else out.push(<em key={k}>{inline(token.slice(1, -1), onCite, k)}</em>);
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text, onCite }) {
  const lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
  const blocks = [];
  let paragraph = [], list = null;
  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ type: 'p', text: paragraph.join(' ') });
    paragraph = [];
  };
  const flushList = () => { if (list) blocks.push(list); list = null; };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    const indent = raw.match(/^\s*/)[0].replace(/\t/g, '  ').length;
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*•+]\s+(.*)$/);
    const numbered = line.match(/^\s*(\d+)[.)]\s+(.*)$/);
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    if (heading) { flushParagraph(); flushList(); blocks.push({ type: 'h', level: heading[1].length, text: heading[2] }); continue; }
    if (bullet || numbered) {
      flushParagraph();
      const ordered = !!numbered, content = bullet ? bullet[1] : numbered[2];
      if (list && indent >= 2 && list.items.length) {
        const parent = list.items[list.items.length - 1];
        parent.children = parent.children || { ordered, items: [] };
        parent.children.items.push({ text: content });
        continue;
      }
      if (!list || list.ordered !== ordered) { flushList(); list = { type: 'list', ordered, start: numbered ? Number(numbered[1]) : 1, items: [] }; }
      list.items.push({ text: content });
      continue;
    }
    if (list && indent >= 2 && list.items.length) { list.items[list.items.length - 1].text += ' ' + line.trim(); continue; }
    flushList();
    paragraph.push(line.trim());
  }
  flushParagraph(); flushList();

  const renderList = (l, key) => {
    const Tag = l.ordered ? 'ol' : 'ul';
    return <Tag key={key} start={l.ordered && l.start !== 1 ? l.start : undefined}>
      {l.items.map((item, i) => <li key={i}>{inline(item.text, onCite, `${key}-${i}`)}{item.children && renderList(item.children, `${key}-${i}-c`)}</li>)}
    </Tag>;
  };
  return <div className="markdown">{blocks.map((b, i) => {
    if (b.type === 'h') { const Tag = `h${Math.min(b.level + 2, 6)}`; return <Tag key={i}>{inline(b.text, onCite, `h${i}`)}</Tag>; }
    if (b.type === 'list') return renderList(b, `l${i}`);
    return <p key={i}>{inline(b.text, onCite, `p${i}`)}</p>;
  })}</div>;
}
