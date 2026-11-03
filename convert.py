#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ODT → EPUB Converter

Конвертация ODT документа в EPUB с сохранением форматирования.
Поддерживает JPEG/PNG обложку.

Использование:
    python convert.py [пути...] [опции]
    
    Если пути не указаны, обрабатывается текущая директория.
    Если путь указывает на файл — конвертируется только этот файл.
    Если путь указывает на директорию — конвертируются все ODT файлы в ней.

Примеры:
    python convert.py
    python convert.py document.odt
    python convert.py /path/to/books
    python convert.py file1.odt file2.odt /path/to/books
    python convert.py -d /path/to/books -e "temp/*" --exclude-file exclude.txt
"""

import argparse
import os
import sys
import zipfile
import uuid
import re
from datetime import datetime
from xml.etree import ElementTree as ET
from pathlib import Path
from fnmatch import fnmatch

# Namespaces
NAMESPACES = {
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'meta': 'urn:oasis:names:tc:opendocument:xmlns:meta:1.0',
    'svg': 'urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0',
}

# Register namespaces for output
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


def log(msg):
    """Вывести сообщение"""
    print(f"  {msg}")


def log_step(step, msg):
    """Вывести шаг"""
    print(f"\n  [{step}] {msg}")


def log_ok(msg):
    """Вывести успех"""
    print(f"  ✅ {msg}")


def log_error(msg):
    """Вывести ошибку"""
    print(f"  ❌ {msg}", file=sys.stderr)


def escape_html(text):
    """Экранировать HTML"""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#39;'))


def escape_xml(text):
    """Экранировать XML"""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))


def get_attr(elem, *names):
    """Получить атрибут элемента с учётом namespace"""
    for name in names:
        # Прямой атрибут
        val = elem.get(name)
        if val:
            return val
        
        # С namespace
        if ':' in name:
            prefix, local = name.split(':', 1)
            ns = NAMESPACES.get(prefix)
            if ns:
                val = elem.get(f'{{{ns}}}{local}')
                if val:
                    return val
    return ''


def find_child(elem, local_name):
    """Найти дочерний элемент по локальному имени"""
    for child in elem:
        if child.tag.endswith(f'}}{local_name}') or child.tag == local_name:
            return child
    return None


# ============================================================
# ODT Parser
# ============================================================

class ODTContent:
    def __init__(self):
        self.content_xml = ''
        self.styles_xml = ''
        self.meta_xml = ''
        self.images = {}  # path -> bytes


def parse_odt(file_path):
    """Разобрать ODT файл"""
    content = ODTContent()
    
    # Открываем ZIP с явным указанием кодировки для поддержки кириллицы
    with zipfile.ZipFile(file_path, 'r') as zf:
        # Читаем XML файлы с явной декодировкой UTF-8
        if 'content.xml' in zf.namelist():
            content.content_xml = zf.read('content.xml').decode('utf-8')
        
        if 'styles.xml' in zf.namelist():
            content.styles_xml = zf.read('styles.xml').decode('utf-8')
        
        if 'meta.xml' in zf.namelist():
            content.meta_xml = zf.read('meta.xml').decode('utf-8')
        
        # Читаем изображения (могут иметь кириллические имена)
        for name in zf.namelist():
            if name.startswith('Pictures/') and not name.endswith('/'):
                content.images[name] = zf.read(name)
    
    return content


# ============================================================
# Style Parser
# ============================================================

class StyleInfo:
    def __init__(self):
        self.name = ''
        self.family = ''
        self.parent_style = ''
        self.bold = False
        self.italic = False
        self.underline = False
        self.strikethrough = False
        self.font_size = ''
        self.color = ''
        self.background_color = ''
        self.text_align = ''
        self.font_family = ''


def parse_styles(styles_xml):
    """Разобрать стили из styles.xml"""
    styles_map = {}
    
    if not styles_xml:
        return styles_map
    
    try:
        root = ET.fromstring(styles_xml)
    except ET.ParseError:
        return styles_map
    
    # Ищем все элементы style
    for elem in root.iter():
        if not elem.tag.endswith('}style') and elem.tag != 'style':
            continue
        
        name = get_attr(elem, 'style:name')
        if not name:
            continue
        
        style = StyleInfo()
        style.name = name
        style.family = get_attr(elem, 'style:family')
        style.parent_style = get_attr(elem, 'style:parent-style-name')
        
        # Text properties
        text_props = find_child(elem, 'text-properties')
        if text_props is not None:
            font_weight = get_attr(text_props, 'fo:font-weight')
            font_style = get_attr(text_props, 'fo:font-style')
            underline_style = get_attr(text_props, 'style:text-underline-style')
            line_through = get_attr(text_props, 'style:text-line-through-style')
            text_decoration = get_attr(text_props, 'fo:text-decoration')
            
            style.bold = font_weight in ('bold', '700', '900')
            style.italic = font_style == 'italic'
            style.underline = underline_style == 'solid' or 'underline' in text_decoration
            style.strikethrough = line_through == 'solid' or 'line-through' in text_decoration
            style.font_size = get_attr(text_props, 'fo:font-size')
            style.color = get_attr(text_props, 'fo:color')
            style.background_color = get_attr(text_props, 'fo:background-color')
            style.font_family = get_attr(text_props, 'style:font-name')
        
        # Paragraph properties
        para_props = find_child(elem, 'paragraph-properties')
        if para_props is not None:
            style.text_align = get_attr(para_props, 'fo:text-align')
        
        styles_map[name] = style
    
    return styles_map


def parse_content_styles(content_xml):
    """Разобрать автоматические стили из content.xml"""
    styles_map = {}
    
    if not content_xml:
        return styles_map
    
    try:
        root = ET.fromstring(content_xml)
    except ET.ParseError:
        return styles_map
    
    # Ищем automatic-styles
    in_auto_styles = False
    for elem in root.iter():
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        
        if tag == 'automatic-styles':
            in_auto_styles = True
            continue
        elif tag == 'body':
            in_auto_styles = False
            continue
        
        if in_auto_styles and (tag == 'style'):
            name = get_attr(elem, 'style:name')
            if not name:
                continue
            
            style = StyleInfo()
            style.name = name
            style.family = get_attr(elem, 'style:family')
            style.parent_style = get_attr(elem, 'style:parent-style-name')
            
            text_props = find_child(elem, 'text-properties')
            if text_props is not None:
                font_weight = get_attr(text_props, 'fo:font-weight')
                font_style = get_attr(text_props, 'fo:font-style')
                underline_style = get_attr(text_props, 'style:text-underline-style')
                line_through = get_attr(text_props, 'style:text-line-through-style')
                
                style.bold = font_weight in ('bold', '700', '900')
                style.italic = font_style == 'italic'
                style.underline = underline_style == 'solid'
                style.strikethrough = line_through == 'solid'
                style.font_size = get_attr(text_props, 'fo:font-size')
                style.color = get_attr(text_props, 'fo:color')
                style.background_color = get_attr(text_props, 'fo:background-color')
                style.font_family = get_attr(text_props, 'style:font-name')
            
            para_props = find_child(elem, 'paragraph-properties')
            if para_props is not None:
                style.text_align = get_attr(para_props, 'fo:text-align')
            
            styles_map[name] = style
    
    return styles_map


def extract_meta(meta_xml):
    """Извлечь метаданные"""
    meta = {
        'title': 'Untitled',
        'author': 'Unknown',
    }
    
    if not meta_xml:
        return meta
    
    try:
        root = ET.fromstring(meta_xml)
    except ET.ParseError:
        return meta
    
    for elem in root.iter():
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        
        if tag == 'title' and elem.text:
            meta['title'] = elem.text
        elif tag in ('creator', 'initial-creator') and elem.text:
            meta['author'] = elem.text
    
    return meta


# ============================================================
# ODT → XHTML Converter
# ============================================================

class XHTMLConverter:
    def __init__(self, styles_map, auto_styles_map, images):
        self.styles_map = styles_map
        self.auto_styles_map = auto_styles_map
        self.images = images
        self.image_files = {}  # new_path -> bytes
        self.image_counter = 0
        self.css_rules = []
    
    def convert(self, content_xml):
        """Конвертировать content.xml в XHTML"""
        if not content_xml:
            return '<p>Empty document</p>', '', {}
        
        try:
            root = ET.fromstring(content_xml)
        except ET.ParseError:
            return '<p>Parse error</p>', '', {}
        
        # Ищем body
        body = None
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if tag == 'body':
                body = elem
                break
        
        if body is None:
            return '<p>Empty document</p>', '', {}
        
        # Генерируем CSS из стилей
        self._generate_css()
        
        # Конвертируем содержимое
        xhtml = self._process_children(body)
        
        # Формируем итоговый CSS
        css = self._build_css()
        
        return xhtml, css, self.image_files
    
    def _generate_css(self):
        """Генерировать CSS правила из стилей"""
        for name, style in self.styles_map.items():
            safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
            rules = []
            
            if style.bold:
                rules.append('font-weight: bold')
            if style.italic:
                rules.append('font-style: italic')
            if style.underline:
                rules.append('text-decoration: underline')
            if style.strikethrough:
                rules.append('text-decoration: line-through')
            if style.font_size:
                rules.append(f'font-size: {style.font_size}')
            if style.color:
                rules.append(f'color: {style.color}')
            if style.background_color:
                rules.append(f'background-color: {style.background_color}')
            if style.text_align:
                rules.append(f'text-align: {style.text_align}')
            if style.font_family:
                rules.append(f"font-family: '{style.font_family}'")
            
            if rules:
                self.css_rules.append(f".{safe_name} {{ {'; '.join(rules)}; }}")
    
    def _build_css(self):
        """Собрать итоговый CSS"""
        base_css = """body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.6; margin: 1em; color: #333; }
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
a { color: #0066cc; text-decoration: underline; }"""
        
        custom_css = '\n'.join(self.css_rules)
        return f"{base_css}\n{custom_css}" if custom_css else base_css
    
    def _process_node(self, elem):
        """Обработать узел"""
        # Текстовый узел
        if elem.text:
            result = escape_html(elem.text)
        else:
            result = ''
        
        # Обработать дочерние элементы
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        
        if tag == 'h':
            level = int(get_attr(elem, 'text:outline-level') or '1')
            level = max(1, min(6, level))
            content = self._process_children(elem)
            result += f'<h{level}>{content}</h{level}>'
        
        elif tag == 'p':
            style_name = get_attr(elem, 'text:style-name')
            safe_style = re.sub(r'[^a-zA-Z0-9_-]', '_', style_name)
            content = self._process_children(elem)
            if safe_style:
                result += f'<p class="{safe_style}">{content}</p>'
            else:
                result += f'<p>{content}</p>'
        
        elif tag == 'span':
            style_name = get_attr(elem, 'text:style-name')
            safe_style = re.sub(r'[^a-zA-Z0-9_-]', '_', style_name)
            content = self._process_children(elem)
            
            style_info = self.auto_styles_map.get(style_name) or self.styles_map.get(style_name)
            if style_info:
                inner = content
                if style_info.bold:
                    inner = f'<strong>{inner}</strong>'
                if style_info.italic:
                    inner = f'<em>{inner}</em>'
                if style_info.underline:
                    inner = f'<span style="text-decoration:underline">{inner}</span>'
                if style_info.strikethrough:
                    inner = f'<span style="text-decoration:line-through">{inner}</span>'
                if style_info.color:
                    inner = f'<span style="color:{style_info.color}">{inner}</span>'
                
                if safe_style:
                    result += f'<span class="{safe_style}">{inner}</span>'
                else:
                    result += inner
            else:
                if safe_style:
                    result += f'<span class="{safe_style}">{content}</span>'
                else:
                    result += content
        
        elif tag == 'a':
            href = get_attr(elem, 'xlink:href') or '#'
            content = self._process_children(elem)
            result += f'<a href="{escape_html(href)}">{content}</a>'
        
        elif tag == 'list':
            result += f'<ul>{self._process_children(elem)}</ul>'
        
        elif tag == 'list-item':
            result += f'<li>{self._process_children(elem)}</li>'
        
        elif tag == 'ordered-list':
            result += f'<ol>{self._process_children(elem)}</ol>'
        
        elif tag == 'line-break':
            result += '<br/>'
        
        elif tag == 'tab':
            result += '<span style="white-space:pre">\t</span>'
        
        elif tag == 's':
            count = int(get_attr(elem, 'text:c') or '1')
            result += '\u00A0' * count
        
        elif tag == 'frame':
            # Ищем изображение
            image_href = ''
            for child in elem.iter():
                child_tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if child_tag == 'image':
                    image_href = get_attr(child, 'xlink:href')
                    break
            
            if image_href:
                ext = Path(image_href).suffix or '.jpg'
                new_name = f'img_{self.image_counter}{ext}'
                self.image_counter += 1
                
                # Ищем изображение в данных
                image_data = self.images.get(image_href)
                if not image_data:
                    image_data = self.images.get('./' + image_href)
                if not image_data:
                    image_data = self.images.get(image_href.lstrip('./'))
                
                if image_data:
                    self.image_files[f'OEBPS/images/{new_name}'] = image_data
                
                width = get_attr(elem, 'svg:width') or '100%'
                css_width = width if '%' in width else f'max-width:{width}'
                result += f'<div class="image-container"><img src="images/{new_name}" alt="image" style="{css_width};width:100%"/></div>'
            else:
                result += self._process_children(elem)
        
        elif tag == 'image':
            href = get_attr(elem, 'xlink:href')
            if href:
                ext = Path(href).suffix or '.jpg'
                new_name = f'img_{self.image_counter}{ext}'
                self.image_counter += 1
                
                image_data = self.images.get(href)
                if not image_data:
                    image_data = self.images.get('./' + href)
                if not image_data:
                    image_data = self.images.get(href.lstrip('./'))
                
                if image_data:
                    self.image_files[f'OEBPS/images/{new_name}'] = image_data
                
                result += f'<div class="image-container"><img src="images/{new_name}" alt="image" style="width:100%"/></div>'
        
        elif tag == 'table':
            result += f'<table class="doc-table">{self._process_children(elem)}</table>'
        
        elif tag == 'table-row':
            result += f'<tr>{self._process_children(elem)}</tr>'
        
        elif tag == 'table-cell':
            col_span = get_attr(elem, 'table:number-columns-spanned') or '1'
            content = self._process_children(elem)
            if int(col_span) > 1:
                result += f'<td colspan="{col_span}">{content}</td>'
            else:
                result += f'<td>{content}</td>'
        
        elif tag == 'table-header-rows':
            result += f'<thead>{self._process_children(elem)}</thead>'
        
        elif tag == 'section':
            result += self._process_children(elem)
        
        else:
            result += self._process_children(elem)
        
        # Tail text
        if elem.tail:
            result += escape_html(elem.tail)
        
        return result
    
    def _process_children(self, elem):
        """Обработать дочерние элементы"""
        result = ''
        for child in elem:
            result += self._process_node(child)
        return result


# ============================================================
# EPUB Generator
# ============================================================

def generate_epub(xhtml, css, image_files, options):
    """Сгенерировать EPUB файл"""
    book_uuid = str(uuid.uuid4())
    
    # Создаём ZIP
    epub_buffer = bytearray()
    
    with zipfile.ZipFile(epub_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        # mimetype (должен быть первым, без сжатия)
        zf.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        
        # META-INF/container.xml
        container_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>'''
        # Явно кодируем в UTF-8 для поддержки кириллицы
        zf.writestr('META-INF/container.xml', container_xml.encode('utf-8'))
        
        manifest_items = []
        spine_items = []
        
        # CSS (явно кодируем в UTF-8)
        zf.writestr('OEBPS/style.css', css.encode('utf-8'))
        manifest_items.append('    <item id="style" href="style.css" media-type="text/css"/>')
        
        # Изображения из ODT (могут иметь кириллические имена)
        image_id = 0
        for path, data in image_files.items():
            file_name = path.replace('OEBPS/', '')
            ext = Path(file_name).suffix.lower().lstrip('.')
            media_type = 'image/png' if ext == 'png' else 'image/jpeg'
            item_id = f'img_{image_id}'
            image_id += 1
            
            # Путь может содержать кириллицу - zipfile автоматически использует UTF-8
            zf.writestr(path, data)
            manifest_items.append(f'    <item id="{item_id}" href="{file_name}" media-type="{media_type}"/>')
        
        # Обложка
        has_cover = False
        if options.get('cover_image'):
            cover_ext = 'png' if options.get('cover_mime_type', '').endswith('png') else 'jpg'
            cover_media_type = 'image/png' if cover_ext == 'png' else 'image/jpeg'
            cover_file_name = f'cover.{cover_ext}'
            
            zf.writestr(f'OEBPS/images/{cover_file_name}', options['cover_image'])
            manifest_items.append(f'    <item id="cover-image" href="images/{cover_file_name}" media-type="{cover_media_type}" properties="cover-image"/>')
            has_cover = True
        
        # Страница обложки (явно кодируем в UTF-8 для поддержки кириллицы)
        if has_cover:
            cover_page = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{options['language']}">
<head>
  <meta charset="UTF-8"/>
  <title>Cover</title>
  <style>body{{margin:0;padding:0;text-align:center}}img{{max-width:100%;max-height:100vh}}</style>
</head>
<body>
  <div id="cover"><img src="images/cover.{cover_ext}" alt="Cover"/></div>
</body>
</html>'''
            zf.writestr('OEBPS/cover.xhtml', cover_page.encode('utf-8'))
            manifest_items.append('    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
            spine_items.append('    <itemref idref="cover"/>')
        
        # Основной контент (явно кодируем в UTF-8)
        content_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{options['language']}">
<head>
  <meta charset="UTF-8"/>
  <title>{escape_xml(options['title'])}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div id="content">
    {xhtml}
  </div>
</body>
</html>'''
        
        zf.writestr('OEBPS/chapter1.xhtml', content_xhtml.encode('utf-8'))
        manifest_items.append('    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>')
        spine_items.append('    <itemref idref="chapter1"/>')
        
        # NCX (таблица содержимого, явно кодируем в UTF-8)
        ncx = f'''<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:{book_uuid}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{escape_xml(options['title'])}</text></docTitle>
  <navMap>
    <navPoint id="navpoint1" playOrder="1">
      <navLabel><text>{escape_xml(options['title'])}</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>'''
        
        zf.writestr('OEBPS/toc.ncx', ncx.encode('utf-8'))
        manifest_items.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
        
        # content.opf (манифест, явно кодируем в UTF-8)
        content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">urn:uuid:{book_uuid}</dc:identifier>
    <dc:title>{escape_xml(options['title'])}</dc:title>
    <dc:creator>{escape_xml(options['author'])}</dc:creator>
    <dc:language>{options['language']}</dc:language>
    <dc:date>{options['date']}</dc:date>
    <meta property="dcterms:modified">{datetime.now().isoformat(timespec='seconds').replace('+00:00', 'Z')}</meta>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine toc="ncx">
{chr(10).join(spine_items)}
  </spine>
</package>'''
        
        zf.writestr('OEBPS/content.opf', content_opf.encode('utf-8'))
    
    return bytes(epub_buffer)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Конвертация ODT документа в EPUB с сохранением форматирования',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Примеры:
  python convert.py
  python convert.py document.odt
  python convert.py file1.odt file2.odt
  python convert.py /path/to/books
  python convert.py file.odt /path/to/books
  python convert.py -e "temp/*" -e "*.draft.odt"
  python convert.py --exclude-file exclude.txt
        '''
    )
    
    parser.add_argument('paths', nargs='*', help='Пути к файлам или директориям (если не указаны, обрабатывается текущая директория)')
    parser.add_argument('-c', '--cover', help='Путь к обложке (JPEG/PNG). Если не указан, ищется файл с тем же именем, что и ODT, с расширением .jpg или .png')
    parser.add_argument('-o', '--output', help='Путь для выходного EPUB файла (только для одного файла, по умолчанию: имя ODT + суффикс _(hh:mm_dd.MM.YYYY) + .epub)')
    parser.add_argument('-t', '--title', help='Название книги (по умолчанию: имя файла ODT без расширения)')
    parser.add_argument('-a', '--author', help='Автор книги (по умолчанию из метаданных ODT)')
    parser.add_argument('-l', '--lang', default='', help='Язык книги (ISO 639-1, по умолчанию: пусто)')
    parser.add_argument('-e', '--exclude', action='append', help='Исключить файл или папку (можно указывать несколько раз, поддерживает glob-паттерны)')
    parser.add_argument('--exclude-file', help='Файл со списком исключений (по одному на строку, # для комментариев)')
    parser.add_argument('--no-recursive', action='store_true', help='Отключить рекурсивный поиск в поддиректориях (по умолчанию: включен)')
    
    args = parser.parse_args()
    
    print('\n  ╔══════════════════════════════════════════╗')
    print('  ║       ODT → EPUB Converter v1.0.0       ║')
    print('  ╚══════════════════════════════════════════╝\n')
    
    # Загрузка списка исключений
    exclude_patterns = []
    if args.exclude:
        exclude_patterns.extend(args.exclude)
    
    if args.exclude_file:
        if not os.path.exists(args.exclude_file):
            log_error(f'Файл исключений не найден: {args.exclude_file}')
            sys.exit(1)
        with open(args.exclude_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    exclude_patterns.append(line)
    
    # Функция проверки исключений
    def should_exclude(file_path):
        """Проверяет, должен ли файл быть исключён"""
        file_path_str = str(file_path)
        file_name = file_path.name
        
        for pattern in exclude_patterns:
            # Проверяем точное совпадение
            if file_path_str == pattern or file_name == pattern:
                return True
            # Проверяем glob-паттерны
            if fnmatch(file_name, pattern) or fnmatch(file_path_str, pattern):
                return True
            # Проверяем, находится ли файл в исключённой папке
            if file_path_str.startswith(pattern + os.sep) or pattern + os.sep in file_path_str:
                return True
        
        return False
    
    # Обработка одного файла
    def process_single_file(input_path, explicit_cover=None, explicit_output=None, explicit_title=None, explicit_author=None, explicit_lang=''):
        """Обрабатывает один ODT файл. Возвращает True при успехе."""
        input_path = os.path.abspath(input_path)
        
        if not os.path.exists(input_path):
            log_error(f'Файл не найден: {input_path}')
            return False
        
        if not input_path.lower().endswith('.odt'):
            log_error(f'Файл должен иметь расширение .odt: {input_path}')
            return False
        
        # Проверяем обложку
        cover_buffer = None
        cover_mime_type = None
        
        if explicit_cover:
            cover_path = os.path.abspath(explicit_cover)
            if not os.path.exists(cover_path):
                log_error(f'Файл обложки не найден: {cover_path}')
                return False
            
            try:
                with open(cover_path, 'rb') as f:
                    cover_buffer = f.read()
            except Exception as e:
                log_error(f'Ошибка чтения файла обложки: {e}')
                return False
            
            ext = Path(cover_path).suffix.lower()
            cover_mime_type = 'image/png' if ext == '.png' else 'image/jpeg'
            log_ok(f'Обложка: {os.path.basename(cover_path)} ({cover_mime_type})')
        else:
            # Автопоиск обложки
            odt_stem = Path(input_path).stem
            odt_dir = Path(input_path).parent
            
            for ext in ('.jpg', '.jpeg', '.png'):
                candidate = odt_dir / f'{odt_stem}{ext}'
                if candidate.exists():
                    try:
                        with open(str(candidate), 'rb') as f:
                            cover_buffer = f.read()
                        cover_mime_type = 'image/png' if ext == '.png' else 'image/jpeg'
                        log_ok(f'Обложка найдена автоматически: {candidate.name} ({cover_mime_type})')
                        break
                    except Exception as e:
                        log_error(f'Ошибка чтения найденной обложки {candidate.name}: {e}')
                        cover_buffer = None
            else:
                log('ℹ️  Обложка не указана и не найдена рядом с ODT файлом')
        
        # Определяем выходной путь
        if explicit_output:
            output_path = os.path.abspath(explicit_output)
        else:
            output_path = os.path.abspath(input_path.replace('.odt', '.epub').replace('.ODT', '.epub'))
        
        # Добавляем суффикс с датой и временем создания перед расширением
        timestamp = datetime.now().strftime('%H:%M_%d.%m.%Y')
        output_stem = Path(output_path).stem
        output_ext = Path(output_path).suffix
        output_dir = Path(output_path).parent
        output_path = str(output_dir / f'{output_stem}_({timestamp}){output_ext}')
        
        # Шаг 1: Читаем ODT
        log_step('1/5', 'Чтение ODT файла...')
        try:
            odt_content = parse_odt(input_path)
            image_count = len(odt_content.images)
            log_ok(f'ODT прочитан (изображений в документе: {image_count})')
        except Exception as e:
            log_error(f'Ошибка чтения ODT файла: {e}')
            return False
        
        # Шаг 2: Анализируем стили
        log_step('2/5', 'Анализ стилей...')
        styles_map = parse_styles(odt_content.styles_xml)
        auto_styles_map = parse_content_styles(odt_content.content_xml)
        log_ok(f'Стили: {len(styles_map)} из styles.xml, {len(auto_styles_map)} из content.xml')
        
        # Извлекаем метаданные
        meta = extract_meta(odt_content.meta_xml)
        title = explicit_title or Path(input_path).stem
        author = explicit_author or meta['author'] or 'Unknown'
        log_ok(f'Метаданные: "{title}" — {author}')
        
        # Шаг 3: Конвертируем содержимое
        log_step('3/5', 'Конвертация содержимого в XHTML...')
        converter = XHTMLConverter(styles_map, auto_styles_map, odt_content.images)
        xhtml, css, image_files = converter.convert(odt_content.content_xml)
        log_ok(f'XHTML сгенерирован ({len(xhtml)} символов, {len(image_files)} изображений)')
        
        # Шаг 4: Генерируем EPUB
        log_step('4/5', 'Сборка EPUB...')
        # Используем дату генерации
        generation_date = datetime.now().isoformat(timespec='seconds')
        epub_buffer = generate_epub(xhtml, css, image_files, {
            'title': title,
            'author': author,
            'date': generation_date,
            'language': explicit_lang or 'und',
            'cover_image': cover_buffer,
            'cover_mime_type': cover_mime_type,
        })
        epub_size_kb = len(epub_buffer) / 1024
        log_ok(f'EPUB собран ({epub_size_kb:.1f} КБ)')
        
        # Шаг 5: Сохраняем файл
        log_step('5/5', 'Сохранение файла...')
        try:
            with open(output_path, 'wb') as f:
                f.write(epub_buffer)
            log_ok(f'Файл сохранён: {output_path}')
        except Exception as e:
            log_error(f'Ошибка записи файла: {e}')
            return False
        
        # Итог
        print('\n  ┌──────────────────────────────────────────┐')
        print(f'  │  📖 {title}')
        print(f'  │  ✍️  {author}')
        if explicit_lang:
            print(f'  │  🌐 {explicit_lang.upper()}')
        print(f'  │  📦 {epub_size_kb:.1f} КБ')
        print(f'  │  📁 {os.path.basename(output_path)}')
        print('  └──────────────────────────────────────────┘')
        print('')
        log_ok('Конвертация завершена!\n')
        return True
    
    # Обработка директории
    def process_directory(directory_path):
        """Обрабатывает все ODT файлы в директории"""
        directory_path = os.path.abspath(directory_path)
        
        if not os.path.exists(directory_path):
            log_error(f'Директория не найдена: {directory_path}')
            return []
        
        if not os.path.isdir(directory_path):
            log_error(f'Указанный путь не является директорией: {directory_path}')
            return []
        
        # Собираем все ODT файлы
        all_odt_files = []
        
        if args.no_recursive:
            # Нерекурсивный режим: только файлы в указанной директории
            for file_name in os.listdir(directory_path):
                file_path = Path(directory_path) / file_name
                if file_path.is_file() and file_name.lower().endswith('.odt'):
                    if not should_exclude(file_path):
                        all_odt_files.append(file_path)
        else:
            # Рекурсивный режим: обход всех поддиректорий
            for root, dirs, files in os.walk(directory_path):
                # Фильтруем директории по исключениям
                dirs[:] = [d for d in dirs if not should_exclude(Path(root) / d)]
                
                for file_name in files:
                    if file_name.lower().endswith('.odt'):
                        file_path = Path(root) / file_name
                        if not should_exclude(file_path):
                            all_odt_files.append(file_path)
        
        return all_odt_files
    
    # Определяем пути для обработки
    paths_to_process = args.paths if args.paths else ['.']
    
    # Собираем все файлы для обработки
    all_files = []
    
    for path in paths_to_process:
        path = os.path.abspath(path)
        
        if not os.path.exists(path):
            log_error(f'Путь не найден: {path}')
            continue
        
        if os.path.isfile(path):
            # Это файл
            if path.lower().endswith('.odt'):
                if not should_exclude(Path(path)):
                    all_files.append(path)
            else:
                log_error(f'Файл не является ODT: {path}')
        elif os.path.isdir(path):
            # Это директория
            files = process_directory(path)
            all_files.extend(files)
    
    if not all_files:
        log('ℹ️  ODT файлы не найдены')
        sys.exit(0)
    
    # Показываем список исключений
    if exclude_patterns:
        log(f'🚫 Исключения ({len(exclude_patterns)}):')
        for pattern in exclude_patterns:
            log(f'   • {pattern}')
        print()
    
    log(f'📂 Найдено ODT файлов: {len(all_files)}')
    
    # Если только один файл и указан -o, используем его
    if len(all_files) == 1 and args.output:
        process_single_file(
            all_files[0],
            explicit_cover=args.cover,
            explicit_output=args.output,
            explicit_title=args.title,
            explicit_author=args.author,
            explicit_lang=args.lang
        )
    else:
        # Обрабатываем каждый файл
        success_count = 0
        fail_count = 0
        
        for i, file_path in enumerate(all_files, 1):
            print(f'\n  {"─" * 50}')
            log(f'📄 [{i}/{len(all_files)}] {Path(file_path).name}')
            print(f'  {"─" * 50}')
            
            try:
                if process_single_file(
                    file_path,
                    explicit_cover=args.cover,
                    explicit_output=None,  # В пакетном режиме всегда автоимя
                    explicit_title=args.title,
                    explicit_author=args.author,
                    explicit_lang=args.lang
                ):
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                log_error(f'Ошибка обработки: {e}')
                fail_count += 1
        
        # Итоговая статистика
        print(f'\n  {"═" * 50}')
        print(f'  📊 Итого: {len(all_files)} файлов')
        print(f'  ✅ Успешно: {success_count}')
        if fail_count > 0:
            print(f'  ❌ Ошибок: {fail_count}')
        print(f'  {"═" * 50}\n')


if __name__ == '__main__':
    main()
