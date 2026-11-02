import JSZip from 'jszip';

export interface ODTContent {
  contentXml: string;
  stylesXml: string;
  metaXml: string;
  images: Map<string, Uint8Array>;
}

export interface StyleInfo {
  name: string;
  family: string;
  bold: boolean;
  italic: boolean;
  underline: boolean;
  strikethrough: boolean;
  fontSize: string;
  color: string;
  backgroundColor: string;
  textAlign: string;
  fontFamily: string;
  parentStyle: string;
  isHeading: boolean;
  headingLevel: number;
}

export async function parseODT(file: File): Promise<ODTContent> {
  const arrayBuffer = await file.arrayBuffer();
  const zip = await JSZip.loadAsync(arrayBuffer);

  const contentXml = await zip.file('content.xml')?.async('string') || '';
  const stylesXml = await zip.file('styles.xml')?.async('string') || '';
  const metaXml = await zip.file('meta.xml')?.async('string') || '';

  const images = new Map<string, Uint8Array>();
  
  // Collect all image paths first
  const imagePaths: string[] = [];
  zip.forEach((relativePath, zipEntry) => {
    if (relativePath.startsWith('Pictures/') && !zipEntry.dir) {
      imagePaths.push(relativePath);
    }
  });

  // Load all images
  for (const path of imagePaths) {
    const entry = zip.file(path);
    if (entry) {
      const data = await entry.async('uint8array');
      images.set(path, data);
    }
  }

  return { contentXml, stylesXml, metaXml, images };
}

function getAttr(el: Element, ...names: string[]): string {
  for (const name of names) {
    const val = el.getAttribute(name);
    if (val) return val;
  }
  // Also try with namespace
  for (const name of names) {
    const parts = name.split(':');
    if (parts.length === 2) {
      const nsMap: Record<string, string> = {
        'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
        'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
        'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
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

function findChild(el: Element, localName: string): Element | null {
  for (let i = 0; i < el.children.length; i++) {
    const child = el.children[i];
    if (child.localName === localName) return child;
  }
  return null;
}

export function parseStyles(stylesXml: string): Map<string, StyleInfo> {
  const stylesMap = new Map<string, StyleInfo>();
  
  if (!stylesXml) return stylesMap;

  const parser = new DOMParser();
  const doc = parser.parseFromString(stylesXml, 'application/xml');
  
  // Find all style elements regardless of namespace
  const allElements = doc.getElementsByTagName('*');
  
  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    if (el.localName !== 'style') continue;
    
    const name = getAttr(el, 'style:name');
    if (!name) continue;
    
    const family = getAttr(el, 'style:family');
    const parentStyle = getAttr(el, 'style:parent-style-name');
    
    const styleInfo: StyleInfo = {
      name,
      family,
      bold: false,
      italic: false,
      underline: false,
      strikethrough: false,
      fontSize: '',
      color: '',
      backgroundColor: '',
      textAlign: '',
      fontFamily: '',
      parentStyle,
      isHeading: false,
      headingLevel: 0,
    };

    // Parse text properties
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

    // Parse paragraph properties
    const paraProps = findChild(el, 'paragraph-properties');
    if (paraProps) {
      styleInfo.textAlign = getAttr(paraProps, 'fo:text-align');
    }

    stylesMap.set(name, styleInfo);
  }

  return stylesMap;
}

export function parseContentStyles(contentXml: string): Map<string, StyleInfo> {
  const stylesMap = new Map<string, StyleInfo>();
  
  if (!contentXml) return stylesMap;

  const parser = new DOMParser();
  const doc = parser.parseFromString(contentXml, 'application/xml');
  
  // Find automatic-styles element
  const allElements = doc.getElementsByTagName('*');
  let inAutoStyles = false;
  
  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    
    if (el.localName === 'automatic-styles') {
      inAutoStyles = true;
      continue;
    }
    
    if (el.localName === 'body') {
      inAutoStyles = false;
      continue;
    }
    
    if (inAutoStyles && el.localName === 'style') {
      const name = getAttr(el, 'style:name');
      if (!name) continue;
      
      const family = getAttr(el, 'style:family');
      const parentStyle = getAttr(el, 'style:parent-style-name');
      
      const styleInfo: StyleInfo = {
        name,
        family,
        bold: false,
        italic: false,
        underline: false,
        strikethrough: false,
        fontSize: '',
        color: '',
        backgroundColor: '',
        textAlign: '',
        fontFamily: '',
        parentStyle,
        isHeading: false,
        headingLevel: 0,
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

export function extractMeta(metaXml: string): { title: string; author: string; date: string } {
  if (!metaXml) return { title: 'Untitled', author: 'Unknown', date: new Date().toISOString() };

  const parser = new DOMParser();
  const doc = parser.parseFromString(metaXml, 'application/xml');
  
  const allElements = doc.getElementsByTagName('*');
  let title = 'Untitled';
  let author = 'Unknown';
  let date = new Date().toISOString();

  for (let i = 0; i < allElements.length; i++) {
    const el = allElements[i];
    if (el.localName === 'title' && el.textContent) {
      title = el.textContent;
    }
    if ((el.localName === 'creator' || el.localName === 'initial-creator') && el.textContent) {
      author = el.textContent;
    }
    if (el.localName === 'date' && el.textContent) {
      date = el.textContent;
    }
  }

  return { title, author, date };
}
