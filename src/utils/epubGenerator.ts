import JSZip from 'jszip';
import { saveAs } from 'file-saver';
import { ConvertResult } from './odtToXhtml';

export interface EpubOptions {
  title: string;
  author: string;
  date: string;
  language: string;
  coverImage?: Uint8Array;
  coverMimeType?: string;
}

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

function escapeXml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

export async function generateEpub(
  convertResult: ConvertResult,
  options: EpubOptions
): Promise<Blob> {
  const zip = new JSZip();
  const uuid = generateUUID();
  const { xhtml, css, imageFiles } = convertResult;

  // 1. mimetype (must be first, uncompressed)
  zip.file('mimetype', 'application/epub+zip', { compression: 'STORE' });

  // 2. META-INF/container.xml
  zip.file('META-INF/container.xml', `<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>`);

  // 3. Build content items
  const manifestItems: string[] = [];
  const spineItems: string[] = [];

  // Add CSS
  zip.file('OEBPS/style.css', css);
  manifestItems.push(`    <item id="style" href="style.css" media-type="text/css"/>`);

  // Add images
  let imageIdCounter = 0;
  imageFiles.forEach((data, path) => {
    const fileName = path.replace('OEBPS/', '');
    const ext = fileName.split('.').pop()?.toLowerCase() || 'jpg';
    const mediaType = ext === 'png' ? 'image/png' : 'image/jpeg';
    const id = `img_${imageIdCounter++}`;
    zip.file(path, data);
    manifestItems.push(`    <item id="${id}" href="${fileName}" media-type="${mediaType}"/>`);
  });

  // Add cover image if provided
  let hasCover = false;
  if (options.coverImage) {
    const coverExt = options.coverMimeType?.includes('png') ? 'png' : 'jpg';
    const coverMediaType = coverExt === 'png' ? 'image/png' : 'image/jpeg';
    const coverFileName = `cover.${coverExt}`;
    zip.file(`OEBPS/images/${coverFileName}`, options.coverImage);
    manifestItems.push(`    <item id="cover-image" href="images/${coverFileName}" media-type="${coverMediaType}" properties="cover-image"/>`);
    hasCover = true;
  }

  // Create cover page if cover image exists
  if (hasCover) {
    const coverExt = options.coverMimeType?.includes('png') ? 'png' : 'jpg';
    const coverPage = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="${options.language}">
<head>
  <meta charset="UTF-8"/>
  <title>Cover</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
  <style>
    body { margin: 0; padding: 0; text-align: center; }
    img { max-width: 100%; max-height: 100vh; }
  </style>
</head>
<body>
  <div id="cover">
    <img src="images/cover.${coverExt}" alt="Cover"/>
  </div>
</body>
</html>`;
    zip.file('OEBPS/cover.xhtml', coverPage);
    manifestItems.push(`    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>`);
    spineItems.push(`    <itemref idref="cover"/>`);
  }

  // Add main content
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

  // Add NCX (table of contents)
  const ncx = `<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:${uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle>
    <text>${escapeXml(options.title)}</text>
  </docTitle>
  <navMap>
    <navPoint id="navpoint1" playOrder="1">
      <navLabel>
        <text>${escapeXml(options.title)}</text>
      </navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>`;

  zip.file('OEBPS/toc.ncx', ncx);
  manifestItems.push(`    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>`);

  // Create content.opf (package document)
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

  // Generate the EPUB (as blob)
  const blob = await zip.generateAsync({ 
    type: 'blob',
    mimeType: 'application/epub+zip',
    compression: 'DEFLATE',
    compressionOptions: { level: 9 }
  });

  return blob;
}

export function downloadEpub(blob: Blob, filename: string): void {
  saveAs(blob, filename);
}
