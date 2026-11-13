#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ODT/Markdown Converter

Конвертация ODT, Markdown, TXT и PDF документов в EPUB, Markdown, PDF или TXT.
Поддерживает JPEG/PNG обложку.

Использование:
    python convert.py [пути...] [опции]

    Если пути не указаны, обрабатывается текущая директория.
    Если путь указывает на файл — конвертируется только этот файл.
    Если путь указывает на директорию — конвертируются все поддерживаемые файлы в ней.

Примеры:
    python convert.py
    python convert.py document.odt
    python convert.py /path/to/books
    python convert.py file1.odt file2.odt /path/to/books
    python convert.py -d /path/to/books -e "temp/*" --exclude-file exclude.txt
    python convert.py books/ --mode summary
    python convert.py books/ --mode quiet
    python convert.py document.odt --format pdf
    python convert.py document.odt --format txt
"""

import argparse
import os
import sys
import zipfile
import uuid
import re
import io
import subprocess
import tempfile
import shutil
import posixpath
from datetime import datetime
from xml.etree import ElementTree as ET
from pathlib import Path, PurePosixPath
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

OUTPUT_MODE = 'verbose'
LOG_FILE_PATH = None
PDF_ENGINE = 'xelatex'
DEFAULT_AUTHOR = 'Шмель'
DEFAULT_PUBLISHER = 'Самиздат'
DEFAULT_RIGHTS = '© Шмель, 2026'

# Register namespaces for output
for prefix, uri in NAMESPACES.items():
    ET.register_namespace(prefix, uri)


def write_file_log(*args, **kwargs):
    """Записать строку журнала, не меняя вывод в консоль."""
    global LOG_FILE_PATH

    if not LOG_FILE_PATH:
        return

    log_kwargs = dict(kwargs)
    log_kwargs.pop('file', None)
    try:
        with open(LOG_FILE_PATH, 'a', encoding='utf-8') as log_file:
            print(*args, file=log_file, **log_kwargs)
    except OSError as e:
        failed_path = LOG_FILE_PATH
        LOG_FILE_PATH = None
        print(f'Не удалось записать журнал {failed_path}: {e}; дальнейшее логирование отключено.', file=sys.stderr)


def verbose_print(*args, **kwargs):
    """Вывести сообщение только в подробном режиме"""
    if OUTPUT_MODE == 'verbose':
        print(*args, **kwargs)
    write_file_log(*args, **kwargs)


def log(msg):
    """Вывести сообщение"""
    verbose_print(f"  {msg}")


def log_step(step, msg):
    """Вывести шаг"""
    verbose_print(f"\n  [{step}] {msg}")


def log_ok(msg):
    """Вывести успех"""
    verbose_print(f"  ✅ {msg}")


def log_error(msg, critical=True):
    """Вывести ошибку"""
    error_message = f"  ❌ {msg}"
    if OUTPUT_MODE != 'quiet' or critical:
        print(error_message, file=sys.stderr)
    write_file_log(error_message)


def log_warning(msg):
    """Вывести предупреждение, кроме тихого режима."""
    warning_message = f"  ⚠️ Предупреждение: {msg}"
    if OUTPUT_MODE != 'quiet':
        print(warning_message, file=sys.stderr)
    write_file_log(warning_message)


def warn_missing_external_utilities():
    """Предупредить об отсутствующих внешних утилитах конвертации."""
    utilities = {
        'pandoc': 'часть маршрутов конвертации требует Pandoc',
        PDF_ENGINE: 'создание PDF через Pandoc требует PDF-движок XeLaTeX',
        'pdftotext': 'извлечение текста из PDF требует Poppler',
        'pdftoppm': 'обработка сканированных PDF требует Poppler',
    }
    for utility, purpose in utilities.items():
        if shutil.which(utility) is None:
            log_warning(f'{utility} не найден в PATH; {purpose}.')


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


def get_image_mime_type(ext):
    """Определить MIME-тип изображения по расширению"""
    ext = ext.lower().lstrip('.')
    mime_types = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'svg': 'image/svg+xml',
        'webp': 'image/webp',
        'bmp': 'image/bmp',
        'tiff': 'image/tiff',
        'tif': 'image/tiff',
        'ico': 'image/x-icon',
    }
    return mime_types.get(ext, 'image/jpeg')


# Поддерживаемые расширения изображений для обложек
SUPPORTED_IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp', '.bmp', '.tiff', '.tif')

MARKDOWN_HORIZONTAL_RULE_PATTERN = re.compile(rb'^ {0,3}---[ \t]*(\r?\n)?$')
MARKDOWN_FENCE_PATTERN = re.compile(rb'^ {0,3}(`{3,}|~{3,})')
MARKDOWN_FENCE_CLOSE_PATTERN = re.compile(rb'^ {0,3}(`+|~+)[ \t]*(?:\r?\n)?$')


def _prepare_markdown_pdf_source(markdown_bytes):
    lines = markdown_bytes.splitlines(keepends=True)
    front_matter_end = 0
    if lines:
        first_line = lines[0][3:] if lines[0].startswith(b'\xef\xbb\xbf') else lines[0]
        if re.fullmatch(rb'---[ \t]*(?:\r?\n)?', first_line):
            for index, line in enumerate(lines[1:], start=1):
                if re.fullmatch(rb'(?:---|\.\.\.)[ \t]*(?:\r?\n)?', line):
                    front_matter_end = index + 1
                    break

    replace_indexes = []
    fence_marker = None
    for index in range(front_matter_end, len(lines)):
        line = lines[index]
        if fence_marker is not None:
            closing_fence = MARKDOWN_FENCE_CLOSE_PATTERN.match(line)
            if (
                closing_fence
                and closing_fence.group(1)[0:1] == fence_marker[0:1]
                and len(closing_fence.group(1)) >= len(fence_marker)
            ):
                fence_marker = None
            continue

        opening_fence = MARKDOWN_FENCE_PATTERN.match(line)
        if opening_fence:
            fence_marker = opening_fence.group(1)
            continue

        if MARKDOWN_HORIZONTAL_RULE_PATTERN.fullmatch(line):
            replace_indexes.append(index)
    if not replace_indexes:
        return markdown_bytes, 0

    for index in replace_indexes:
        lines[index] = re.sub(rb'^( {0,3})---', rb'\1***', lines[index], count=1)

    return b''.join(lines), len(replace_indexes)

SUPPORTED_EPUB_MEDIA_TYPES = {
    '.mp3': ('audio', 'audio/mpeg'),
    '.m4a': ('audio', 'audio/mp4'),
    '.m4b': ('audio', 'audio/mp4'),
    '.aac': ('audio', 'audio/aac'),
    '.ogg': ('audio', 'audio/ogg'),
    '.oga': ('audio', 'audio/ogg'),
    '.opus': ('audio', 'audio/ogg'),
    '.wav': ('audio', 'audio/wav'),
    '.mp4': ('video', 'video/mp4'),
    '.m4v': ('video', 'video/mp4'),
    '.ogv': ('video', 'video/ogg'),
    '.webm': ('video', 'video/webm'),
}


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
        self.break_before = False  # fo:break-before="page" — разрыв страницы перед стилем


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
            style.break_before = get_attr(para_props, 'fo:break-before') == 'page'

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
                style.break_before = get_attr(para_props, 'fo:break-before') == 'page'

            styles_map[name] = style

    return styles_map


def extract_meta(meta_xml):
    """Извлечь метаданные"""
    meta = {
        'title': 'Untitled',
        'author': DEFAULT_AUTHOR,
        'publisher': '',
        'rights': '',
        'language': '',
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
        elif tag == 'publisher' and elem.text:
            meta['publisher'] = elem.text.strip()
        elif tag == 'rights' and elem.text:
            meta['rights'] = elem.text.strip()
        elif tag == 'language' and elem.text:
            meta['language'] = elem.text.strip()

    return meta


# ============================================================
# ODT → XHTML Converter
# ============================================================

# Методы разбиения EPUB на разделы (chapters)
SPLIT_METHODS = ('page-break', 'heading', 'none')


class XHTMLConverter:
    def __init__(self, styles_map, auto_styles_map, images, split_method='page-break'):
        self.styles_map = styles_map
        self.auto_styles_map = auto_styles_map
        self.images = images
        self.image_files = {}  # new_path -> bytes
        self.image_counter = 0
        self.css_rules = []
        self.split_method = split_method
        self.chapters = []  # список (title, xhtml) — результат разбиения

    def convert(self, content_xml):
        """Конвертировать content.xml в XHTML.

        Возвращает (xhtml, css, image_files, chapters), где chapters —
        список пар (заголовок_или_None, xhtml_раздела).
        """
        empty_chapters = [(None, '<p>Empty document</p>')]
        if not content_xml:
            return '<p>Empty document</p>', '', {}, empty_chapters

        try:
            root = ET.fromstring(content_xml)
        except ET.ParseError:
            return '<p>Parse error</p>', '', {}, [(None, '<p>Parse error</p>')]

        # Ищем body
        body = None
        for elem in root.iter():
            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if tag == 'body':
                body = elem
                break

        if body is None:
            return '<p>Empty document</p>', '', {}, empty_chapters

        # office:body содержит office:text — содержимое документа находится там
        for child in body:
            ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if ctag == 'text':
                body = child
                break

        # Генерируем CSS из стилей
        self._generate_css()

        # Конвертируем содержимое с разбиением на разделы
        blocks = self._split_body(body)
        self.chapters = self._build_chapters(blocks)
        xhtml = ''.join(xh for _, xh in self.chapters)

        # Формируем итоговый CSS
        css = self._build_css()

        return xhtml, css, self.image_files, self.chapters

    def _split_body(self, body):
        """Разбить body на блоки в соответствии с методом разбиения.

        Блок — это «атом» разбиения: элемент верхнего уровня body, который
        не был разрезан. Внутри блока абзацы со style break-before сохраняют
        явный page-break.
        """
        top = [c for c in body if not (c.tag.split('}')[-1] if '}' in c.tag else c.tag) == 'annotation']

        if self.split_method == 'none':
            return [('block', el) for el in top]

        if self.split_method == 'heading':
            blocks = []
            for el in top:
                tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag
                if tag == 'h':
                    blocks.append(('heading', el))
                else:
                    blocks.append(('block', el))
            return blocks

        # split_method == 'page-break' (по умолчанию)
        # Кандидаты на разрыв: hard-break (style break-before / draw:page-break)
        # и soft-break (стиль с fo:break-before="page" или имя стиля Chapter*)
        candidates = []  # (индекс в top, тип разрыва)
        for i, el in enumerate(top):
            kind = self._break_kind(el)
            if kind:
                candidates.append((i, kind))

        # Разрывы внутри нумерованных списков игнорируются (сохраняем нумерацию)
        list_starts = [i for i, el in enumerate(top)
                       if (el.tag.split('}')[-1] if '}' in el.tag else el.tag) == 'list']
        filtered = []
        for idx, kind in candidates:
            if kind != 'hard' and any(ls < idx for ls in list_starts):
                continue
            filtered.append((idx, kind))
        candidates = filtered

        # Соседние hard-разрывы склеиваем (не создаём пустые главы)
        kept = []
        for idx, kind in candidates:
            if kept and kind == 'hard' and idx == kept[-1][0] + 1:
                continue
            kept.append((idx, kind))

        # Пустых разделов быть не должно: первый кандидат не в начале документа
        if kept and kept[0][0] == 0:
            kept = kept[1:]

        blocks = []
        prev = 0
        for idx, _kind in kept:
            for el in top[prev:idx]:
                blocks.append(('block', el))
            start_el = top[idx]
            if self._has_break_attr(start_el):
                blocks.append(('heading', start_el))
            else:
                blocks.append(('soft', start_el))
            prev = idx
        for el in top[prev:]:
            blocks.append(('block', el))

        return blocks

    def _style_name(self, elem):
        """Имя стиля текстового элемента (с учётом text:p / h / span)"""
        return get_attr(elem, 'text:style-name') or ''

    def _has_break_attr(self, elem):
        """Проверяет явные атрибуты разрыва страницы у элемента"""
        style_name = self._style_name(elem)
        style_info = self.auto_styles_map.get(style_name) or self.styles_map.get(style_name)
        if style_info and getattr(style_info, 'break_before', False):
            return True
        if get_attr(elem, 'fo:break-before') == 'page':
            return True
        for child in elem.iter():
            ctag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
            if ctag == 'page-break':
                return True
        return False

    def _break_kind(self, elem):
        """Определить тип разрыва страницы перед элементом: hard / soft / None"""
        if self._has_break_attr(elem):
            return 'hard'
        # Мягкий критерий: стиль с break-before="page" или имя стиля Chapter*
        style_name = self._style_name(elem)
        if style_name.startswith('Chapter'):
            return 'soft'
        style_info = self.auto_styles_map.get(style_name) or self.styles_map.get(style_name)
        if style_info:
            resolved = style_info
            seen = set()
            while resolved is not None and resolved.name not in seen:
                seen.add(resolved.name)
                if getattr(resolved, 'break_before', False):
                    return 'soft'
                resolved = (self.styles_map.get(resolved.parent_style)
                            or self.auto_styles_map.get(resolved.parent_style))
        return None

    def _chapter_title(self, elem):
        """Извлечь текст заголовка раздела из элемента h"""
        parts = []
        for t in elem.itertext():
            parts.append(t)
        title = ''.join(parts).strip()
        return title or None

    def _build_chapters(self, blocks):
        """Собрать блоки в список разделов (title, xhtml)"""
        chapters = []
        cur_parts = []
        cur_title = None

        def flush():
            if cur_parts:
                chapters.append((cur_title, ''.join(cur_parts)))

        for kind, el in blocks:
            if kind == 'heading':
                flush()
                cur_parts = []
                cur_title = self._chapter_title(el)
                cur_parts.append(self._process_node(el))
            elif kind == 'soft':
                flush()
                cur_parts = []
                cur_title = None
                cur_parts.append('<div class="page-break">' + self._process_node(el) + '</div>')
            else:
                cur_parts.append(self._process_node(el))

        flush()
        if not chapters:
            chapters = [(None, '')]
        return chapters

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
.page-break { page-break-before: always; break-before: page; }
a { color: #0066cc; text-decoration: underline; }"""

        custom_css = '\n'.join(self.css_rules)
        return f"{base_css}\n{custom_css}" if custom_css else base_css

    def _process_node(self, elem):
        """Обработать узел"""
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag

        # Для блочных элементов текст элемента идёт внутрь тега, а не перед ним
        if tag in ('h', 'p'):
            result = ''
        elif tag == 'span':
            result = ''
        else:
            result = escape_html(elem.text) if elem.text else ''

        # Офисные контейнеры (office:text, office:body) — прозрачные, просто спускаться вниз
        if tag in ('text', 'body'):
            return result + self._process_children(elem)

        if tag == 'h':
            level = int(get_attr(elem, 'text:outline-level') or '1')
            level = max(1, min(6, level))
            content = escape_html(elem.text) if elem.text else ''
            content += self._process_children(elem)
            result += f'<h{level}>{content}</h{level}>'

        elif tag == 'p':
            style_name = get_attr(elem, 'text:style-name')
            safe_style = re.sub(r'[^a-zA-Z0-9_-]', '_', style_name)
            content = escape_html(elem.text) if elem.text else ''
            content += self._process_children(elem)
            if safe_style:
                result += f'<p class=\"{safe_style}\">{content}</p>'
            else:
                result += f'<p>{content}</p>'

        elif tag == 'span':
            style_name = get_attr(elem, 'text:style-name')
            safe_style = re.sub(r'[^a-zA-Z0-9_-]', '_', style_name)
            content = escape_html(elem.text) if elem.text else ''
            content += self._process_children(elem)

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

def generate_epub(xhtml, css, image_files, options, chapters=None):
    """Сгенерировать EPUB файл.

    chapters — список пар (заголовок_или_None, xhtml_раздела). Если не передан
    или содержит один раздел, EPUB формируется как единая глава (как раньше).
    """
    book_uuid = str(uuid.uuid4())

    if not chapters:
        chapters = [(None, xhtml)]

    # Создаём ZIP
    epub_buffer = io.BytesIO()

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
            media_type = get_image_mime_type(ext)
            item_id = f'img_{image_id}'
            image_id += 1

            # Путь может содержать кириллицу - zipfile автоматически использует UTF-8
            zf.writestr(path, data)
            manifest_items.append(f'    <item id="{item_id}" href="{file_name}" media-type="{media_type}"/>')

        # Обложка
        has_cover = False
        if options.get('cover_image'):
            # Определяем расширение из MIME-типа
            cover_mime = options.get('cover_mime_type', 'image/jpeg')
            if cover_mime == 'image/png':
                cover_ext = 'png'
            elif cover_mime == 'image/gif':
                cover_ext = 'gif'
            elif cover_mime == 'image/webp':
                cover_ext = 'webp'
            elif cover_mime == 'image/svg+xml':
                cover_ext = 'svg'
            elif cover_mime == 'image/bmp':
                cover_ext = 'bmp'
            elif cover_mime == 'image/tiff':
                cover_ext = 'tiff'
            else:
                cover_ext = 'jpg'
            cover_media_type = cover_mime
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

        # Основной контент: один XHTML-файл на раздел (главу)
        chapter_entries = []  # (file_name, title_or_None)
        for idx, (ch_title, ch_xhtml) in enumerate(chapters, 1):
            chapter_file = f'chapter{idx}.xhtml'
            chapter_title = ch_title or options['title']
            content_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{options['language']}">
<head>
  <meta charset="UTF-8"/>
  <title>{escape_xml(chapter_title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div id="content">
    {ch_xhtml}
  </div>
</body>
</html>'''

            zf.writestr(f'OEBPS/{chapter_file}', content_xhtml.encode('utf-8'))
            manifest_items.append(f'    <item id="chapter{idx}" href="{chapter_file}" media-type="application/xhtml+xml"/>')
            spine_items.append(f'    <itemref idref="chapter{idx}"/>')
            chapter_entries.append((chapter_file, chapter_title))

        # Навигационные пункты (одна запись на главу)
        nav_points = []
        for order, (chapter_file, chapter_title) in enumerate(chapter_entries, 1):
            nav_points.append(f'''    <navPoint id="navpoint{order}" playOrder="{order}">
      <navLabel><text>{escape_xml(chapter_title)}</text></navLabel>
      <content src="{chapter_file}"/>
    </navPoint>''')

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
{chr(10).join(nav_points)}
  </navMap>
</ncx>'''

        zf.writestr('OEBPS/toc.ncx', ncx.encode('utf-8'))
        manifest_items.append('    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

        # nav.xhtml (EPUB 3) — таблица содержимого со списком глав
        nav_lis = []
        for order, (chapter_file, chapter_title) in enumerate(chapter_entries, 1):
            nav_lis.append(f'      <li><a href="{chapter_file}">{escape_xml(chapter_title)}</a></li>')
        nav_xhtml = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{options['language']}">
<head>
  <meta charset="UTF-8"/>
  <title>{escape_xml(options['title'])}</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>{escape_xml(options['title'])}</h1>
    <ol>
{chr(10).join(nav_lis)}
    </ol>
  </nav>
</body>
</html>'''

        zf.writestr('OEBPS/nav.xhtml', nav_xhtml.encode('utf-8'))
        manifest_items.insert(0, '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        if options.get('interactive_toc'):
            toc_position = 1 if has_cover else 0
            spine_items.insert(toc_position, '    <itemref idref="nav"/>')

        # content.opf (манифест, явно кодируем в UTF-8)
        content_opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:identifier id="bookid">urn:uuid:{book_uuid}</dc:identifier>
    <dc:title>{escape_xml(options['title'])}</dc:title>
    <dc:creator>{escape_xml(options['author'])}</dc:creator>
    <dc:publisher>{escape_xml(options.get('publisher') or DEFAULT_PUBLISHER)}</dc:publisher>
    <dc:rights>{escape_xml(options.get('rights') or DEFAULT_RIGHTS)}</dc:rights>
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

    return epub_buffer.getvalue()


def _set_epub_metadata(
    package,
    opf_ns,
    identifier,
    layout_method,
    author=None,
    publisher=None,
    rights=None,
):
    metadata = package.find(f'{{{opf_ns}}}metadata')
    if metadata is None:
        metadata = ET.SubElement(package, f'{{{opf_ns}}}metadata')

    dc_ns = 'http://purl.org/dc/elements/1.1/'
    creators = metadata.findall(f'{{{dc_ns}}}creator')
    creator = next(
        (element for element in creators if element.text and element.text.strip()),
        creators[0] if creators else None,
    )
    if creator is None:
        creator = ET.SubElement(metadata, f'{{{dc_ns}}}creator')
    if author is not None:
        creator.text = author
    elif not any(element.text and element.text.strip() for element in creators):
        creator.text = DEFAULT_AUTHOR

    publisher_element = metadata.find(f'{{{dc_ns}}}publisher')
    if publisher_element is None:
        publisher_element = ET.SubElement(metadata, f'{{{dc_ns}}}publisher')
    if publisher is not None:
        publisher_element.text = publisher
    elif not publisher_element.text or not publisher_element.text.strip():
        publisher_element.text = DEFAULT_PUBLISHER
    rights_element = metadata.find(f'{{{dc_ns}}}rights')
    if rights_element is None:
        rights_element = ET.SubElement(metadata, f'{{{dc_ns}}}rights')
    if rights is not None:
        rights_element.text = rights
    elif not rights_element.text or not rights_element.text.strip():
        rights_element.text = DEFAULT_RIGHTS

    unique_identifier_id = package.get('unique-identifier')
    identifiers = metadata.findall(f'{{{dc_ns}}}identifier')
    identifier_element = next(
        (
            element
            for element in identifiers
            if element.get('id') == unique_identifier_id
        ),
        identifiers[0] if identifiers else None,
    )

    if identifier_element is None:
        identifier_id = unique_identifier_id or 'book-identifier'
        existing_ids = {
            element.get('id')
            for element in metadata
            if element.get('id')
        }
        base_identifier_id = identifier_id
        suffix = 1
        while identifier_id in existing_ids:
            suffix += 1
            identifier_id = f'{base_identifier_id}-{suffix}'
        identifier_element = ET.SubElement(
            metadata,
            f'{{{dc_ns}}}identifier',
            {'id': identifier_id},
        )
        package.set('unique-identifier', identifier_id)
    elif not identifier_element.get('id'):
        identifier_id = unique_identifier_id or 'book-identifier'
        identifier_element.set('id', identifier_id)
        package.set('unique-identifier', identifier_id)
    elif package.get('unique-identifier') != identifier_element.get('id'):
        package.set('unique-identifier', identifier_element.get('id'))

    if identifier is not None:
        identifier_element.text = identifier
    elif not identifier_element.text:
        identifier_element.text = f'urn:uuid:{uuid.uuid4()}'

    prefix = package.get('prefix', '')
    if not re.search(r'(^|\s)rendition:', prefix):
        prefix = ' '.join(
            part for part in (
                prefix,
                'rendition: http://www.idpf.org/vocab/rendition/#',
            )
            if part
        )
        package.set('prefix', prefix)

    layout_elements = [
        element
        for element in metadata.findall(f'{{{opf_ns}}}meta')
        if element.get('property') == 'rendition:layout'
    ]
    if layout_elements:
        if layout_method is None:
            layout_method = layout_elements[0].text or 'reflowable'
        layout_elements[0].text = layout_method
        for duplicate in layout_elements[1:]:
            metadata.remove(duplicate)
    else:
        layout_method = layout_method or 'reflowable'
        ET.SubElement(
            metadata,
            f'{{{opf_ns}}}meta',
            {'property': 'rendition:layout'},
        ).text = layout_method

    return identifier_element.text


def update_epub_metadata(
    epub_path,
    identifier=None,
    layout_method='reflowable',
    author=None,
    publisher=None,
    rights=None,
):
    epub_path = os.path.abspath(epub_path)
    temp_fd, temp_path = tempfile.mkstemp(
        prefix='.epub-metadata-',
        suffix='.tmp',
        dir=os.path.dirname(epub_path),
    )
    os.close(temp_fd)

    try:
        with zipfile.ZipFile(epub_path, 'r') as source:
            archive_names = {info.filename for info in source.infolist()}
            container = ET.fromstring(source.read('META-INF/container.xml'))
            container_ns = 'urn:oasis:names:tc:opendocument:xmlns:container'
            rootfile = container.find(f'.//{{{container_ns}}}rootfile')
            if rootfile is None or not rootfile.get('full-path'):
                raise ValueError('В EPUB не найден основной OPF-файл')

            opf_path = rootfile.get('full-path')
            package_directory = PurePosixPath(opf_path).parent
            package = ET.fromstring(source.read(opf_path))
            opf_ns = package.tag[1:].split('}', 1)[0] if package.tag.startswith('{') else ''
            manifest = package.find(f'{{{opf_ns}}}manifest')
            if manifest is None:
                raise ValueError('В EPUB отсутствует manifest')

            effective_identifier = _set_epub_metadata(
                package,
                opf_ns,
                identifier,
                layout_method,
                author,
                publisher,
                rights,
            )
            updates = {
                opf_path: ET.tostring(package, encoding='utf-8'),
            }

            ncx_item = next(
                (
                    item
                    for item in manifest.findall(f'{{{opf_ns}}}item')
                    if item.get('media-type') == 'application/x-dtbncx+xml'
                ),
                None,
            )
            if ncx_item is not None and ncx_item.get('href'):
                ncx_path = posixpath.normpath(
                    posixpath.join(str(package_directory), ncx_item.get('href').split('#', 1)[0])
                )
                if ncx_path in archive_names:
                    ncx_document = ET.fromstring(source.read(ncx_path))
                    ncx_ns = ncx_document.tag[1:].split('}', 1)[0] if ncx_document.tag.startswith('{') else ''
                    head = ncx_document.find(f'{{{ncx_ns}}}head')
                    if head is not None:
                        uid_meta = next(
                            (
                                element
                                for element in head.findall(f'{{{ncx_ns}}}meta')
                                if element.get('name') == 'dtb:uid'
                            ),
                            None,
                        )
                        if uid_meta is None:
                            uid_meta = ET.SubElement(
                                head,
                                f'{{{ncx_ns}}}meta',
                                {'name': 'dtb:uid'},
                            )
                        uid_meta.set('content', effective_identifier)
                        updates[ncx_path] = ET.tostring(
                            ncx_document,
                            encoding='utf-8',
                        )

            with zipfile.ZipFile(temp_path, 'w') as target:
                target.comment = source.comment
                for info in source.infolist():
                    if info.filename in updates:
                        target.writestr(info, updates.pop(info.filename))
                    else:
                        with source.open(info, 'r') as source_file:
                            with target.open(info, 'w') as target_file:
                                shutil.copyfileobj(source_file, target_file)
                for archive_path, content in updates.items():
                    target.writestr(archive_path, content, compress_type=zipfile.ZIP_DEFLATED)

        os.replace(temp_path, epub_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    return effective_identifier


def add_media_files_to_epub(
    epub_path,
    media_paths,
    media_root,
    identifier=None,
    layout_method='reflowable',
    author=None,
    publisher=None,
    rights=None,
):
    """Добавить аудио/видео, страницу воспроизведения и записи EPUB-манифеста."""
    media_paths = [
        Path(path)
        for path in media_paths
        if Path(path).suffix.lower() in SUPPORTED_EPUB_MEDIA_TYPES
    ]
    if not media_paths:
        return 0

    epub_path = os.path.abspath(epub_path)
    temp_fd, temp_path = tempfile.mkstemp(
        prefix='.epub-media-',
        suffix='.tmp',
        dir=os.path.dirname(epub_path),
    )
    os.close(temp_fd)

    try:
        with zipfile.ZipFile(epub_path, 'r') as source:
            archive_names = {info.filename for info in source.infolist()}
            container = ET.fromstring(source.read('META-INF/container.xml'))
            container_ns = 'urn:oasis:names:tc:opendocument:xmlns:container'
            rootfile = container.find(f'.//{{{container_ns}}}rootfile')
            if rootfile is None or not rootfile.get('full-path'):
                raise ValueError('В EPUB не найден основной OPF-файл')

            opf_path = rootfile.get('full-path')
            package_directory = PurePosixPath(opf_path).parent
            package = ET.fromstring(source.read(opf_path))
            opf_ns = package.tag[1:].split('}', 1)[0] if package.tag.startswith('{') else ''
            manifest = package.find(f'{{{opf_ns}}}manifest')
            spine = package.find(f'{{{opf_ns}}}spine')
            if manifest is None or spine is None:
                raise ValueError('В EPUB отсутствует manifest или spine')

            effective_identifier = _set_epub_metadata(
                package,
                opf_ns,
                identifier,
                layout_method,
                author,
                publisher,
                rights,
            )
            used_ids = {
                item.get('id')
                for item in manifest.findall(f'{{{opf_ns}}}item')
                if item.get('id')
            }

            def unique_id(prefix):
                candidate = prefix
                suffix = 1
                while candidate in used_ids:
                    suffix += 1
                    candidate = f'{prefix}-{suffix}'
                used_ids.add(candidate)
                return candidate

            page_name = 'media.xhtml'
            page_suffix = 1
            page_archive_path = str(package_directory / page_name)
            while page_archive_path in archive_names:
                page_suffix += 1
                page_name = f'media-{page_suffix}.xhtml'
                page_archive_path = str(package_directory / page_name)

            media_directory_name = 'media-assets'
            media_directory_path = str(package_directory / media_directory_name)
            while any(
                name == media_directory_path or name.startswith(media_directory_path + '/')
                for name in archive_names
            ):
                media_directory_name += '-extra'
                media_directory_path = str(package_directory / media_directory_name)

            media_root = Path(os.path.abspath(media_root))
            media_page_items = []
            media_archive_paths = []
            for index, media_path in enumerate(media_paths, start=1):
                media_kind, mime_type = SUPPORTED_EPUB_MEDIA_TYPES[media_path.suffix.lower()]
                media_name = f'asset-{index:04d}{media_path.suffix.lower()}'
                media_href = f'{media_directory_name}/{media_name}'
                media_archive_path = str(package_directory / media_href)
                try:
                    label = media_path.relative_to(media_root).as_posix()
                except ValueError:
                    label = media_path.name

                item_id = unique_id(f'external-media-{index}')
                ET.SubElement(
                    manifest,
                    f'{{{opf_ns}}}item',
                    {
                        'id': item_id,
                        'href': media_href,
                        'media-type': mime_type,
                    },
                )
                media_archive_paths.append((media_archive_path, media_path))
                media_element = (
                    f'<{media_kind} controls="controls" preload="metadata">'
                    f'<source src="{media_href}" type="{mime_type}"/>'
                    f'Воспроизведение не поддерживается этой EPUB-читалкой.'
                    f'</{media_kind}>'
                )
                media_page_items.append(
                    f'<section><h2>{escape_xml(label)}</h2>{media_element}</section>'
                )

            page_id = unique_id('external-media-page')
            ET.SubElement(
                manifest,
                f'{{{opf_ns}}}item',
                {
                    'id': page_id,
                    'href': page_name,
                    'media-type': 'application/xhtml+xml',
                },
            )
            ET.SubElement(spine, f'{{{opf_ns}}}itemref', {'idref': page_id})

            language = 'ru'
            metadata = package.find(f'{{{opf_ns}}}metadata')
            if metadata is not None:
                dc_ns = 'http://purl.org/dc/elements/1.1/'
                language_node = metadata.find(f'{{{dc_ns}}}language')
                if language_node is not None and language_node.text:
                    language = language_node.text

            media_page = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{escape_xml(language)}">
<head><meta charset="UTF-8"/><title>Медиафайлы</title></head>
<body><h1>Медиафайлы</h1>{''.join(media_page_items)}</body>
</html>'''

            updates = {
                opf_path: ET.tostring(package, encoding='utf-8'),
                page_archive_path: media_page.encode('utf-8'),
            }

            nav_item = next(
                (
                    item
                    for item in manifest.findall(f'{{{opf_ns}}}item')
                    if 'nav' in item.get('properties', '').split()
                ),
                None,
            )
            if nav_item is not None and nav_item.get('href'):
                nav_path = posixpath.normpath(
                    posixpath.join(str(package_directory), nav_item.get('href').split('#', 1)[0])
                )
                if nav_path in archive_names:
                    nav_document = ET.fromstring(source.read(nav_path))
                    xhtml_ns = nav_document.tag[1:].split('}', 1)[0] if nav_document.tag.startswith('{') else ''
                    toc_list = nav_document.find(f'.//{{{xhtml_ns}}}ol')
                    if toc_list is not None:
                        nav_parent = str(PurePosixPath(nav_path).parent)
                        nav_href = posixpath.relpath(page_archive_path, nav_parent)
                        list_item = ET.SubElement(toc_list, f'{{{xhtml_ns}}}li')
                        link = ET.SubElement(list_item, f'{{{xhtml_ns}}}a', {'href': nav_href})
                        link.text = 'Медиафайлы'
                        updates[nav_path] = ET.tostring(
                            nav_document,
                            encoding='utf-8',
                        )

            ncx_item = next(
                (
                    item
                    for item in manifest.findall(f'{{{opf_ns}}}item')
                    if item.get('media-type') == 'application/x-dtbncx+xml'
                ),
                None,
            )
            if ncx_item is not None and ncx_item.get('href'):
                ncx_path = posixpath.normpath(
                    posixpath.join(str(package_directory), ncx_item.get('href').split('#', 1)[0])
                )
                if ncx_path in archive_names:
                    ncx_document = ET.fromstring(source.read(ncx_path))
                    ncx_ns = ncx_document.tag[1:].split('}', 1)[0] if ncx_document.tag.startswith('{') else ''
                    ncx_head = ncx_document.find(f'{{{ncx_ns}}}head')
                    if ncx_head is not None:
                        uid_meta = next(
                            (
                                element
                                for element in ncx_head.findall(f'{{{ncx_ns}}}meta')
                                if element.get('name') == 'dtb:uid'
                            ),
                            None,
                        )
                        if uid_meta is None:
                            uid_meta = ET.SubElement(
                                ncx_head,
                                f'{{{ncx_ns}}}meta',
                                {'name': 'dtb:uid'},
                            )
                        uid_meta.set('content', effective_identifier)
                    nav_map = ncx_document.find(f'{{{ncx_ns}}}navMap')
                    if nav_map is not None:
                        play_orders = [
                            int(node.get('playOrder', '0'))
                            for node in nav_map.findall(f'.//{{{ncx_ns}}}navPoint')
                        ]
                        next_order = max(play_orders, default=0) + 1
                        ncx_ids = {
                            node.get('id')
                            for node in nav_map.findall(f'.//{{{ncx_ns}}}navPoint')
                            if node.get('id')
                        }
                        ncx_point_id = 'external-media-navigation'
                        ncx_id_suffix = 1
                        while ncx_point_id in ncx_ids:
                            ncx_id_suffix += 1
                            ncx_point_id = f'external-media-navigation-{ncx_id_suffix}'
                        point = ET.SubElement(
                            nav_map,
                            f'{{{ncx_ns}}}navPoint',
                            {
                                'id': ncx_point_id,
                                'playOrder': str(next_order),
                            },
                        )
                        label = ET.SubElement(
                            ET.SubElement(point, f'{{{ncx_ns}}}navLabel'),
                            f'{{{ncx_ns}}}text',
                        )
                        label.text = 'Медиафайлы'
                        ncx_parent = str(PurePosixPath(ncx_path).parent)
                        ncx_href = posixpath.relpath(page_archive_path, ncx_parent)
                        ET.SubElement(point, f'{{{ncx_ns}}}content', {'src': ncx_href})
                        updates[ncx_path] = ET.tostring(
                            ncx_document,
                            encoding='utf-8',
                        )

            with zipfile.ZipFile(temp_path, 'w') as target:
                target.comment = source.comment
                for info in source.infolist():
                    if info.filename in updates:
                        target.writestr(info, updates.pop(info.filename))
                    else:
                        with source.open(info, 'r') as source_file:
                            with target.open(info, 'w') as target_file:
                                shutil.copyfileobj(source_file, target_file)
                for archive_path, content in updates.items():
                    target.writestr(archive_path, content, compress_type=zipfile.ZIP_DEFLATED)
                for archive_path, media_path in media_archive_paths:
                    target.write(
                        str(media_path),
                        archive_path,
                        compress_type=zipfile.ZIP_DEFLATED,
                    )

        os.replace(temp_path, epub_path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    return len(media_archive_paths)


# ============================================================
# Main
# ============================================================

def main():
    global OUTPUT_MODE, LOG_FILE_PATH
    LOG_FILE_PATH = None

    source_format_aliases = {
        'odt': 'odt',
        'md': 'md',
        'markdown': 'md',
        'txt': 'txt',
        'pdf': 'pdf',
        'image': 'images',
        'images': 'images',
    }

    def parse_input_formats(value):
        formats = [part.strip().lower().lstrip('.') for part in value.split(',') if part.strip()]
        if not formats:
            raise argparse.ArgumentTypeError('укажите хотя бы один формат через запятую')
        unknown_formats = sorted(set(formats) - source_format_aliases.keys())
        if unknown_formats:
            allowed = ', '.join(sorted(source_format_aliases))
            raise argparse.ArgumentTypeError(
                f'неизвестный формат {", ".join(unknown_formats)}; допустимые значения: {allowed}'
            )
        return [source_format_aliases[format_name] for format_name in formats]

    def parse_identifier(value):
        identifier = value.strip()
        if not identifier:
            raise argparse.ArgumentTypeError('идентификатор не может быть пустым')
        return identifier

    source_format_outputs = {
        'odt': {'epub', 'md', 'pdf', 'txt'},
        'md': {'epub', 'md', 'pdf'},
        'txt': {'epub', 'pdf', 'txt'},
        'pdf': {'epub', 'pdf'},
        'images': {'epub'},
    }

    parser = argparse.ArgumentParser(
        description='Конвертация ODT в EPUB, Markdown, PDF или TXT; Markdown/TXT/PDF и папок с изображениями — в EPUB',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Примеры:
  python convert.py
  python convert.py document.odt
    python convert.py document.odt --format md
  python convert.py file1.odt file2.odt
  python convert.py /path/to/books
  python convert.py file.odt /path/to/books
  python convert.py -e "temp/*" -e "*_draft.odt"
  python convert.py --exclude-file exclude.txt
  python convert.py document.odt --split-method heading

Примечание: файлы, начинающиеся с _ или имеющие суффикс _Draft (например,
_notes.odt или test_Draft.odt), автоматически пропускаются.
        '''
    )

    parser.add_argument('paths', nargs='*', help='Пути к файлам или директориям (если не указаны, обрабатывается текущая директория)')
    output_format_choices = ('epub', 'md', 'pdf', 'txt')
    parser.add_argument('--format', choices=output_format_choices, default=None,
                        help='Формат результата: epub, md, pdf или txt; если не указан, определяется по расширению -o (иначе epub)')
    parser.add_argument(
        '--input-formats',
        action='append',
        type=parse_input_formats,
        metavar='LIST',
        help='Фильтр форматов при поиске в папках, например odt,md,txt; можно указывать несколько раз. Допустимо: odt, md/markdown, txt, pdf, images',
    )
    parser.add_argument('--mode', choices=('quiet', 'summary', 'verbose'), default='verbose',
                        help='Режим вывода: quiet — только критические ошибки, summary — строка результата по файлу, verbose — подробный вывод')
    parser.add_argument('--log-file', metavar='PATH', help='Записывать подробный журнал в файл (добавление в конец; относительный путь от текущей папки)')
    parser.add_argument('-c', '--cover', help='Путь к обложке (JPEG/PNG/GIF/WebP/SVG/BMP/TIFF); автопоиск по имени выполняется для ODT')
    parser.add_argument('-o', '--output', help='Путь к файлу результата или папке назначения; для ODT сохраняется суффикс даты')
    overwrite_group = parser.add_mutually_exclusive_group()
    overwrite_group.add_argument('--ask-overwrite', action='store_true',
                                 help='Спрашивать перед заменой существующего файла (иначе существующий файл пропускается)')
    overwrite_group.add_argument('--force', action='store_true',
                                 help='Принудительно перезаписывать существующие файлы без запроса')
    parser.add_argument('--no-timestamp', action='store_true',
                        help='Не добавлять дату и время к имени EPUB, созданного из ODT; действует и в пакетном режиме')
    parser.add_argument('-t', '--title', help='Название книги (по умолчанию: имя исходного файла без расширения)')
    parser.add_argument(
        '--identifier',
        type=parse_identifier,
        help='Идентификатор книги для метаданных EPUB (например, ISBN или URN; по умолчанию генерируется автоматически)',
    )
    parser.add_argument(
        '-a',
        '--author',
        help=f'Автор книги (по умолчанию из метаданных ODT или {DEFAULT_AUTHOR})',
    )
    parser.add_argument(
        '--publisher',
        default=None,
        help=f'Издатель книги (по умолчанию: {DEFAULT_PUBLISHER})',
    )
    parser.add_argument(
        '--rights',
        default=None,
        help=f'Информация об авторских правах (по умолчанию: {DEFAULT_RIGHTS})',
    )
    parser.add_argument('-l', '--lang', default='', help='Язык книги (ISO 639-1). По умолчанию: из метаданных ODT, при их отсутствии — ru')
    parser.add_argument(
        '--css',
        metavar='PATH',
        help='Файл CSS для оформления EPUB; для ODT его правила добавляются после автоматически созданных',
    )
    parser.add_argument(
        '--toc',
        action='store_true',
        help='Добавить в EPUB видимое интерактивное оглавление',
    )
    parser.add_argument(
        '--layout-method',
        choices=('reflowable', 'pre-paginated'),
        default=None,
        help='Режим компоновки EPUB: reflowable (по умолчанию) или pre-paginated',
    )
    parser.add_argument('-e', '--exclude', action='append',
                        help='Исключить файл или папку (можно повторять; glob-шаблоны сверяются с именем и относительным путём)')
    parser.add_argument('--exclude-file', help='Файл со списком исключений (по одному на строку, # для комментариев)')
    parser.add_argument('--no-recursive', action='store_true', help='Отключить рекурсивный поиск в поддиректориях (по умолчанию: включен)')
    parser.add_argument('-m', '--media-dir', dest='media_dir', default=None, help='Папка для автопоиска обложек/медиафайлов (по умолчанию: текущая папка, рекурсивно с подпапками)')
    parser.add_argument('--split-method', dest='split_method', choices=SPLIT_METHODS, default=None,
                        help='Метод разбиения EPUB на разделы: page-break — по разрывам страниц ODT (по умолчанию), '
                               'heading — по заголовкам, none — без разбиения (единая глава)')

    args = parser.parse_args()
    if args.format is None:
        output_path = Path(args.output) if args.output else None
        output_is_directory = bool(
            output_path
            and (
                output_path.is_dir()
                or args.output.endswith(('/', '\\'))
            )
        )
        if output_path and not output_is_directory and not output_path.is_absolute():
            input_paths = args.paths or ['.']
            output_is_directory = any(
                (Path(input_path) / output_path).is_dir()
                if Path(input_path).is_dir()
                else (Path(input_path).parent / output_path).is_dir()
                for input_path in input_paths
                if Path(input_path).is_dir() or Path(input_path).is_file()
            )
        output_suffix = (
            output_path.suffix.lower().lstrip('.')
            if output_path and not output_is_directory
            else ''
        )
        args.format = output_suffix if output_suffix in output_format_choices else 'epub'
    args.media_dir_explicit = args.media_dir is not None
    OUTPUT_MODE = args.mode

    ignored_epub_options = []
    if args.format != 'epub':
        for attribute, option, provided, replacement in (
            ('cover', '--cover', args.cover is not None, None),
            ('identifier', '--identifier', args.identifier is not None, None),
            ('publisher', '--publisher', args.publisher is not None, None),
            ('rights', '--rights', args.rights is not None, None),
            ('layout_method', '--layout-method', args.layout_method is not None, None),
            ('css', '--css', args.css is not None, None),
            ('toc', '--toc', args.toc, False),
            ('no_timestamp', '--no-timestamp', args.no_timestamp, False),
            ('media_dir', '--media-dir', args.media_dir_explicit, None),
            ('split_method', '--split-method', args.split_method is not None, None),
        ):
            if provided:
                ignored_epub_options.append(option)
                setattr(args, attribute, replacement)
        if ignored_epub_options:
            log_error(
                f'Параметры {", ".join(ignored_epub_options)} не применимы к формату '
                f'{args.format} и будут пропущены.',
                critical=False,
            )

    args.media_dir = args.media_dir or '.'
    args.split_method = args.split_method or 'page-break'
    if args.media_dir_explicit and args.format == 'epub' and not os.path.isdir(args.media_dir):
        parser.error(f'папка медиа не найдена или не является папкой: {args.media_dir}')

    custom_css = None
    if args.css:
        args.css = os.path.abspath(args.css)
        try:
            with open(args.css, 'r', encoding='utf-8') as css_file:
                custom_css = css_file.read()
        except (OSError, UnicodeError) as e:
            parser.error(f'не удалось прочитать файл CSS {args.css}: {e}')

    if args.log_file:
        log_file_path = os.path.abspath(args.log_file)
        try:
            Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)
            with open(log_file_path, 'a', encoding='utf-8') as log_file:
                log_file.write(f'\n=== Запуск {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ===\n')
        except OSError as e:
            log_error(f'Не удалось открыть журнал {log_file_path}: {e}')
            sys.exit(1)
        LOG_FILE_PATH = log_file_path

    warn_missing_external_utilities()

    compatible_source_formats = {
        source_format
        for source_format, output_formats in source_format_outputs.items()
        if args.format in output_formats
    }
    if args.input_formats:
        selected_source_formats = {
            source_format
            for format_list in args.input_formats
            for source_format in format_list
        }
        unsupported_formats = sorted(
            source_format
            for source_format in selected_source_formats
            if source_format not in compatible_source_formats
        )
        if unsupported_formats:
            parser.error(
                f'Форматы источников {", ".join(unsupported_formats)} нельзя преобразовать в {args.format}'
            )
    else:
        selected_source_formats = compatible_source_formats

    convertible_input_extensions = {
        f'.{source_format}'
        for source_format in compatible_source_formats
        if source_format != 'images'
    }
    input_extensions = {
        f'.{source_format}'
        for source_format in selected_source_formats
        if source_format != 'images'
    }

    verbose_print('\n  ╔══════════════════════════════════════════╗')
    verbose_print('  ║          ODT Converter v1.0.0           ║')
    verbose_print('  ╚══════════════════════════════════════════╝\n')

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
    def should_exclude(file_path, root_path=None):
        """Проверяет, должен ли файл быть исключён"""
        file_path = Path(file_path)
        file_name = file_path.name

        if LOG_FILE_PATH and os.path.abspath(file_path) == LOG_FILE_PATH:
            return True

        # Исходные файлы с подчёркиванием в начале имени не обрабатываются.
        if file_path.is_file() and file_name.startswith('_'):
            return True

        # Файлы с суффиксом Draft (через подчёркивание) не обрабатываются.
        if Path(file_name).stem.lower().endswith('_draft'):
            return True

        path_candidates = {
            file_path.as_posix(),
            file_name,
            Path(os.path.relpath(file_path, os.getcwd())).as_posix(),
        }
        if root_path is not None:
            try:
                path_candidates.add(file_path.relative_to(root_path).as_posix())
            except ValueError:
                pass

        for pattern in exclude_patterns:
            pattern = os.path.normpath(pattern).replace('\\', '/')
            for candidate in path_candidates:
                candidate_path = Path(candidate)
                candidate_paths = {candidate_path.as_posix()}
                candidate_paths.update(
                    parent.as_posix()
                    for parent in candidate_path.parents
                    if str(parent) not in ('.', '..')
                )
                if any(
                    fnmatch(os.path.normpath(value).replace(os.sep, '/'), pattern)
                    for value in candidate_paths
                ):
                    return True

        return False

    def find_epub_media_files(media_dir, recursive):
        media_root = os.path.abspath(media_dir) if media_dir else os.getcwd()
        if not os.path.isdir(media_root):
            log_error(f'Папка медиа не найдена: {media_root}', critical=False)
            return [], media_root

        media_paths = []
        if recursive and not args.no_recursive:
            for root, dirs, files in os.walk(media_root):
                dirs[:] = [
                    directory_name
                    for directory_name in dirs
                    if not should_exclude(Path(root) / directory_name, root_path=media_root)
                ]
                for file_name in files:
                    media_path = Path(root) / file_name
                    if (
                        media_path.suffix.lower() in SUPPORTED_EPUB_MEDIA_TYPES
                        and not should_exclude(media_path, root_path=media_root)
                    ):
                        media_paths.append(media_path)
        else:
            for file_name in os.listdir(media_root):
                media_path = Path(media_root) / file_name
                if (
                    media_path.is_file()
                    and media_path.suffix.lower() in SUPPORTED_EPUB_MEDIA_TYPES
                    and not should_exclude(media_path, root_path=media_root)
                ):
                    media_paths.append(media_path)

        media_paths.sort(key=lambda media_path: media_path.relative_to(media_root).as_posix().casefold())
        return media_paths, media_root

    def attach_epub_media(output_path, media_dir, recursive):
        media_paths, media_root = find_epub_media_files(media_dir, recursive)
        try:
            if media_paths:
                media_count = add_media_files_to_epub(
                    output_path,
                    media_paths,
                    media_root,
                    identifier=args.identifier,
                    layout_method=args.layout_method,
                    author=args.author,
                    publisher=args.publisher,
                    rights=args.rights,
                )
            else:
                update_epub_metadata(
                    output_path,
                    identifier=args.identifier,
                    layout_method=args.layout_method,
                    author=args.author,
                    publisher=args.publisher,
                    rights=args.rights,
                )
                media_count = 0
        except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile) as e:
            log_error(f'Не удалось обновить метаданные EPUB или добавить аудио/видео: {e}')
            return False

        if media_count:
            log_ok(f'В EPUB добавлено медиафайлов: {media_count}')
        return True

    def allow_output_write(output_path, result_info):
        if not os.path.lexists(output_path):
            return True
        if args.force:
            return True

        if args.ask_overwrite:
            if not sys.stdin.isatty():
                log_error(
                    f'Нельзя запросить перезапись {output_path}: ввод не является интерактивным. '
                    'Используйте --force или запустите команду в терминале.'
                )
                result_info['skipped'] = True
                return False

            prompt = f'Файл уже существует: {output_path}. Перезаписать? [y/N] '
            print(
                prompt,
                end='',
                file=sys.stderr,
                flush=True,
            )
            write_file_log(prompt)
            answer = input().strip().lower()
            if answer in ('y', 'yes', 'д', 'да'):
                return True

        log(f'Существующий файл пропущен: {output_path}')
        result_info['skipped'] = True
        return False

    def prepare_output_parent(output_path):
        if LOG_FILE_PATH and os.path.abspath(output_path) == LOG_FILE_PATH:
            log_error(f'Путь журнала совпадает с выходным файлом: {output_path}')
            return False
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log_error(f'Не удалось создать папку для результата {output_path}: {e}')
            return False
        return True

    def path_identity_keys(path):
        resolved_path = os.path.normcase(os.path.realpath(path))
        identities = {('path', resolved_path)}
        try:
            stat_result = os.stat(path)
        except OSError:
            pass
        else:
            identities.add(('inode', stat_result.st_dev, stat_result.st_ino))
        return identities

    def paths_refer_to_same_file(first_path, second_path):
        return not path_identity_keys(first_path).isdisjoint(
            path_identity_keys(second_path)
        )

    # Обработка одного файла
    def process_single_file(input_path, explicit_cover=None, explicit_output=None, explicit_title=None, explicit_author=None, explicit_lang='', media_dir='.', media_recursive=True, result_info=None):
        """Обрабатывает один входной файл. Возвращает True при успехе."""
        if result_info is None:
            result_info = {}
        result_info['output_path'] = None
        result_info['skipped'] = False

        input_path = os.path.abspath(input_path)

        if not os.path.exists(input_path):
            log_error(f'Файл не найден: {input_path}')
            return False

        input_extension = Path(input_path).suffix.lower()
        if input_extension not in convertible_input_extensions:
            extensions = ', '.join(sorted(convertible_input_extensions))
            log_error(f'Формат входного файла не поддерживается (ожидается {extensions}): {input_path}')
            return False

        if input_extension == f'.{args.format}':
            result_info['output_path'] = input_path
            result_info['skipped'] = True
            log(f'Форматы источника и результата совпадают; файл пропущен: {input_path}')
            return False

        is_markdown = input_extension == '.md'
        is_plain_text = input_extension == '.txt'
        is_pdf = input_extension == '.pdf'
        is_text_source = is_markdown or is_plain_text
        if args.format in ('md', 'pdf', 'txt') or is_text_source or is_pdf:
            pandoc_formats = {'md': 'markdown', 'pdf': 'pdf', 'txt': 'plain', 'epub': 'epub'}
            format_names = {'md': 'Markdown', 'pdf': 'PDF', 'txt': 'TXT', 'epub': 'EPUB'}
            output_path = os.path.abspath(explicit_output) if explicit_output else str(Path(input_path).with_suffix(f'.{args.format}'))
            if paths_refer_to_same_file(input_path, output_path):
                log_error(f'Путь результата совпадает с исходным файлом: {input_path}')
                result_info['output_path'] = output_path
                return False
            if not prepare_output_parent(output_path):
                return False
            if result_info is not None:
                result_info['output_path'] = output_path
            if not allow_output_write(output_path, result_info):
                return False
            source_format = 'markdown' if is_text_source or is_pdf else 'odt'
            source_name = 'PDF' if is_pdf else 'Markdown' if is_markdown else 'TXT' if is_plain_text else 'ODT'
            if not is_pdf:
                log_step('1/1', f'Конвертация {source_name} в {format_names[args.format]} с Pandoc...')
            pandoc_command = [
                'pandoc',
                f'--from={source_format}',
                f'--to={pandoc_formats[args.format]}',
            ]
            if args.format == 'pdf':
                pandoc_command.append(f'--pdf-engine={PDF_ENGINE}')
                pandoc_command.extend(['-V', 'mainfont=DejaVu Serif'])
            if is_pdf:
                pandoc_command.extend(['-M', f'title={explicit_title or Path(input_path).stem}'])
                pandoc_command.extend(['-M', f'lang={explicit_lang or "ru"}'])
                if explicit_author:
                    pandoc_command.extend(['-M', f'author={explicit_author}'])
            elif is_text_source:
                if explicit_title:
                    pandoc_command.extend(['-M', f'title={explicit_title}'])
                if explicit_author:
                    pandoc_command.extend(['-M', f'author={explicit_author}'])
                if explicit_lang:
                    pandoc_command.extend(['-M', f'lang={explicit_lang}'])
            if (is_text_source or is_pdf) and args.format == 'epub' and explicit_cover:
                cover_path = os.path.abspath(explicit_cover)
                if not os.path.isfile(cover_path):
                    log_error(f'Файл обложки не найден: {cover_path}')
                    return False
                pandoc_command.append(f'--epub-cover-image={cover_path}')
            if args.format == 'epub':
                if args.css:
                    pandoc_command.extend(['--css', args.css])
                if args.toc:
                    pandoc_command.append('--toc')

            def run_pandoc(command):
                if args.format == 'pdf' and shutil.which(PDF_ENGINE) is None:
                    log_error(
                        'xelatex не найден. Установите TeX Live '
                        '(например, пакеты texlive-xetex, '
                        'texlive-latex-recommended и fonts-dejavu-core) '
                        'для создания PDF с поддержкой Unicode.'
                    )
                    return False
                try:
                    subprocess.run(
                        command,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                    )
                except FileNotFoundError:
                    log_error('Pandoc не найден. Установите Pandoc и добавьте его в PATH для конвертации.')
                    return False
                except subprocess.CalledProcessError as e:
                    error = e.stderr.strip() or str(e)
                    log_error(f'Ошибка Pandoc: {error}')
                    return False
                except OSError as e:
                    log_error(f'Ошибка запуска Pandoc: {e}')
                    return False
                return True

            if is_pdf:
                with tempfile.TemporaryDirectory(prefix='odt2epub-') as temp_dir:
                    extracted_text_path = Path(temp_dir) / 'extracted.txt'
                    log_step('1/2', 'Извлечение текста из PDF с помощью pdftotext...')
                    try:
                        subprocess.run(
                            ['pdftotext', '-enc', 'UTF-8', '-nopgbrk', input_path, str(extracted_text_path)],
                            check=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            universal_newlines=True,
                        )
                    except FileNotFoundError:
                        log_error('pdftotext не найден. Установите Poppler и добавьте его в PATH для конвертации PDF.')
                        return False
                    except subprocess.CalledProcessError as e:
                        error = e.stderr.strip() or str(e)
                        log_error(f'Ошибка извлечения текста из PDF: {error}')
                        return False
                    except OSError as e:
                        log_error(f'Ошибка запуска pdftotext: {e}')
                        return False

                    try:
                        with extracted_text_path.open(encoding='utf-8') as extracted_text:
                            has_extractable_text = any(line.strip() for line in extracted_text)
                    except (OSError, UnicodeError) as e:
                        log_error(f'Не удалось прочитать извлечённый текст PDF: {e}')
                        return False

                    if has_extractable_text:
                        pandoc_input_path = extracted_text_path
                        log_step('2/2', 'Создание EPUB из текста PDF с помощью Pandoc...')
                    else:
                        log('Текстовый слой не найден; страницы PDF будут добавлены в EPUB как изображения.')
                        log_step('2/3', 'Рендеринг страниц PDF в изображения с помощью pdftoppm...')
                        image_prefix = Path(temp_dir) / 'page'
                        try:
                            subprocess.run(
                                ['pdftoppm', '-png', '-r', '150', input_path, str(image_prefix)],
                                check=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True,
                            )
                        except FileNotFoundError:
                            log_error('pdftoppm не найден. Установите Poppler и добавьте pdftoppm в PATH для обработки сканированных PDF.')
                            return False
                        except subprocess.CalledProcessError as e:
                            error = e.stderr.strip() or str(e)
                            log_error(f'Ошибка рендеринга страниц PDF: {error}')
                            return False
                        except OSError as e:
                            log_error(f'Ошибка запуска pdftoppm: {e}')
                            return False

                        page_images = sorted(
                            Path(temp_dir).glob('page-*.png'),
                            key=lambda page: int(page.stem.rsplit('-', 1)[1]),
                        )
                        if not page_images:
                            log_error('pdftoppm не создал изображения страниц PDF.')
                            return False

                        pages_markdown = '\n\n'.join(
                            f'![Страница {index}]({page.name})'
                            for index, page in enumerate(page_images, start=1)
                        ) + '\n'
                        pandoc_input_path = Path(temp_dir) / 'pages.md'
                        try:
                            pandoc_input_path.write_text(pages_markdown, encoding='utf-8')
                        except OSError as e:
                            log_error(f'Не удалось создать список страниц PDF: {e}')
                            return False

                        pandoc_command.append(f'--resource-path={temp_dir}')
                        log_step('3/3', 'Создание EPUB из изображений страниц с помощью Pandoc...')

                    pandoc_command.extend([str(pandoc_input_path), '--output', output_path])
                    if not run_pandoc(pandoc_command):
                        return False
            else:
                pandoc_input_path = input_path
                if is_markdown and args.format in ('epub', 'pdf'):
                    try:
                        markdown_bytes = Path(input_path).read_bytes()
                    except OSError as e:
                        log_error(f'Не удалось прочитать Markdown-файл {input_path}: {e}')
                        return False

                    markdown_bytes, replaced_rules = _prepare_markdown_pdf_source(
                        markdown_bytes,
                    )
                    if replaced_rules:
                        try:
                            with tempfile.TemporaryDirectory(
                                prefix='odt2epub-markdown-',
                            ) as temp_dir:
                                pandoc_input_path = str(
                                    Path(temp_dir) / Path(input_path).name
                                )
                                Path(pandoc_input_path).write_bytes(markdown_bytes)
                                resource_paths = dict.fromkeys((
                                    str(Path(input_path).parent),
                                    os.getcwd(),
                                ))
                                pandoc_command.append(
                                    f'--resource-path={os.pathsep.join(resource_paths)}'
                                )
                                pandoc_command.extend([
                                    pandoc_input_path,
                                    '--output',
                                    output_path,
                                ])
                                if not run_pandoc(pandoc_command):
                                    return False
                        except OSError as e:
                            log_error(f'Не удалось создать временную копию Markdown-файла: {e}')
                            return False
                    else:
                        pandoc_command.extend([pandoc_input_path, '--output', output_path])
                        if not run_pandoc(pandoc_command):
                            return False
                else:
                    pandoc_command.extend([pandoc_input_path, '--output', output_path])
                    if not run_pandoc(pandoc_command):
                        return False

            if args.format == 'epub' and not attach_epub_media(
                output_path,
                media_dir,
                media_recursive,
            ):
                return False

            log_ok(f'{format_names[args.format]} сохранён: {output_path}')
            return True

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
            cover_mime_type = get_image_mime_type(ext)
            log_ok(f'Обложка: {os.path.basename(cover_path)} ({cover_mime_type})')
        else:
            # Автопоиск обложки в media-директории (по умолчанию: текущая папка с подпапками)
            odt_stem = Path(input_path).stem

            def _find_cover_in(dir_path):
                for ext in SUPPORTED_IMAGE_EXTENSIONS:
                    candidate = dir_path / f'{odt_stem}{ext}'
                    if candidate.exists():
                        return candidate
                return None

            found_candidate = None
            search_root = os.path.abspath(media_dir) if media_dir else '.'
            if not os.path.isdir(search_root):
                log_error(f'Папка медиа не найдена: {search_root}', critical=False)
            elif media_recursive and not args.no_recursive:
                # Рекурсивный обход: сначала более мелкие уровни глубины
                matches = []
                for root, dirs, files in os.walk(search_root):
                    dirs.sort()
                    cand = _find_cover_in(Path(root))
                    if cand:
                        matches.append((root.count(os.sep), cand))
                if matches:
                    matches.sort(key=lambda x: x[0])
                    found_candidate = matches[0][1]
            else:
                found_candidate = _find_cover_in(Path(search_root))

            if found_candidate:
                try:
                    with open(str(found_candidate), 'rb') as f:
                        cover_buffer = f.read()
                    cover_mime_type = get_image_mime_type(found_candidate.suffix.lower())
                    log_ok(f'Обложка найдена автоматически: {found_candidate} ({cover_mime_type})')
                except Exception as e:
                    log_error(f'Ошибка чтения найденной обложки {found_candidate.name}: {e}', critical=False)
                    cover_buffer = None
            else:
                log('ℹ️  Обложка не указана и не найдена в папке медиа (по умолчанию: текущая папка с подпапками)')

        # Определяем выходной путь
        if explicit_output:
            output_path = os.path.abspath(explicit_output)
        else:
            output_path = str(Path(input_path).with_suffix('.epub'))
            if not args.no_timestamp:
                timestamp = datetime.now().strftime('%H:%M_%d.%m.%Y')
                output_path = str(
                    Path(output_path).with_name(
                        f'{Path(output_path).stem}_({timestamp}){Path(output_path).suffix}'
                    )
                )
        if paths_refer_to_same_file(input_path, output_path):
            log_error(f'Путь результата совпадает с исходным файлом: {input_path}')
            result_info['output_path'] = output_path
            return False
        if not prepare_output_parent(output_path):
            return False
        if result_info is not None:
            result_info['output_path'] = output_path
        if not allow_output_write(output_path, result_info):
            return False

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
        author = explicit_author or meta['author'] or DEFAULT_AUTHOR
        language = explicit_lang or meta['language'] or 'ru'
        log_ok(f'Метаданные: "{title}" — {author}')

        # Шаг 3: Конвертируем содержимое
        log_step('3/5', 'Конвертация содержимого в XHTML...')
        converter = XHTMLConverter(styles_map, auto_styles_map, odt_content.images, split_method=args.split_method)
        xhtml, css, image_files, chapters = converter.convert(odt_content.content_xml)
        if custom_css:
            css = f'{css.rstrip()}\n\n{custom_css}'
        log_ok(f'XHTML сгенерирован ({len(xhtml)} символов, {len(image_files)} изображений)')
        log_ok(f'Разбиение: метод "{args.split_method}", разделов: {len(chapters)}')

        # Шаг 4: Генерируем EPUB
        log_step('4/5', 'Сборка EPUB...')
        # Используем дату генерации
        generation_date = datetime.now().isoformat(timespec='seconds')
        epub_buffer = generate_epub(xhtml, css, image_files, {
            'title': title,
            'author': author,
            'publisher': args.publisher if args.publisher is not None else (
                meta['publisher'] or DEFAULT_PUBLISHER
            ),
            'rights': args.rights if args.rights is not None else (
                meta['rights'] or DEFAULT_RIGHTS
            ),
            'date': generation_date,
            'language': language,
            'cover_image': cover_buffer,
            'cover_mime_type': cover_mime_type,
            'interactive_toc': args.toc,
        }, chapters=chapters)
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

        if not attach_epub_media(output_path, media_dir, media_recursive):
            return False

        # Итог
        verbose_print('\n  ┌──────────────────────────────────────────┐')
        verbose_print(f'  │  📖 {title}')
        verbose_print(f'  │  ✍️  {author}')
        verbose_print(f'  │  🌐 {language.upper()}')
        verbose_print(f'  │  📦 {epub_size_kb:.1f} КБ')
        verbose_print(f'  │  📁 {os.path.basename(output_path)}')
        verbose_print('  └──────────────────────────────────────────┘')
        verbose_print('')
        log_ok('Конвертация завершена!\n')
        return True

    def print_file_result(input_path, result_info, success):
        output_path = result_info.get('output_path')
        output_name = Path(output_path).name if output_path else '—'
        if result_info.get('skipped'):
            result = 'ПРОПУЩЕНО'
        else:
            result = 'УСПЕШНО' if success else 'ОШИБКА'
        result_line = f'{Path(input_path).name} — {output_name} — {result}'
        if OUTPUT_MODE == 'summary':
            print(result_line)
        write_file_log(result_line)

    def process_image_folder(directory_path, image_paths, explicit_output=None, result_info=None,
                             media_dir='.', media_recursive=True):
        """Создаёт EPUB, в котором каждое изображение является отдельной страницей."""
        if result_info is None:
            result_info = {}
        result_info['output_path'] = None
        result_info['skipped'] = False

        directory = Path(directory_path)
        output_path = (
            os.path.abspath(explicit_output)
            if explicit_output
            else str(directory.parent / f'{directory.name or "Images"}.epub')
        )
        result_info['output_path'] = output_path
        if any(paths_refer_to_same_file(output_path, image_path) for image_path in image_paths):
            log_error(f'Путь результата совпадает с исходным изображением: {output_path}')
            return False
        if not prepare_output_parent(output_path):
            return False
        if not allow_output_write(output_path, result_info):
            return False

        image_paths = sorted(
            image_paths,
            key=lambda image_path: [
                int(part) if part.isdigit() else part.casefold()
                for part in re.split(r'(\d+)', Path(image_path).relative_to(directory).as_posix())
            ],
        )
        markdown = '\n\n'.join(
            (
                f'## Страница {index}\n\n'
                if args.toc
                else ''
            )
            + f'![Страница {index}](<{Path(image_path).relative_to(directory).as_posix()}>)'
            for index, image_path in enumerate(image_paths, start=1)
        ) + '\n'

        try:
            with tempfile.TemporaryDirectory(prefix='odt2epub-images-') as temp_dir:
                markdown_path = Path(temp_dir) / 'images.md'
                markdown_path.write_text(markdown, encoding='utf-8')
                title = args.title or directory.name or 'Изображения'
                pandoc_command = [
                    'pandoc',
                    '--from=markdown',
                    '--to=epub',
                    '-M',
                    f'title={title}',
                    '-M',
                    f'lang={args.lang or "ru"}',
                ]
                pandoc_command.extend(['-M', f'author={args.author or DEFAULT_AUTHOR}'])
                if args.css:
                    pandoc_command.extend(['--css', args.css])
                if args.toc:
                    pandoc_command.append('--toc')
                if args.cover:
                    cover_path = os.path.abspath(args.cover)
                    if not os.path.isfile(cover_path):
                        log_error(f'Файл обложки не найден: {cover_path}')
                        return False
                    pandoc_command.append(f'--epub-cover-image={cover_path}')
                pandoc_command.extend([
                    f'--resource-path={directory}',
                    str(markdown_path),
                    '--output',
                    output_path,
                ])

                log_step('1/1', f'Создание EPUB из {len(image_paths)} изображений...')
                try:
                    subprocess.run(
                        pandoc_command,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        universal_newlines=True,
                    )
                except FileNotFoundError:
                    log_error('Pandoc не найден. Установите Pandoc и добавьте его в PATH для создания EPUB из изображений.')
                    return False
                except subprocess.CalledProcessError as e:
                    error = e.stderr.strip() or str(e)
                    log_error(f'Ошибка Pandoc: {error}')
                    return False
                except OSError as e:
                    log_error(f'Ошибка запуска Pandoc: {e}')
                    return False
        except OSError as e:
            log_error(f'Ошибка подготовки изображений для EPUB: {e}')
            return False

        if not attach_epub_media(output_path, media_dir, media_recursive):
            return False

        log_ok(f'EPUB сохранён: {output_path}')
        return True

    # Обработка директории
    def process_directory(directory_path):
        """Обрабатывает все поддерживаемые входные файлы в директории"""
        directory_path = os.path.abspath(directory_path)

        if not os.path.exists(directory_path):
            log_error(f'Директория не найдена: {directory_path}')
            return []

        if not os.path.isdir(directory_path):
            log_error(f'Указанный путь не является директорией: {directory_path}')
            return []

        # Собираем все ODT файлы
        all_input_files = []

        if args.no_recursive:
            # Нерекурсивный режим: только файлы в указанной директории
            for file_name in os.listdir(directory_path):
                file_path = Path(directory_path) / file_name
                if file_path.is_file() and file_path.suffix.lower() in input_extensions:
                    if not should_exclude(file_path, root_path=directory_path):
                        all_input_files.append(file_path)
        else:
            # Рекурсивный режим: обход всех поддиректорий
            for root, dirs, files in os.walk(directory_path):
                # Фильтруем директории по исключениям
                dirs[:] = [
                    d for d in dirs
                    if not should_exclude(Path(root) / d, root_path=directory_path)
                ]

                for file_name in files:
                    file_path = Path(root) / file_name
                    if file_path.suffix.lower() in input_extensions:
                        if not should_exclude(file_path, root_path=directory_path):
                            all_input_files.append(file_path)

        return all_input_files

    def find_image_files(directory_path):
        """Находит изображения в папке, соблюдая настройки рекурсии и исключения."""
        image_paths = []
        if args.no_recursive:
            for file_name in os.listdir(directory_path):
                file_path = Path(directory_path) / file_name
                if (
                    file_path.is_file()
                    and file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                    and not should_exclude(file_path, root_path=directory_path)
                ):
                    image_paths.append(file_path)
            return image_paths

        for root, dirs, files in os.walk(directory_path):
            dirs[:] = [
                directory_name
                for directory_name in dirs
                if not should_exclude(Path(root) / directory_name, root_path=directory_path)
            ]
            for file_name in files:
                file_path = Path(root) / file_name
                if (
                    file_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
                    and not should_exclude(file_path, root_path=directory_path)
                ):
                    image_paths.append(file_path)
        return image_paths

    # Определяем пути для обработки
    paths_to_process = args.paths if args.paths else ['.']

    # Собираем все файлы для обработки
    all_files = []
    image_books = []

    for path in paths_to_process:
        path = os.path.abspath(path)

        if not os.path.exists(path):
            log_error(f'Путь не найден: {path}')
            continue

        if os.path.isfile(path):
            # Это файл
            if Path(path).suffix.lower() in convertible_input_extensions:
                if should_exclude(Path(path)):
                    log(f'⏭️  Файл пропущен (исключение): {os.path.basename(path)}')
                else:
                    all_files.append(path)
            else:
                extensions = ', '.join(sorted(input_extensions))
                log_error(f'Формат входного файла не поддерживается (ожидается {extensions}): {path}')
        elif os.path.isdir(path):
            # Это директория
            if should_exclude(Path(path)):
                log(f'⏭️  Директория пропущена (исключение): {os.path.basename(path)}')
            else:
                files = process_directory(path)
                all_files.extend(files)
                if 'images' in selected_source_formats and not files:
                    image_paths = find_image_files(path)
                    if image_paths:
                        image_books.append((path, image_paths))

    if not all_files and not image_books:
        log('ℹ️  Файлы для конвертации не найдены')
        sys.exit(0)

    # Показываем список исключений
    if exclude_patterns:
        log(f'🚫 Исключения ({len(exclude_patterns)}):')
        for pattern in exclude_patterns:
            log(f'   • {pattern}')
        verbose_print()

    total_inputs = len(all_files) + len(image_books)
    log(f'📂 Найдено источников для конвертации: {total_inputs}')

    def output_for_source(source_path, image_folder=False):
        source_path = Path(source_path)
        source_directory = source_path if image_folder else source_path.parent
        output_path = None
        if args.output:
            requested_output = Path(args.output)
            if not requested_output.is_absolute():
                requested_output = source_directory / requested_output
            requested_output = Path(os.path.abspath(requested_output))

            is_output_directory = requested_output.is_dir() or (
                not requested_output.exists()
                and (
                    not Path(args.output).suffix
                    or args.output.endswith(('/', '\\'))
                )
            )
            if is_output_directory:
                source_name = source_path.name if image_folder else source_path.stem
                if not source_name:
                    source_name = 'Images'
                output_extension = '.epub' if image_folder else f'.{args.format}'
                output_path = requested_output / f'{source_name}{output_extension}'
            elif total_inputs == 1:
                output_path = requested_output

        if output_path is None:
            if image_folder:
                output_path = source_directory.parent / f'{source_directory.name or "Images"}.epub'
            else:
                output_path = source_path.with_suffix(f'.{args.format}')

        if (
            not image_folder
            and args.format == 'epub'
            and source_path.suffix.lower() == '.odt'
            and not args.no_timestamp
        ):
            timestamp = datetime.now().strftime('%H:%M_%d.%m.%Y')
            output_path = output_path.with_name(
                f'{output_path.stem}_({timestamp}){output_path.suffix}'
            )
        return os.path.abspath(output_path)

    planned_output_paths = {}
    source_paths = [
        os.path.abspath(str(source_path))
        for source_path in all_files
    ]
    source_paths.extend(
        os.path.abspath(str(image_path))
        for _, images in image_books
        for image_path in images
    )
    output_claims = []
    for source_path in all_files:
        source_path = os.path.abspath(str(source_path))
        if Path(source_path).suffix.lower() == f'.{args.format}':
            continue
        output_path = output_for_source(source_path)
        planned_output_paths[('file', source_path)] = output_path
        output_claims.append((source_path, output_path))
    for image_directory, _image_paths in image_books:
        source_path = os.path.abspath(str(image_directory))
        output_path = output_for_source(source_path, image_folder=True)
        planned_output_paths[('images', source_path)] = output_path
        output_claims.append((source_path, output_path))

    output_conflicts = []
    source_identities = {}
    for source_path in source_paths:
        for identity in path_identity_keys(source_path):
            source_identities.setdefault(identity, source_path)

    output_identities = {}
    for source_path, output_path in output_claims:
        identities = path_identity_keys(output_path)
        matching_source = next(
            (source_identities[identity] for identity in identities if identity in source_identities),
            None,
        )
        if matching_source is not None:
            output_conflicts.append(
                f'Результат {output_path} совпадает с исходным файлом {matching_source}'
            )
        conflicting_source = next(
            (output_identities[identity] for identity in identities if identity in output_identities),
            None,
        )
        if conflicting_source is not None:
            output_conflicts.append(
                f'Источники {conflicting_source} и {source_path} имеют общий путь результата '
                f'{output_path}'
            )
        else:
            for identity in identities:
                output_identities[identity] = source_path

    if output_conflicts:
        for conflict in output_conflicts:
            log_error(conflict)
        sys.exit(1)

    for _source_path, output_path in output_claims:
        output_suffix = Path(output_path).suffix.lower().lstrip('.')
        if output_suffix and output_suffix not in output_format_choices:
            parser.error(
                f'расширение выходного файла должно соответствовать одному '
                f'из форматов: {", ".join(output_format_choices)}'
            )
        if output_suffix and output_suffix != args.format:
            parser.error(
                f'расширение выходного файла .{output_suffix} не соответствует '
                f'формату {args.format}'
            )

    # Если только один файл и указан -o, используем его
    if total_inputs == 1 and args.output and all_files:
        result_info = {}
        try:
            success = process_single_file(
                all_files[0],
                explicit_cover=args.cover,
                explicit_output=planned_output_paths.get(
                    ('file', os.path.abspath(str(all_files[0])))
                ),
                explicit_title=args.title,
                explicit_author=args.author,
                explicit_lang=args.lang,
                media_dir=args.media_dir,
                media_recursive=not args.no_recursive,
                result_info=result_info
            )
        except Exception as e:
            log_error(f'Ошибка обработки: {e}')
            success = False
        print_file_result(all_files[0], result_info, success)
    elif total_inputs == 1 and args.output:
        image_directory, image_paths = image_books[0]
        result_info = {}
        try:
            success = process_image_folder(
                image_directory,
                image_paths,
                explicit_output=planned_output_paths[
                    ('images', os.path.abspath(str(image_directory)))
                ],
                result_info=result_info,
                media_dir=args.media_dir,
                media_recursive=not args.no_recursive,
            )
        except Exception as e:
            log_error(f'Ошибка обработки: {e}')
            success = False
        print_file_result(image_directory, result_info, success)
    else:
        # Обрабатываем каждый файл
        success_count = 0
        fail_count = 0
        skipped_count = 0

        for i, file_path in enumerate(all_files, 1):
            verbose_print(f'\n  {"─" * 50}')
            log(f'📄 [{i}/{total_inputs}] {Path(file_path).name}')
            verbose_print(f'  {"─" * 50}')
            result_info = {}

            try:
                success = process_single_file(
                    file_path,
                    explicit_cover=args.cover,
                    explicit_output=planned_output_paths.get(
                        ('file', os.path.abspath(str(file_path)))
                    ),
                    explicit_title=args.title,
                    explicit_author=args.author,
                    explicit_lang=args.lang,
                    media_dir=args.media_dir,
                    media_recursive=not args.no_recursive,
                    result_info=result_info
                )
                if success:
                    success_count += 1
                elif result_info.get('skipped'):
                    skipped_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                log_error(f'Ошибка обработки: {e}')
                fail_count += 1
                success = False

            print_file_result(file_path, result_info, success)

        for i, (image_directory, image_paths) in enumerate(image_books, len(all_files) + 1):
            verbose_print(f'\n  {"─" * 50}')
            log(f'🖼️  [{i}/{total_inputs}] {Path(image_directory).name or image_directory}')
            verbose_print(f'  {"─" * 50}')
            result_info = {}

            try:
                success = process_image_folder(
                    image_directory,
                    image_paths,
                    explicit_output=planned_output_paths[
                        ('images', os.path.abspath(str(image_directory)))
                    ],
                    result_info=result_info,
                    media_dir=args.media_dir,
                    media_recursive=not args.no_recursive,
                )
                if success:
                    success_count += 1
                elif result_info.get('skipped'):
                    skipped_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                log_error(f'Ошибка обработки: {e}')
                fail_count += 1
                success = False

            print_file_result(image_directory, result_info, success)

        # Итоговая статистика
        verbose_print(f'\n  {"═" * 50}')
        verbose_print(f'  📊 Итого: {total_inputs} источников')
        verbose_print(f'  ✅ Успешно: {success_count}')
        if skipped_count > 0:
            verbose_print(f'  ⏭️  Пропущено: {skipped_count}')
        if fail_count > 0:
            verbose_print(f'  ❌ Ошибок: {fail_count}')
        verbose_print(f'  {"═" * 50}\n')


if __name__ == '__main__':
    main()
