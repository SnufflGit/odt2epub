#!/usr/bin/env node

/**
 * ODT → EPUB Converter CLI
 * 
 * Конвертация ODT документа в EPUB с сохранением форматирования.
 * Поддерживает JPEG/PNG обложку.
 * 
 * Использование:
 *   node convert.js -i document.odt -o output.epub [-c cover.jpg] [-t "Название"] [-a "Автор"] [-l ru]
 */

const fs = require('fs');
const path = require('path');
const { program } = require('commander');
const JSZip = require('jszip');
const { DOMParser } = require('@xmldom/xmldom');

// ============================================================
// CLI Setup
// ============================================================

program
  .name('odt2epub')
  .description('Конвертация ODT документа в EPUB с сохранением форматирования')
  .version('1.0.0')
  .requiredOption('-i, --input <path>', 'Путь к входному ODT файлу')
  .option('-c, --cover <path>', 'Путь к обложке (JPEG/PNG)')
  .option('-o, --output <path>', 'Путь для выходного EPUB файла')
  .option('-t, --title <string>', 'Название книги (по умолчанию из метаданных ODT)')
  .option('-a, --author <string>', 'Автор книги (по умолчанию из метаданных ODT)')
  .option('-l, --lang <code>', 'Язык книги (ISO 639-1)', 'ru')
  .parse(process.argv);

const opts = program.opts();

// ============================================================
// Utility functions
// ============================================================

function log(msg) {
  console.log(`  ${msg}`);
}

function logStep(step, msg) {
  console.log(`\n  [${step}] ${msg}`);
}

function logOk(msg) {
  console.log(`  ✅ ${msg}`);
}

function logError(msg) {
  console.error(`  ❌ ${msg}`);
}

function getAttr(el, ...names) {
  for (const name of names) {
    const val = el.getAttribute(name);
    if (val) return val;
  }
  for (const name of names) {
    const parts = name.split(':');
    if (parts.length === 2) {
      const nsMap = {
        'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
        'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
        'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
        'xlink': 'http://www.w3.org/1999/xlink',
        'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
        'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
        'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
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

function findChild(el, localName) {
  for (let i = 0; i < el.childNodes.length; i++) {
    const child = el.childNodes[i];
    if (child.nodeType === 1 && child.localName === localName) return child;
  }
  return null;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escapeXml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function generateUUID() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// ============================================================
// ODT Parser
// ============================================================

async function parseODT(filePath) {
  const buffer = fs.readFileSync(filePath);
  const zip = await JSZip.loadAsync(buffer);

  const contentXml = (await zip.file('content.xml')?.async('string')) || '';
  const stylesXml = (await zip.file('styles.xml')?.async('string')) || '';
  const metaXml = (await zip.file('meta.xml')?.async('string')) || '';

  const images = new Map();

  // Collect image paths
  const imagePaths = [];
  zip.forEach((relativePath, zipEntry) => {
    if (relativePath.startsWith('Pictures/') && !zipEntry.dir) {
      imagePaths.push(relativePath);
    }
  });

  // Load all images
  for (const imgPath of imagePaths) {
    const entry = zip.file(imgPath);
    if (entry) {
      const data = await entry.async('uint8array');
      images.set(imgPath, data);
    }
  }

  return { contentXml, stylesXml, metaXml, images };
}

// ============================================================
// Style Parser
// ============================================================

function parseStyles(stylesXml) {
  const stylesMap = new Map();
  if (!stylesXml) return stylesMap;

  const parser = new DOMParser();
  const doc = parser.parseFromString(stylesXml, 'application/xml');
  const allElements = doc.getElementsByTagName('*');

  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    if (el.localName !== 'style') continue;

    const name = getAttr(el, 'style:name');
    if (!name) continue;

    const family = getAttr(el, 'style:family');
    const parentStyle = getAttr(el, 'style:parent-style-name');

    const styleInfo = {
      name, family, parentStyle,
      bold: false, italic: false, underline: false, strikethrough: false,
      fontSize: '', color: '', backgroundColor: '', textAlign: '', fontFamily: '',
    };

    const textProps = findChild(el, 'text-properties');
    if (textProps) {
      const fontWeight = getAttr(textProps, 'fo:font-weight');
      const fontStyle = getAttr(textProps, 'fo:font-style');
      const underlineStyle = getAttr(textProps, 'style:text-underline-style');
      const lineThrough = getAttr(textProps, 'style:text-line-through-style');
      const textDecoration = getAttr(textProps, 'fo:text-decoration');

      styleInfo.bold = fontWeight === 'bold' || fontWeight === '700' || fontWeight === '900';
      styleInfo.italic = fontStyle === 'italic';
      styleInfo.underline = underlineStyle === 'solid' || (textDecoration || '').includes('underline');
      styleInfo.strikethrough = lineThrough === 'solid' || (textDecoration || '').includes('line-through');
      styleInfo.fontSize = getAttr(textProps, 'fo:font-size');
      styleInfo.color = getAttr(textProps, 'fo:color');
      styleInfo.backgroundColor = getAttr(textProps, 'fo:background-color');
      styleInfo.fontFamily = getAttr(textProps, 'style:font-name');
    }

    const paraProps = findChild(el, 'paragraph-properties');
    if (paraProps) {
      styleInfo.textAlign = getAttr(paraProps, 'fo:text-align');
    }

    stylesMap.set(name, styleInfo);
  }

  return stylesMap;
}

function parseContentStyles(contentXml) {
  const stylesMap = new Map();
  if (!contentXml) return stylesMap;

  const parser = new DOMParser();
  const doc = parser.parseFromString(contentXml, 'application/xml');
  const allElements = doc.getElementsByTagName('*');
  let inAutoStyles = false;

  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    if (el.localName === 'automatic-styles') { inAutoStyles = true; continue; }
    if (el.localName === 'body') { inAutoStyles = false; continue; }

    if (inAutoStyles && el.localName === 'style') {
      const name = getAttr(el, 'style:name');
      if (!name) continue;

      const family = getAttr(el, 'style:family');
      const parentStyle = getAttr(el, 'style:parent-style-name');

      const styleInfo = {
        name, family, parentStyle,
        bold: false, italic: false, underline: false, strikethrough: false,
        fontSize: '', color: '', backgroundColor: '', textAlign: '', fontFamily: '',
      };

      const textProps = findChild(el, 'text-properties');
      if (textProps) {
        const fontWeight = getAttr(textProps, 'fo:font-weight');
        const fontStyle = getAttr(textProps, 'fo:font-style');
        const underlineStyle = getAttr(textProps, 'style:text-underline-style');
        const lineThrough = getAttr(textProps, 'style:text-line-through-style');

        styleInfo.bold = fontWeight === 'bold' || fontWeight === '700' || fontWeight === '900';
        styleInfo.italic = fontStyle === 'italic';
        styleInfo.underline = underlineStyle === 'solid';
        styleInfo.strikethrough = lineThrough === 'solid';
        styleInfo.fontSize = getAttr(textProps, 'fo:font-size');
        styleInfo.color = getAttr(textProps, 'fo:color');
        styleInfo.backgroundColor = getAttr(textProps, 'fo:background-color');
        styleInfo.fontFamily = getAttr(textProps, 'style:font-name');
      }

      const paraProps = findChild(el, 'paragraph-properties');
      if (paraProps) {
        styleInfo.textAlign = getAttr(paraProps, 'fo:text-align');
      }

      stylesMap.set(name, styleInfo);
    }
  }

  return stylesMap;
}

function extractMeta(metaXml) {
  if (!metaXml) return { title: 'Untitled', author: 'Unknown', date: new Date().toISOString() };

  const parser = new DOMParser();
  const doc = parser.parseFromString(metaXml, 'application/xml');
  const allElements = doc.getElementsByTagName('*');
  let title = 'Untitled', author = 'Unknown', date = new Date().toISOString();

  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    if (el.localName === 'title' && el.textContent) title = el.textContent;
    if ((el.localName === 'creator' || el.localName === 'initial-creator') && el.textContent) author = el.textContent;
    if (el.localName === 'date' && el.textContent) date = el.textContent;
  }

  return { title, author, date };
}

// ============================================================
// ODT → XHTML Converter
// ============================================================

function convertODTToXHTML(contentXml, stylesMap, autoStylesMap, images) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(contentXml, 'application/xml');

  let body = null;
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

  const cssRules = [];
  const imageFiles = new Map();
  let imageCounter = 0;

  // Generate CSS from styles
  stylesMap.forEach((style, name) => {
    const safeName = name.replace(/[^a-zA-Z0-9_-]/g, '_');
    const rules = [];
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

  function processNode(node) {
    if (node.nodeType === 3) { // TEXT_NODE
      return escapeHtml(node.textContent || '');
    }
    if (node.nodeType !== 1) return ''; // ELEMENT_NODE

    const el = node;
    const localName = el.localName;

    switch (localName) {
      case 'h': {
        const level = parseInt(getAttr(el, 'text:outline-level') || '1');
        const safeLevel = Math.min(Math.max(level, 1), 6);
        return `<h${safeLevel}>${processChildren(el)}</h${safeLevel}>`;
      }
      case 'p': {
        const styleName = getAttr(el, 'text:style-name');
        const safeStyle = styleName.replace(/[^a-zA-Z0-9_-]/g, '_');
        const content = processChildren(el);
        return safeStyle ? `<p class="${safeStyle}">${content}</p>` : `<p>${content}</p>`;
      }
      case 'span': {
        const styleName = getAttr(el, 'text:style-name');
        const safeStyle = styleName.replace(/[^a-zA-Z0-9_-]/g, '_');
        const content = processChildren(el);
        const styleInfo = autoStylesMap.get(styleName) || stylesMap.get(styleName);
        if (styleInfo) {
          let result = content;
          if (styleInfo.bold) result = `<strong>${result}</strong>`;
          if (styleInfo.italic) result = `<em>${result}</em>`;
          if (styleInfo.underline) result = `<span style="text-decoration:underline">${result}</span>`;
          if (styleInfo.strikethrough) result = `<span style="text-decoration:line-through">${result}</span>`;
          if (styleInfo.color) result = `<span style="color:${styleInfo.color}">${result}</span>`;
          return safeStyle ? `<span class="${safeStyle}">${result}</span>` : result;
        }
        return safeStyle ? `<span class="${safeStyle}">${content}</span>` : content;
      }
      case 'a': {
        const href = getAttr(el, 'xlink:href') || '#';
        return `<a href="${escapeHtml(href)}">${processChildren(el)}</a>`;
      }
      case 'list':
        return `<ul>${processChildren(el)}</ul>`;
      case 'list-item':
        return `<li>${processChildren(el)}</li>`;
      case 'ordered-list':
        return `<ol>${processChildren(el)}</ol>`;
      case 'line-break':
        return '<br/>';
      case 'tab':
        return '<span style="white-space:pre">\t</span>';
      case 's': {
        const count = parseInt(getAttr(el, 'text:c') || '1');
        return '\u00A0'.repeat(count);
      }
      case 'frame': {
        let imageHref = '';
        const children = el.getElementsByTagName('*');
        for (let i = 0; i < children.length; i++) {
          if (children[i].localName === 'image') {
            imageHref = getAttr(children[i], 'xlink:href');
            break;
          }
        }
        if (imageHref) {
          const ext = path.extname(imageHref) || '.jpg';
          const newImageName = `img_${imageCounter++}${ext}`;
          let imageData = images.get(imageHref) || images.get('./' + imageHref) || images.get(imageHref.replace('./', ''));
          if (imageData) {
            imageFiles.set(`OEBPS/images/${newImageName}`, imageData);
          }
          const width = getAttr(el, 'svg:width') || '100%';
          const cssWidth = width.includes('%') ? width : `max-width:${width}`;
          return `<div class="image-container"><img src="images/${newImageName}" alt="image" style="${cssWidth};width:100%"/></div>`;
        }
        return processChildren(el);
      }
      case 'image': {
        const href = getAttr(el, 'xlink:href');
        if (href) {
          const ext = path.extname(href) || '.jpg';
          const newImageName = `img_${imageCounter++}${ext}`;
          let imageData = images.get(href) || images.get('./' + href) || images.get(href.replace('./', ''));
          if (imageData) {
            imageFiles.set(`OEBPS/images/${newImageName}`, imageData);
          }
          return `<div class="image-container"><img src="images/${newImageName}" alt="image" style="width:100%"/></div>`;
        }
        return '';
      }
      case 'table':
        return `<table class="doc-table">${processChildren(el)}</table>`;
      case 'table-row':
        return `<tr>${processChildren(el)}</tr>`;
      case 'table-cell': {
        const colSpan = getAttr(el, 'table:number-columns-spanned') || '1';
        return parseInt(colSpan) > 1
          ? `<td colspan="${colSpan}">${processChildren(el)}</td>`
          : `<td>${processChildren(el)}</td>`;
      }
      case 'table-header-rows':
        return `<thead>${processChildren(el)}</thead>`;
      case 'section':
        return processChildren(el);
      default:
        return processChildren(el);
    }
  }

  function processChildren(node) {
    let result = '';
    for (let i = 0; i < node.childNodes.length; i++) {
      result += processNode(node.childNodes[i]);
    }
    return result;
  }

  const xhtml = processChildren(body);

  const css = `body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.6; margin: 1em; color: #333; }
h1 { font-size: 2em; margin: 1em 0 0.5em; font-weight: bold; }
h2 { font-size: 1.5em; margin: 0.8em 0 0.4em; font-weight: bold; }
h3 { font-size: 1.3em; margin: 0.6em 0 0.3em; font-weight: bold; }
h4 { font-size: 1.1em; margin: 0.5em 0 0.3em; font-weight: bold; }
h5 { font-size: 1em; margin: 0.4em 0 0.2em; font-weight: bold; }
h6 { font-size: 0.9em; margin: 0.3em 0 0.2em; font-weight: bold; }
p { margin: 0.5em 0; }
ul, ol { margin: 0.5em 0; padding-left: 2em; }
li { margin: 0.2em 0; }
table.doc-table { border-collapse: collapse; width: 100%; margin: 1em 0; }
table.doc-table td { border: 1px solid #ccc; padding: 0.5em; }
.image-container { text-align: center; margin: 1em 0; }
.image-container img { max-width: 100%; height: auto; }
a { color: #0066cc; text-decoration: underline; }
${cssRules.join('\n')}`;

  return { xhtml, css, imageFiles };
}

// ============================================================
// EPUB Generator
// ============================================================

async function generateEpub(convertResult, options) {
  const zip = new JSZip();
  const uuid = generateUUID();
  const { xhtml, css, imageFiles } = convertResult;

  // mimetype (must be first, uncompressed)
  zip.file('mimetype', 'application/epub+zip', { compression: 'STORE' });

  // META-INF/container.xml
  zip.file('META-INF/container.xml', `<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>`);

  const manifestItems = [];
  const spineItems = [];

  // CSS
  zip.file('OEBPS/style.css', css);
  manifestItems.push(`    <item id="style" href="style.css" media-type="text/css"/>`);

  // Images from ODT
  let imageIdCounter = 0;
  imageFiles.forEach((data, filePath) => {
    const fileName = filePath.replace('OEBPS/', '');
    const ext = path.extname(fileName).toLowerCase().replace('.', '');
    const mediaType = ext === 'png' ? 'image/png' : 'image/jpeg';
    const id = `img_${imageIdCounter++}`;
    zip.file(filePath, data);
    manifestItems.push(`    <item id="${id}" href="${fileName}" media-type="${mediaType}"/>`);
  });

  // Cover image
  let hasCover = false;
  if (options.coverImage) {
    const coverExt = options.coverMimeType?.includes('png') ? 'png' : 'jpg';
    const coverMediaType = coverExt === 'png' ? 'image/png' : 'image/jpeg';
    const coverFileName = `cover.${coverExt}`;
    zip.file(`OEBPS/images/${coverFileName}`, options.coverImage);
    manifestItems.push(`    <item id="cover-image" href="images/${coverFileName}" media-type="${coverMediaType}" properties="cover-image"/>`);
    hasCover = true;
  }

  // Cover page
  if (hasCover) {
    const coverExt = options.coverMimeType?.includes('png') ? 'png' : 'jpg';
    const coverPage = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="${options.language}">
<head>
  <meta charset="UTF-8"/>
  <title>Cover</title>
  <style>body{margin:0;padding:0;text-align:center}img{max-width:100%;max-height:100vh}</style>
</head>
<body>
  <div id="cover"><img src="images/cover.${coverExt}" alt="Cover"/></div>
</body>
</html>`;
    zip.file('OEBPS/cover.xhtml', coverPage);
    manifestItems.push(`    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>`);
    spineItems.push(`    <itemref idref="cover"/>`);
  }

  // Main content
  const contentXhtml = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="${options.language}">
<head>
  <meta charset="UTF-8"/>
  <title>${escapeXml(options.title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div id="content">
    ${xhtml}
  </div>
</body>
</html>`;

  zip.file('OEBPS/chapter1.xhtml', contentXhtml);
  manifestItems.push(`    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>`);
  spineItems.push(`    <itemref idref="chapter1"/>`);

  // NCX
  const ncx = `<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:${uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>${escapeXml(options.title)}</text></docTitle>
  <navMap>
    <navPoint id="navpoint1" playOrder="1">
      <navLabel><text>${escapeXml(options.title)}</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>`;

  zip.file('OEBPS/toc.ncx', ncx);
  manifestItems.push(`    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>`);

  // content.opf
  const contentOpf = `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">urn:uuid:${uuid}</dc:identifier>
    <dc:title>${escapeXml(options.title)}</dc:title>
    <dc:creator>${escapeXml(options.author)}</dc:creator>
    <dc:language>${options.language}</dc:language>
    <dc:date>${options.date}</dc:date>
    <meta property="dcterms:modified">${new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')}</meta>
  </metadata>
  <manifest>
${manifestItems.join('\n')}
  </manifest>
  <spine toc="ncx">
${spineItems.join('\n')}
  </spine>
</package>`;

  zip.file('OEBPS/content.opf', contentOpf);

  // Generate EPUB buffer
  const buffer = await zip.generateAsync({
    type: 'nodebuffer',
    mimeType: 'application/epub+zip',
    compression: 'DEFLATE',
    compressionOptions: { level: 9 }
  });

  return buffer;
}

// ============================================================
// Main
// ============================================================

async function main() {
  console.log('\n  ╔══════════════════════════════════════════╗');
  console.log('  ║       ODT → EPUB Converter v1.0.0       ║');
  console.log('  ╚══════════════════════════════════════════╝\n');

  // Validate input file
  const inputPath = path.resolve(opts.input);
  if (!fs.existsSync(inputPath)) {
    logError(`Файл не найден: ${inputPath}`);
    process.exit(1);
  }

  if (!inputPath.toLowerCase().endsWith('.odt')) {
    logError(`Файл должен иметь расширение .odt: ${inputPath}`);
    process.exit(1);
  }

  // Validate cover if provided
  let coverBuffer = null;
  let coverMimeType = null;
  if (opts.cover) {
    const coverPath = path.resolve(opts.cover);
    if (!fs.existsSync(coverPath)) {
      logError(`Файл обложки не найден: ${coverPath}`);
      process.exit(1);
    }
    coverBuffer = fs.readFileSync(coverPath);
    const ext = path.extname(coverPath).toLowerCase();
    coverMimeType = ext === '.png' ? 'image/png' : 'image/jpeg';
    logOk(`Обложка: ${path.basename(coverPath)} (${coverMimeType})`);
  }

  // Determine output path
  const outputPath = opts.output
    ? path.resolve(opts.output)
    : inputPath.replace(/\.odt$/i, '.epub');

  // Step 1: Parse ODT
  logStep('1/5', 'Чтение ODT файла...');
  const odtContent = await parseODT(inputPath);
  const imageSize = odtContent.images.size;
  logOk(`ODT прочитан (изображений в документе: ${imageSize})`);

  // Step 2: Parse styles
  logStep('2/5', 'Анализ стилей...');
  const stylesMap = parseStyles(odtContent.stylesXml);
  const autoStylesMap = parseContentStyles(odtContent.contentXml);
  logOk(`Стили: ${stylesMap.size} из styles.xml, ${autoStylesMap.size} из content.xml`);

  // Extract metadata
  const meta = extractMeta(odtContent.metaXml);
  const title = opts.title || meta.title || 'Untitled';
  const author = opts.author || meta.author || 'Unknown';
  logOk(`Метаданные: "${title}" — ${author}`);

  // Step 3: Convert content
  logStep('3/5', 'Конвертация содержимого в XHTML...');
  const convertResult = convertODTToXHTML(
    odtContent.contentXml,
    stylesMap,
    autoStylesMap,
    odtContent.images
  );
  logOk(`XHTML сгенерирован (${convertResult.xhtml.length} символов, ${convertResult.imageFiles.size} изображений)`);

  // Step 4: Generate EPUB
  logStep('4/5', 'Сборка EPUB...');
  const epubBuffer = await generateEpub(convertResult, {
    title,
    author,
    date: meta.date,
    language: opts.lang,
    coverImage: coverBuffer,
    coverMimeType,
  });
  const epubSizeKB = (epubBuffer.length / 1024).toFixed(1);
  logOk(`EPUB собран (${epubSizeKB} КБ)`);

  // Step 5: Save file
  logStep('5/5', 'Сохранение файла...');
  fs.writeFileSync(outputPath, epubBuffer);
  logOk(`Файл сохранён: ${outputPath}`);

  // Summary
  console.log('\n  ┌──────────────────────────────────────────┐');
  console.log(`  │  📖 ${title}`);
  console.log(`  │  ✍️  ${author}`);
  console.log(`  │  🌐 ${opts.lang.toUpperCase()}`);
  console.log(`  │  📦 ${epubSizeKB} КБ`);
  console.log(`  │  📁 ${path.basename(outputPath)}`);
  console.log('  └──────────────────────────────────────────┘');
  console.log('');
  logOk('Конвертация завершена!\n');
}

main().catch((err) => {
  logError(`Критическая ошибка: ${err.message}`);
  if (process.env.DEBUG) console.error(err);
  process.exit(1);
});
