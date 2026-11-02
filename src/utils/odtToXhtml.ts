import { StyleInfo } from './odtParser';

export interface ConvertResult {
  xhtml: string;
  css: string;
  imageFiles: Map<string, Uint8Array>;
}

function getAttr(el: Element, ...names: string[]): string {
  for (const name of names) {
    const val = el.getAttribute(name);
    if (val) return val;
  }
  // Try namespace-aware lookup
  for (const name of names) {
    const parts = name.split(':');
    if (parts.length === 2) {
      const nsMap: Record<string, string> = {
        'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
        'xlink': 'http://www.w3.org/1999/xlink',
        'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
        'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
        'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
        'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
        'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
      };
      const ns = nsMap[parts[0]];
      if (ns) {
        const val = el.getAttributeNS(ns, parts[1]);
        if (val) return val;
      }
    }
  }
  return '';
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function convertODTToXHTML(
  contentXml: string,
  stylesMap: Map<string, StyleInfo>,
  autoStylesMap: Map<string, StyleInfo>,
  images: Map<string, Uint8Array>
): ConvertResult {
  const parser = new DOMParser();
  const doc = parser.parseFromString(contentXml, 'application/xml');
  
  // Find body element
  let body: Element | null = null;
  const allElements = doc.getElementsByTagName('*');
  for (let i = 0; i < allElements.length; i++) {
    if (allElements[i].localName === 'body') {
      body = allElements[i];
      break;
    }
  }

  if (!body) {
    return { xhtml: '<p>Empty document</p>', css: '', imageFiles: new Map() };
  }

  const cssRules: string[] = [];
  const imageFiles = new Map<string, Uint8Array>();
  let imageCounter = 0;

  // Generate CSS from styles
  stylesMap.forEach((style, name) => {
    const safeName = name.replace(/[^a-zA-Z0-9_-]/g, '_');
    const rules: string[] = [];
    
    if (style.bold) rules.push('font-weight: bold');
    if (style.italic) rules.push('font-style: italic');
    if (style.underline) rules.push('text-decoration: underline');
    if (style.strikethrough) rules.push('text-decoration: line-through');
    if (style.fontSize) rules.push(`font-size: ${style.fontSize}`);
    if (style.color) rules.push(`color: ${style.color}`);
    if (style.backgroundColor) rules.push(`background-color: ${style.backgroundColor}`);
    if (style.textAlign) rules.push(`text-align: ${style.textAlign}`);
    if (style.fontFamily) rules.push(`font-family: '${style.fontFamily}'`);
    
    if (rules.length > 0) {
      cssRules.push(`.${safeName} { ${rules.join('; ')}; }`);
    }
  });

  function processNode(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return escapeHtml(node.textContent || '');
    }

    if (node.nodeType !== Node.ELEMENT_NODE) return '';

    const el = node as Element;
    const localName = el.localName;

    // Text namespace elements
    switch (localName) {
      case 'h': {
        const level = parseInt(getAttr(el, 'text:outline-level') || '1');
        const safeLevel = Math.min(Math.max(level, 1), 6);
        const content = processChildren(el);
        return `<h${safeLevel}>${content}</h${safeLevel}>`;
      }
      case 'p': {
        const styleName = getAttr(el, 'text:style-name');
        const safeStyle = styleName.replace(/[^a-zA-Z0-9_-]/g, '_');
        const content = processChildren(el);
        if (safeStyle) {
          return `<p class="${safeStyle}">${content}</p>`;
        }
        return `<p>${content}</p>`;
      }
      case 'span': {
        const styleName = getAttr(el, 'text:style-name');
        const safeStyle = styleName.replace(/[^a-zA-Z0-9_-]/g, '_');
        const content = processChildren(el);
        
        // Check if style indicates bold/italic etc.
        const styleInfo = autoStylesMap.get(styleName) || stylesMap.get(styleName);
        if (styleInfo) {
          let result = content;
          if (styleInfo.bold) result = `<strong>${result}</strong>`;
          if (styleInfo.italic) result = `<em>${result}</em>`;
          if (styleInfo.underline) result = `<span style="text-decoration: underline">${result}</span>`;
          if (styleInfo.strikethrough) result = `<span style="text-decoration: line-through">${result}</span>`;
          if (styleInfo.color) result = `<span style="color: ${styleInfo.color}">${result}</span>`;
          if (safeStyle) {
            return `<span class="${safeStyle}">${result}</span>`;
          }
          return result;
        }
        
        if (safeStyle) {
          return `<span class="${safeStyle}">${content}</span>`;
        }
        return content;
      }
      case 'a': {
        const href = getAttr(el, 'xlink:href') || '#';
        const content = processChildren(el);
        return `<a href="${escapeHtml(href)}">${content}</a>`;
      }
      case 'list': {
        const content = processChildren(el);
        return `<ul>${content}</ul>`;
      }
      case 'list-item': {
        const content = processChildren(el);
        return `<li>${content}</li>`;
      }
      case 'ordered-list': {
        const content = processChildren(el);
        return `<ol>${content}</ol>`;
      }
      case 'line-break': {
        return '<br/>';
      }
      case 'tab': {
        return '<span style="white-space:pre">\t</span>';
      }
      case 's': {
        const count = parseInt(getAttr(el, 'text:c') || '1');
        return '\u00A0'.repeat(count);
      }
      case 'note': {
        const noteClass = getAttr(el, 'text:note-class');
        const noteBody = el.getElementsByTagName('*');
        let noteContent = '';
        for (let i = 0; i < noteBody.length; i++) {
          if (noteBody[i].localName === 'note-body' || noteBody[i].localName === 'note-citation') {
            noteContent += processChildren(noteBody[i]);
          }
        }
        if (noteClass === 'footnote') {
          return `<span class="footnote"><sup>${noteContent}</sup></span>`;
        }
        return `<span class="endnote"><sup>${noteContent}</sup></span>`;
      }
      case 'note-citation': {
        const content = processChildren(el);
        return `<sup>${content}</sup>`;
      }
      case 'note-body': {
        const content = processChildren(el);
        return `<span class="note-body">${content}</span>`;
      }
    }

    // Draw namespace (images)
    if (localName === 'frame') {
      // Look for draw:image child
      let imageHref = '';
      const children = el.getElementsByTagName('*');
      for (let i = 0; i < children.length; i++) {
        if (children[i].localName === 'image') {
          imageHref = getAttr(children[i], 'xlink:href');
          break;
        }
      }
      
      if (imageHref) {
        const newImageName = `img_${imageCounter++}.jpg`;
        
        // Try to find image data with different path patterns
        let imageData = images.get(imageHref);
        if (!imageData) imageData = images.get('./' + imageHref);
        if (!imageData) imageData = images.get(imageHref.replace('./', ''));
        
        if (imageData) {
          imageFiles.set(`OEBPS/images/${newImageName}`, imageData);
        }
        
        const width = getAttr(el, 'svg:width') || '100%';
        const cssWidth = width.includes('%') ? width : `max-width: ${width}`;
        return `<div class="image-container"><img src="images/${newImageName}" alt="image" style="${cssWidth}; width: 100%;"/></div>`;
      }
      return processChildren(el);
    }

    if (localName === 'image') {
      const href = getAttr(el, 'xlink:href');
      if (href) {
        const newImageName = `img_${imageCounter++}.jpg`;
        
        let imageData = images.get(href);
        if (!imageData) imageData = images.get('./' + href);
        if (!imageData) imageData = images.get(href.replace('./', ''));
        
        if (imageData) {
          imageFiles.set(`OEBPS/images/${newImageName}`, imageData);
        }
        
        return `<div class="image-container"><img src="images/${newImageName}" alt="image" style="width: 100%;"/></div>`;
      }
      return '';
    }

    // Table elements
    if (localName === 'table') {
      const content = processChildren(el);
      return `<table class="doc-table">${content}</table>`;
    }
    if (localName === 'table-row') {
      const content = processChildren(el);
      return `<tr>${content}</tr>`;
    }
    if (localName === 'table-cell') {
      const content = processChildren(el);
      const colSpan = getAttr(el, 'table:number-columns-spanned') || '1';
      if (parseInt(colSpan) > 1) {
        return `<td colspan="${colSpan}">${content}</td>`;
      }
      return `<td>${content}</td>`;
    }
    if (localName === 'table-header-rows') {
      const content = processChildren(el);
      return `<thead>${content}</thead>`;
    }

    // Section
    if (localName === 'section') {
      return processChildren(el);
    }

    // Default: process children
    return processChildren(el);
  }

  function processChildren(node: Node): string {
    let result = '';
    for (let i = 0; i < node.childNodes.length; i++) {
      result += processNode(node.childNodes[i]);
    }
    return result;
  }

  const xhtml = processChildren(body);
  
  const css = `
/* Document styles */
body {
  font-family: Georgia, 'Times New Roman', serif;
  line-height: 1.6;
  margin: 1em;
  color: #333;
}

h1 { font-size: 2em; margin: 1em 0 0.5em; font-weight: bold; }
h2 { font-size: 1.5em; margin: 0.8em 0 0.4em; font-weight: bold; }
h3 { font-size: 1.3em; margin: 0.6em 0 0.3em; font-weight: bold; }
h4 { font-size: 1.1em; margin: 0.5em 0 0.3em; font-weight: bold; }
h5 { font-size: 1em; margin: 0.4em 0 0.2em; font-weight: bold; }
h6 { font-size: 0.9em; margin: 0.3em 0 0.2em; font-weight: bold; }

p { margin: 0.5em 0; text-indent: 0; }

ul, ol { margin: 0.5em 0; padding-left: 2em; }
li { margin: 0.2em 0; }

table.doc-table {
  border-collapse: collapse;
  width: 100%;
  margin: 1em 0;
}
table.doc-table td {
  border: 1px solid #ccc;
  padding: 0.5em;
}

.image-container {
  text-align: center;
  margin: 1em 0;
}
.image-container img {
  max-width: 100%;
  height: auto;
}

a { color: #0066cc; text-decoration: underline; }

.footnote { font-size: 0.8em; vertical-align: super; }
.endnote { font-size: 0.8em; vertical-align: super; }
.note-body { font-size: 0.85em; }

${cssRules.join('\n')}
`.trim();

  return { xhtml, css, imageFiles };
}
