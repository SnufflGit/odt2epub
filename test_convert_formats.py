import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import convert


class PandocFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_dir.name)
        self.input_path = self.directory / 'Книга.odt'
        self.input_path.write_bytes(b'')
        self.which_patcher = patch(
            'convert.shutil.which',
            return_value='/usr/bin/fake',
        )
        self.which_patcher.start()
        self.addCleanup(self.which_patcher.stop)
        self.epub_bytes = convert.generate_epub(
            '<p>Тест</p>',
            '',
            {},
            {
                'title': 'Тест',
                'author': 'Автор',
                'language': 'ru',
                'date': '2024-01-01',
            },
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_startup_warns_when_external_utilities_are_missing(self):
        empty_directory = self.directory / 'empty'
        empty_directory.mkdir()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, 'argv', [
            'convert.py',
            str(empty_directory),
            '--mode',
            'verbose',
        ]):
            with patch('convert.shutil.which', return_value=None):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn('pandoc не найден в PATH', stderr.getvalue())
        self.assertIn('xelatex не найден в PATH', stderr.getvalue())
        self.assertIn('pdfinfo не найден в PATH', stderr.getvalue())
        self.assertIn('pdftotext не найден в PATH', stderr.getvalue())
        self.assertIn('pdftoppm не найден в PATH', stderr.getvalue())

    def test_missing_xelatex_is_reported_before_running_pandoc(self):
        with patch('convert.shutil.which', return_value=None):
            commands, _, stderr = self.run_conversion(
                'pdf',
                mode='quiet',
            )

        self.assertEqual(commands, [])
        self.assertIn('xelatex не найден', stderr)

    def test_russian_odt_content_and_filename_are_preserved_with_default_author(self):
        source_path = self.directory / 'Русская книга.odt'
        content_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text>
    <text:h text:outline-level="1">Заголовок книги</text:h>
    <text:p>Текст на русском: Ёжик, ёлка и «кириллица».</text:p>
  </office:text></office:body>
</office:document-content>'''
        meta_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">
  <office:meta/>
</office:document-meta>'''
        with zipfile.ZipFile(source_path, 'w') as archive:
            archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
            archive.writestr('content.xml', content_xml.encode('utf-8'))
            archive.writestr('meta.xml', meta_xml.encode('utf-8'))

        css_path = self.directory / 'Пользовательские стили.css'
        css_path.write_text('p { color: #123456; }', encoding='utf-8')
        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--media-dir',
            str(self.directory),
            '--css',
            str(css_path),
            '--toc',
            '--no-timestamp',
            '--mode',
            'quiet',
        ]):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                convert.main()

        output_path = self.directory / 'Русская книга.epub'
        self.assertTrue(output_path.is_file())
        with zipfile.ZipFile(output_path) as archive:
            self.assertIsNone(archive.testzip())
            opf_ns = {
                'opf': 'http://www.idpf.org/2007/opf',
                'dc': 'http://purl.org/dc/elements/1.1/',
            }
            package = ET.fromstring(archive.read('OEBPS/content.opf'))
            self.assertEqual(
                package.find('opf:metadata/dc:title', opf_ns).text,
                'Русская книга',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:creator', opf_ns).text,
                'Шмель',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:publisher', opf_ns).text,
                'Самиздат',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:rights', opf_ns).text,
                '© Шмель, 2026',
            )
            nav_id = next(
                item.get('id')
                for item in package.findall('opf:manifest/opf:item', opf_ns)
                if 'nav' in item.get('properties', '').split()
            )
            spine = package.find('opf:spine', opf_ns)
            self.assertIn(nav_id, [item.get('idref') for item in spine])
            self.assertIn(
                'color: #123456',
                archive.read('OEBPS/style.css').decode('utf-8'),
            )
            chapter = archive.read('OEBPS/chapter1.xhtml').decode('utf-8')
            self.assertIn('Заголовок книги', chapter)
            self.assertIn('Ёжик, ёлка и «кириллица»', chapter)
            nav = archive.read('OEBPS/nav.xhtml').decode('utf-8')
            self.assertIn('href="chapter1.xhtml"', nav)

    def test_add_audio_and_video_media_to_epub(self):
        epub_path = self.directory / 'Книга.epub'
        media_directory = self.directory / 'Медиа'
        media_directory.mkdir()
        audio_path = media_directory / 'Аудио.mp3'
        video_path = media_directory / 'Видео.mp4'
        audio_path.write_bytes(b'audio data')
        video_path.write_bytes(b'video data')
        container_xml = '''<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>'''
        package_xml = '''<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:language>ru</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="chapter"/></spine>
</package>'''
        nav_xml = '''<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><nav><ol><li><a href="chapter.xhtml">Глава</a></li></ol></nav></body>
</html>'''
        ncx_xml = '''<?xml version="1.0"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap><navPoint id="chapter" playOrder="1">
    <navLabel><text>Глава</text></navLabel><content src="chapter.xhtml"/>
  </navPoint></navMap>
</ncx>'''
        with zipfile.ZipFile(epub_path, 'w') as archive:
            archive.writestr(
                'mimetype',
                'application/epub+zip',
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr('META-INF/container.xml', container_xml)
            archive.writestr('OEBPS/content.opf', package_xml)
            archive.writestr('OEBPS/nav.xhtml', nav_xml)
            archive.writestr('OEBPS/toc.ncx', ncx_xml)
            archive.writestr('OEBPS/chapter.xhtml', '<html/>')

        added_count = convert.add_media_files_to_epub(
            epub_path,
            [audio_path, video_path],
            media_directory,
        )

        self.assertEqual(added_count, 2)
        with zipfile.ZipFile(epub_path) as archive:
            names = archive.namelist()
            self.assertEqual(names[0], 'mimetype')
            self.assertIsNone(archive.testzip())
            self.assertEqual(
                archive.read('OEBPS/media-assets/asset-0001.mp3'),
                b'audio data',
            )
            self.assertEqual(
                archive.read('OEBPS/media-assets/asset-0002.mp4'),
                b'video data',
            )
            package = ET.fromstring(archive.read('OEBPS/content.opf'))
            opf_ns = {'opf': 'http://www.idpf.org/2007/opf'}
            manifest_items = package.findall('opf:manifest/opf:item', opf_ns)
            media_items = [item for item in manifest_items if item.get('id', '').startswith('external-media-')]
            self.assertEqual(
                [item.get('media-type') for item in media_items[:2]],
                ['audio/mpeg', 'video/mp4'],
            )
            media_page = next(
                item.get('href')
                for item in manifest_items
                if item.get('media-type') == 'application/xhtml+xml'
                and item.get('href', '').startswith('media')
            )
            self.assertEqual(media_page, 'media.xhtml')
            spine = package.find('opf:spine', opf_ns)
            self.assertEqual(spine[-1].get('idref'), media_items[-1].get('id'))
            media_xhtml = ET.fromstring(archive.read(f'OEBPS/{media_page}'))
            xhtml_ns = {'x': 'http://www.w3.org/1999/xhtml'}
            self.assertEqual(
                len(media_xhtml.findall('.//x:audio', xhtml_ns)),
                1,
            )
            self.assertEqual(
                len(media_xhtml.findall('.//x:video', xhtml_ns)),
                1,
            )
            self.assertEqual(
                [element.tag.rsplit('}', 1)[-1] for element in media_xhtml.findall('.//x:audio', xhtml_ns)
                 + media_xhtml.findall('.//x:video', xhtml_ns)],
                ['audio', 'video'],
            )
            self.assertEqual(
                media_xhtml.find('.//x:audio/x:source', xhtml_ns).get('src'),
                'media-assets/asset-0001.mp3',
            )
            self.assertEqual(
                media_xhtml.find('.//x:video/x:source', xhtml_ns).get('src'),
                'media-assets/asset-0002.mp4',
            )
            self.assertIn('Аудио.mp3', ''.join(media_xhtml.itertext()))
            nav = ET.fromstring(archive.read('OEBPS/nav.xhtml'))
            self.assertIn('Медиафайлы', ''.join(nav.itertext()))
            ncx = ET.fromstring(archive.read('OEBPS/toc.ncx'))
            ncx_ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
            self.assertIn('Медиафайлы', ''.join(ncx.itertext()))
            self.assertEqual(len(ncx.findall('.//n:navPoint', ncx_ns)), 2)

    def test_odt_publisher_and_rights_are_preserved_from_source_metadata(self):
        source_directory = self.directory / 'Каталог.odt'
        source_directory.mkdir()
        source_path = source_directory / 'Метаданные.odt'
        content_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text><text:p>Текст</text:p></office:text></office:body>
</office:document-content>'''
        meta_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0">
  <office:meta>
    <meta:publisher>Издатель из ODT</meta:publisher>
    <meta:rights>Права из ODT</meta:rights>
  </office:meta>
</office:document-meta>'''
        with zipfile.ZipFile(source_path, 'w') as archive:
            archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
            archive.writestr('content.xml', content_xml.encode('utf-8'))
            archive.writestr('meta.xml', meta_xml.encode('utf-8'))

        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--no-timestamp',
            '--mode',
            'quiet',
        ]):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                convert.main()

        with zipfile.ZipFile(source_directory / 'Метаданные.epub') as archive:
            package = ET.fromstring(archive.read('OEBPS/content.opf'))
        opf_ns = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/',
        }
        self.assertEqual(
            package.find('opf:metadata/dc:publisher', opf_ns).text,
            'Издатель из ODT',
        )
        self.assertEqual(
            package.find('opf:metadata/dc:rights', opf_ns).text,
            'Права из ODT',
        )

    def test_explicit_publisher_and_rights_override_source_metadata(self):
        opf_ns = 'http://www.idpf.org/2007/opf'
        package = ET.fromstring('''<package xmlns="http://www.idpf.org/2007/opf"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata><dc:publisher>Источник</dc:publisher><dc:rights>Источник</dc:rights></metadata>
</package>''')

        convert._set_epub_metadata(
            package,
            opf_ns,
            identifier=None,
            layout_method=None,
            publisher='Переопределение',
            rights='Переопределение',
        )

        dc_ns = 'http://purl.org/dc/elements/1.1/'
        metadata = package.find(f'{{{opf_ns}}}metadata')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}publisher').text, 'Переопределение')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}rights').text, 'Переопределение')

    def test_epub_metadata_defaults_fill_missing_values_without_replacing_source(self):
        opf_ns = 'http://www.idpf.org/2007/opf'
        dc_ns = 'http://purl.org/dc/elements/1.1/'
        package = ET.fromstring('''<package xmlns="http://www.idpf.org/2007/opf"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata>
    <dc:title>Заголовок из Markdown</dc:title>
    <dc:creator>Автор из Markdown</dc:creator>
    <dc:language>en</dc:language>
    <dc:date>2025-01-02</dc:date>
  </metadata>
  <manifest/>
</package>''')

        convert._set_epub_metadata(
            package,
            opf_ns,
            identifier=None,
            layout_method=None,
            default_title='Имя файла',
            default_language='ru',
            default_date='2026-11-13',
        )

        metadata = package.find(f'{{{opf_ns}}}metadata')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}title').text, 'Заголовок из Markdown')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}creator').text, 'Автор из Markdown')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}language').text, 'en')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}date').text, '2025-01-02')
        self.assertEqual(metadata.find(f'{{{dc_ns}}}publisher').text, convert.DEFAULT_PUBLISHER)
        self.assertEqual(metadata.find(f'{{{dc_ns}}}rights').text, convert.DEFAULT_RIGHTS)

    def test_extract_pdf_metadata_prefers_xmp_and_reads_info_dictionary(self):
        info_output = '''Title: Info title
Author: Info author
Metadata Stream: yes
'''
        xmp_output = '''<?xml version="1.0"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <rdf:RDF><rdf:Description>
    <dc:title><rdf:Alt><rdf:li xml:lang="x-default">XMP title</rdf:li></rdf:Alt></dc:title>
    <dc:creator><rdf:Seq><rdf:li>XMP author</rdf:li></rdf:Seq></dc:creator>
    <dc:publisher><rdf:Bag><rdf:li>XMP publisher</rdf:li></rdf:Bag></dc:publisher>
    <dc:rights><rdf:Alt><rdf:li>Rights</rdf:li></rdf:Alt></dc:rights>
    <dc:language><rdf:Bag><rdf:li>ru</rdf:li></rdf:Bag></dc:language>
  </rdf:Description></rdf:RDF>
</x:xmpmeta>'''
        with patch('convert.subprocess.run') as run:
            run.side_effect = [
                type('Result', (), {'stdout': info_output})(),
                type('Result', (), {'stdout': xmp_output})(),
            ]
            metadata = convert.extract_pdf_metadata('book.pdf')

        self.assertEqual(metadata, {
            'title': 'XMP title',
            'author': 'XMP author',
            'publisher': 'XMP publisher',
            'rights': 'Rights',
            'language': 'ru',
        })

    def test_pandoc_epub_route_embeds_media_from_media_directory(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Книга\n', encoding='utf-8')
        media_directory = self.directory / 'media'
        media_directory.mkdir()
        audio_path = media_directory / 'book.mp3'
        audio_path.write_bytes(b'audio data')
        css_path = self.directory / 'custom.css'
        css_path.write_text('body { color: navy; }', encoding='utf-8')
        epub_bytes = convert.generate_epub(
            '<p>Текст</p>',
            '',
            {},
            {
                'title': 'Книга',
                'author': 'Автор',
                'language': 'ru',
                'date': '2024-01-01',
            },
        )

        commands = []

        def fake_pandoc(command, **_kwargs):
            commands.append(command)
            output_path = Path(command[command.index('--output') + 1])
            output_path.write_bytes(epub_bytes)

        _, _, stderr = self.run_conversion(
            'epub',
            input_path=markdown_path,
            run_side_effect=fake_pandoc,
            extra_args=(
                '--media-dir',
                str(media_directory),
                '--css',
                str(css_path),
                '--toc',
            ),
        )

        self.assertEqual(stderr, '')
        self.assertIn('--toc', commands[0])
        self.assertIn('--css', commands[0])
        self.assertIn(str(css_path), commands[0])
        with zipfile.ZipFile(self.directory / 'Книга.epub') as archive:
            self.assertEqual(
                archive.read('OEBPS/media-assets/asset-0001.mp3'),
                b'audio data',
            )
            package = ET.fromstring(archive.read('OEBPS/content.opf'))
            opf_ns = {
                'opf': 'http://www.idpf.org/2007/opf',
                'dc': 'http://purl.org/dc/elements/1.1/',
            }
            self.assertEqual(
                package.find('opf:metadata/dc:creator', opf_ns).text,
                'Автор',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:publisher', opf_ns).text,
                'Самиздат',
            )
            layout = package.find(
                'opf:metadata/opf:meta[@property="rendition:layout"]',
                opf_ns,
            )
            self.assertEqual(layout.text, 'reflowable')

    def test_epub_identifier_and_layout_method_can_be_configured(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Книга\n', encoding='utf-8')
        identifier = 'urn:isbn:9781234567890'
        epub_bytes = convert.generate_epub(
            '<p>Текст</p>',
            '',
            {},
            {
                'title': 'Книга',
                'author': '',
                'language': 'ru',
                'date': '2024-01-01',
            },
        )

        def fake_pandoc(command, **_kwargs):
            output_path = Path(command[command.index('--output') + 1])
            output_path.write_bytes(epub_bytes)

        self.run_conversion(
            'epub',
            input_path=markdown_path,
            run_side_effect=fake_pandoc,
            extra_args=(
                '--identifier',
                identifier,
                '--layout-method',
                'pre-paginated',
                '--publisher',
                'Издательство «Ё»',
                '--rights',
                '© Издательство «Ё», 2025',
            ),
        )

        with zipfile.ZipFile(self.directory / 'Книга.epub') as archive:
            package = ET.fromstring(archive.read('OEBPS/content.opf'))
            opf_ns = {
                'opf': 'http://www.idpf.org/2007/opf',
                'dc': 'http://purl.org/dc/elements/1.1/',
            }
            identifier_element = package.find('opf:metadata/dc:identifier', opf_ns)
            self.assertEqual(identifier_element.text, identifier)
            self.assertEqual(
                package.find('opf:metadata/dc:creator', opf_ns).text,
                'Шмель',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:publisher', opf_ns).text,
                'Издательство «Ё»',
            )
            self.assertEqual(
                package.find('opf:metadata/dc:rights', opf_ns).text,
                '© Издательство «Ё», 2025',
            )
            self.assertEqual(
                package.get('unique-identifier'),
                identifier_element.get('id'),
            )
            layout = package.find(
                'opf:metadata/opf:meta[@property="rendition:layout"]',
                opf_ns,
            )
            self.assertEqual(layout.text, 'pre-paginated')
            self.assertIn('rendition:', package.get('prefix'))

            ncx = ET.fromstring(archive.read('OEBPS/toc.ncx'))
            ncx_ns = {'n': 'http://www.daisy.org/z3986/2005/ncx/'}
            uid_meta = ncx.find('n:head/n:meta[@name="dtb:uid"]', ncx_ns)
            self.assertEqual(uid_meta.get('content'), identifier)

    def run_conversion(self, output_format, explicit_output=None, run_side_effect=None, mode=None,
                       overwrite_options=(), input_path=None, extra_args=(),
                       no_timestamp=True):
        commands = []
        input_path = input_path or self.input_path

        def fake_run(command, **_kwargs):
            commands.append(command)
            output_path = Path(command[command.index('--output') + 1])
            output_path.write_bytes(
                self.epub_bytes if output_path.suffix == '.epub' else b'converted'
            )

        args = [str(input_path), '--format', output_format]
        if explicit_output:
            args.extend(['--output', str(explicit_output)])
        if mode:
            args.extend(['--mode', mode])
        if no_timestamp:
            args.append('--no-timestamp')
        args.extend(overwrite_options)
        args.extend(extra_args)

        stdout = io.StringIO()
        stderr = io.StringIO()
        side_effect = run_side_effect or fake_run
        with patch.object(sys, 'argv', ['convert.py', *args]):
            with patch('convert.subprocess.run', side_effect=side_effect):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    convert.main()
        return commands, stdout.getvalue(), stderr.getvalue()

    def test_formats_use_expected_pandoc_writer_and_extension(self):
        for output_format, pandoc_writer in (
            ('md', 'markdown'),
            ('pdf', 'pdf'),
            ('txt', 'plain'),
        ):
            with self.subTest(output_format=output_format):
                commands, _, stderr = self.run_conversion(output_format)
                self.assertEqual(stderr, '')
                self.assertEqual(len(commands), 1)
                command = commands[0]
                self.assertIn(f'--to={pandoc_writer}', command)
                if output_format == 'pdf':
                    self.assertIn('--pdf-engine=xelatex', command)
                    self.assertIn('mainfont=DejaVu Serif', command)
                output_path = Path(command[command.index('--output') + 1])
                self.assertEqual(output_path, self.directory / f'Книга.{output_format}')
                self.assertTrue(output_path.is_file())

    def test_all_document_sources_use_timestamp_suffix_by_default(self):
        source_paths = {
            'Документ.odt': self.directory / 'Документ.odt',
            'Текст.md': self.directory / 'Текст.md',
            'Заметка.txt': self.directory / 'Заметка.txt',
            'Скан.pdf': self.directory / 'Скан.pdf',
        }
        shutil.copyfile(
            Path(__file__).with_name('test_Draft.odt'),
            source_paths['Документ.odt'],
        )
        source_paths['Текст.md'].write_text('# Текст\n', encoding='utf-8')
        source_paths['Заметка.txt'].write_text('Заметка\n', encoding='utf-8')
        source_paths['Скан.pdf'].write_bytes(b'%PDF')

        def fake_run(command, **_kwargs):
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Metadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                Path(command[-1]).write_text('Текст из PDF\n', encoding='utf-8')
                return
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            *(str(path) for path in source_paths.values()),
            '--format',
            'epub',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        for source_name in source_paths:
            output_path = next(self.directory.glob(f'{Path(source_name).stem}_(*).epub'))
            self.assertRegex(
                output_path.name,
                rf'^{re.escape(Path(source_name).stem)}_'
                r'\(\d{2}:\d{2}_\d{2}\.\d{2}\.\d{4}\)\.epub$',
            )

    def test_output_extension_selects_format_when_format_is_omitted(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Заголовок\n\nТекст книги.\n', encoding='utf-8')
        output_path = self.directory / '1.pdf'
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(b'converted')

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--cover',
            'Обложка.jpg',
            '--output',
            str(output_path),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertIn('--to=pdf', commands[0])
        self.assertNotIn('--epub-cover-image=Обложка.jpg', commands[0])
        self.assertEqual(
            Path(commands[0][commands[0].index('--output') + 1]),
            output_path,
        )
        self.assertTrue(output_path.is_file())

    def test_markdown_pdf_rewrites_horizontal_rule_before_wikilink_in_temp_file(self):
        markdown_path = self.directory / 'Книга.md'
        original_markdown = (
            '# Заголовок\n\n---\n![[Библия/Обложка.jpg]]\n\n'
            '---\n## Следующий раздел\n'
        ).encode('utf-8')
        markdown_path.write_bytes(original_markdown)
        output_path = self.directory / 'Книга.pdf'
        commands = []
        temporary_inputs = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            input_path = Path(command[command.index('--output') - 1])
            temporary_inputs.append(input_path)
            self.assertNotEqual(input_path, markdown_path)
            self.assertEqual(
                input_path.read_bytes(),
                original_markdown.replace(b'\n---\n', b'\n***\n'),
            )
            self.assertIn(
                f'--resource-path={self.directory}{os.pathsep}{Path.cwd()}',
                command,
            )
            Path(command[command.index('--output') + 1]).write_bytes(b'converted')

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'pdf',
            '--output',
            str(output_path),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertEqual(markdown_path.read_bytes(), original_markdown)
        self.assertFalse(temporary_inputs[0].exists())
        self.assertTrue(output_path.is_file())

    def test_markdown_epub_rewrites_horizontal_rule_before_wikilink_in_temp_file(self):
        markdown_path = self.directory / 'Книга.md'
        original_markdown = (
            '# Заголовок\n\n---\n![[Библия/Обложка.jpg]]\n\n'
            '---\n## Следующий раздел\n'
        ).encode('utf-8')
        markdown_path.write_bytes(original_markdown)
        output_path = self.directory / 'Книга.epub'
        commands = []
        temporary_inputs = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            input_path = Path(command[command.index('--output') - 1])
            temporary_inputs.append(input_path)
            self.assertNotEqual(input_path, markdown_path)
            self.assertEqual(
                input_path.read_bytes(),
                original_markdown.replace(b'\n---\n', b'\n***\n'),
            )
            self.assertIn(
                f'--resource-path={self.directory}{os.pathsep}{Path.cwd()}',
                command,
            )
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'epub',
            '--output',
            str(output_path),
            '--media-dir',
            str(self.directory),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertIn('--to=epub', commands[0])
        self.assertEqual(markdown_path.read_bytes(), original_markdown)
        self.assertFalse(temporary_inputs[0].exists())
        self.assertTrue(output_path.is_file())

    def test_markdown_pdf_rewrite_preserves_front_matter_and_fenced_code(self):
        markdown_bytes = (
            b'---\ntitle: Book\n---\n'
            b'# Title\n\n---\n![[cover.jpg]]\n\n---\n## Section\n\n'
            b'```markdown\n---\n![[example.jpg]]\n```\n'
        )

        prepared_markdown, replaced_rules = convert._prepare_markdown_source(
            markdown_bytes,
        )

        self.assertEqual(replaced_rules, 2)
        self.assertTrue(prepared_markdown.startswith(b'---\ntitle: Book\n---\n'))
        self.assertIn(b'\n***\n![[cover.jpg]]', prepared_markdown)
        self.assertIn(b'```markdown\n---\n![[example.jpg]]\n```', prepared_markdown)

        markdown_without_wikilinks = b'# Title\n\n---\n## Section\n'
        prepared_markdown, replaced_rules = convert._prepare_markdown_source(
            markdown_without_wikilinks,
        )
        self.assertEqual(replaced_rules, 1)
        self.assertEqual(
            prepared_markdown,
            b'# Title\n\n***\n## Section\n',
        )

        fenced_only = b'```markdown\n---\n![[example.jpg]]\n```\n'
        unchanged_markdown, replaced_rules = convert._prepare_markdown_source(fenced_only)
        self.assertEqual(unchanged_markdown, fenced_only)
        self.assertEqual(replaced_rules, 0)

    def test_txt_epub_rewrites_horizontal_rule_before_obsidian_embed_in_temp_file(self):
        text_path = self.directory / 'Книга.txt'
        original_text = (
            '# Заголовок\n\n---\n![[Вставки/Изображение.png]]\n\nТекст.\n'
        ).encode('utf-8')
        text_path.write_bytes(original_text)
        output_path = self.directory / 'Книга.epub'
        temporary_inputs = []
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            input_path = Path(command[command.index('--output') - 1])
            temporary_inputs.append(input_path)
            self.assertNotEqual(input_path, text_path)
            self.assertEqual(
                input_path.read_bytes(),
                original_text.replace(b'\n---\n', b'\n***\n'),
            )
            self.assertIn(
                f'--resource-path={self.directory}{os.pathsep}{Path.cwd()}',
                command,
            )
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(text_path),
            '--format',
            'epub',
            '--output',
            str(output_path),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertEqual(text_path.read_bytes(), original_text)
        self.assertFalse(temporary_inputs[0].exists())
        self.assertTrue(output_path.is_file())

    def test_markdown_pdf_temp_input_is_removed_when_pandoc_fails(self):
        markdown_path = self.directory / 'Книга.md'
        original_markdown = b'# Title\n\n---\n![[cover.jpg]]\n'
        markdown_path.write_bytes(original_markdown)
        output_path = self.directory / 'Книга.pdf'
        temporary_inputs = []

        def failing_run(command, **_kwargs):
            input_path = Path(command[command.index('--output') - 1])
            temporary_inputs.append(input_path)
            self.assertTrue(input_path.is_file())
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr='simulated Pandoc failure',
            )

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'pdf',
            '--output',
            str(output_path),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=failing_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                    convert.main()

        self.assertEqual(markdown_path.read_bytes(), original_markdown)
        self.assertFalse(temporary_inputs[0].exists())
        self.assertIn('simulated Pandoc failure', stderr.getvalue())

    def test_output_rejects_unsupported_extension_before_overwriting_cover(self):
        source_path = self.directory / 'Книга.odt'
        source_path.write_bytes(b'')
        cover_path = self.directory / 'Обложка.jpg'
        original_cover = b'original cover'
        cover_path.write_bytes(original_cover)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--cover',
            str(cover_path),
            '--output',
            str(cover_path),
            '--force',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=AssertionError('should not convert')):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn('расширение выходного файла', stderr.getvalue())
        self.assertEqual(cover_path.read_bytes(), original_cover)

    def test_output_rejects_extension_that_conflicts_with_explicit_format(self):
        with patch.object(sys, 'argv', [
            'convert.py',
            str(self.input_path),
            '--format',
            'epub',
            '--output',
            str(self.directory / 'Книга.pdf'),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=AssertionError('should not convert')):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn('не соответствует формату epub', stderr.getvalue())

    def test_dotted_output_directory_is_not_treated_as_an_extension(self):
        output_directory = self.directory / 'converted.pdf'
        output_directory.mkdir()
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Заголовок\n', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--output',
            output_directory.name,
            '--no-timestamp',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertIn('--to=epub', commands[0])
        self.assertTrue((output_directory / 'Книга.epub').is_file())

    def test_markdown_and_text_inputs_can_be_converted_to_epub_and_pdf(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Заголовок\n\nТекст книги.\n', encoding='utf-8')
        text_path = self.directory / 'Текст книги.txt'
        text_path.write_text('Заголовок\n\nТекст книги.\n', encoding='utf-8')

        for input_path in (markdown_path, text_path):
            for output_format, pandoc_writer in (('epub', 'epub'), ('pdf', 'pdf')):
                with self.subTest(input_path=input_path.name, output_format=output_format):
                    commands, _, stderr = self.run_conversion(
                        output_format,
                        input_path=input_path,
                    )
                    self.assertEqual(stderr, '')
                    self.assertEqual(len(commands), 1)
                    self.assertIn('--from=markdown', commands[0])
                    self.assertIn(f'--to={pandoc_writer}', commands[0])
                    self.assertEqual(
                        commands[0][commands[0].index('--output') - 1],
                        str(input_path),
                    )
                    output_path = Path(commands[0][commands[0].index('--output') + 1])
                    self.assertEqual(output_path, self.directory / f'{input_path.stem}.{output_format}')
                    self.assertTrue(output_path.is_file())

    def test_txt_epub_uses_same_name_cover_from_media_subdirectory(self):
        text_path = self.directory / 'Книга.txt'
        text_path.write_text('Текст книги.\n', encoding='utf-8')
        cover_path = self.directory / 'media' / 'Книга.png'
        cover_path.parent.mkdir()
        cover_path.write_bytes(b'cover image')

        commands, _, stderr = self.run_conversion('epub', input_path=text_path)

        self.assertEqual(stderr, '')
        self.assertEqual(len(commands), 1)
        self.assertIn(f'--epub-cover-image={cover_path}', commands[0])

    def test_cover_search_prioritizes_source_then_media_then_other_subfolders(self):
        source_directory = self.directory / 'source'
        source_directory.mkdir()
        input_path = source_directory / 'Книга.md'
        input_path.write_text('# Книга\n', encoding='utf-8')
        media_cover = source_directory / 'media' / 'Книга.jpg'
        media_cover.parent.mkdir()
        media_cover.write_bytes(b'media cover')
        other_cover = source_directory / 'other' / 'Книга.png'
        other_cover.parent.mkdir()
        other_cover.write_bytes(b'other cover')
        source_cover = source_directory / 'Книга.gif'
        source_cover.write_bytes(b'source cover')

        self.assertEqual(
            convert._find_cover_file(input_path),
            source_cover,
        )
        source_cover.unlink()
        self.assertEqual(
            convert._find_cover_file(input_path),
            media_cover,
        )
        media_cover.unlink()
        self.assertEqual(
            convert._find_cover_file(input_path),
            other_cover,
        )

    def test_explicit_media_directory_is_searched_before_other_subfolders(self):
        source_directory = self.directory / 'source'
        source_directory.mkdir()
        input_path = source_directory / 'Книга.txt'
        input_path.write_text('Текст.\n', encoding='utf-8')
        media_directory = self.directory / 'custom-media'
        media_directory.mkdir()
        media_cover = media_directory / 'Книга.webp'
        media_cover.write_bytes(b'media cover')
        other_cover = source_directory / 'other' / 'Книга.jpg'
        other_cover.parent.mkdir()
        other_cover.write_bytes(b'other cover')

        self.assertEqual(
            convert._find_cover_file(
                input_path,
                media_dir=media_directory,
                media_dir_explicit=True,
            ),
            media_cover,
        )

    def test_odt_epub_uses_cover_from_media_subdirectory(self):
        odt_path = self.directory / 'Книга.odt'
        content_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
  xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
  <office:body><office:text><text:p>Текст книги.</text:p></office:text></office:body>
</office:document-content>'''
        meta_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
  xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">
  <office:meta/>
</office:document-meta>'''
        with zipfile.ZipFile(odt_path, 'w') as archive:
            archive.writestr('mimetype', 'application/vnd.oasis.opendocument.text')
            archive.writestr('content.xml', content_xml.encode('utf-8'))
            archive.writestr('meta.xml', meta_xml.encode('utf-8'))
        cover_path = self.directory / 'media' / 'Книга.jpg'
        cover_path.parent.mkdir()
        cover_path.write_bytes(b'cover image')

        with patch.object(sys, 'argv', [
            'convert.py',
            str(odt_path),
            '--no-timestamp',
            '--mode',
            'quiet',
        ]):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                convert.main()

        with zipfile.ZipFile(self.directory / 'Книга.epub') as archive:
            self.assertEqual(archive.read('OEBPS/images/cover.jpg'), b'cover image')

    def test_markdown_epub_automatically_uses_same_name_cover(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Книга\n\nТекст.\n', encoding='utf-8')
        cover_path = self.directory / 'Книга.png'
        cover_path.write_bytes(b'cover image')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'epub',
            '--media-dir',
            str(self.directory),
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertIn(f'--epub-cover-image={cover_path}', commands[0])

    def test_markdown_epub_recursively_finds_same_name_cover(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Книга\n\nТекст.\n', encoding='utf-8')
        cover_path = self.directory / 'Обложки' / 'Книга.jpg'
        cover_path.parent.mkdir()
        cover_path.write_bytes(b'cover image')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'epub',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertIn(f'--epub-cover-image={cover_path}', commands[0])

    def test_markdown_epub_no_recursive_skips_cover_in_subdirectory(self):
        markdown_path = self.directory / 'Книга.md'
        markdown_path.write_text('# Книга\n\nТекст.\n', encoding='utf-8')
        cover_path = self.directory / 'Обложки' / 'Книга.jpg'
        cover_path.parent.mkdir()
        cover_path.write_bytes(b'cover image')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            '--format',
            'epub',
            '--no-recursive',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertFalse(any(arg.startswith('--epub-cover-image=') for arg in commands[0]))

    def test_image_folder_creates_epub_in_natural_image_order(self):
        image_directory = self.directory / 'Фотоальбом'
        image_directory.mkdir()
        (image_directory / 'page10.png').write_bytes(b'image 10')
        (image_directory / 'page2.png').write_bytes(b'image 2')
        output_path = self.directory / 'Результат.epub'
        css_path = self.directory / 'image-styles.css'
        css_path.write_text('img { max-width: 90%; }', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            markdown_path = Path(command[command.index('--output') - 1])
            markdown = markdown_path.read_text(encoding='utf-8')
            self.assertLess(markdown.index('page2.png'), markdown.index('page10.png'))
            self.assertIn('## Страница 1', markdown)
            self.assertIn(f'--resource-path={image_directory}', command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, 'argv', [
            'convert.py',
            str(image_directory),
            '--format',
            'epub',
            '--output',
            str(output_path),
            '--mode',
            'summary',
            '--input-formats',
            'images',
            '--css',
            str(css_path),
            '--toc',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    convert.main()

        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'pandoc')
        self.assertIn('--to=epub', commands[0])
        self.assertIn('--toc', commands[0])
        self.assertIn(str(css_path), commands[0])
        self.assertTrue(output_path.is_file())
        self.assertEqual(stdout.getvalue().strip(), 'Фотоальбом — Результат.epub — УСПЕШНО')

    def test_input_formats_filter_directory_scan(self):
        input_directory = self.directory / 'Books'
        input_directory.mkdir()
        markdown_path = input_directory / 'Book.md'
        text_path = input_directory / 'Plain.txt'
        (input_directory / 'Book.odt').write_bytes(b'')
        (input_directory / 'Book.pdf').write_bytes(b'')
        (input_directory / 'image.png').write_bytes(b'image')
        markdown_path.write_text('# Markdown\n', encoding='utf-8')
        text_path.write_text('Plain text\n', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(input_directory),
            '--format',
            'epub',
            '--input-formats',
            'md,txt',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        converted_inputs = {Path(command[-3]) for command in commands}
        self.assertEqual(converted_inputs, {markdown_path, text_path}, commands)

    def test_input_formats_do_not_filter_explicit_file_arguments(self):
        text_path = self.directory / 'Explicit.txt'
        text_path.write_text('Text\n', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(text_path),
            '--format',
            'epub',
            '--input-formats',
            'odt',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertEqual(Path(commands[0][-3]), text_path)

    def test_matching_source_and_output_formats_are_skipped_unchanged(self):
        for extension, output_format in (('md', 'md'), ('txt', 'txt'), ('pdf', 'pdf')):
            with self.subTest(extension=extension):
                source_path = self.directory / f'Already.{extension}'
                original_content = b'original source'
                source_path.write_bytes(original_content)
                commands, stdout, stderr = self.run_conversion(
                    output_format,
                    mode='summary',
                    input_path=source_path,
                )
                self.assertEqual(commands, [])
                self.assertEqual(stderr, '')
                self.assertEqual(source_path.read_bytes(), original_content)
                self.assertEqual(
                    stdout.strip(),
                    f'Already.{extension} — Already.{extension} — ПРОПУЩЕНО',
                )

    def test_output_path_must_not_overwrite_source_even_with_force(self):
        source_path = self.directory / 'Источник.odt'
        original_content = 'исходное содержимое'.encode('utf-8')
        source_path.write_bytes(original_content)
        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--format',
            'md',
            '--output',
            str(source_path),
            '--force',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=AssertionError('should not convert')):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn('совпадает с исходным файлом', stderr.getvalue())
        self.assertEqual(source_path.read_bytes(), original_content)

    def test_batch_with_duplicate_output_paths_fails_before_conversion(self):
        source_directory = self.directory / 'sources'
        source_directory.mkdir()
        (source_directory / 'Книга.md').write_text('# Markdown\n', encoding='utf-8')
        (source_directory / 'Книга.txt').write_text('Текст\n', encoding='utf-8')
        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_directory),
            '--format',
            'epub',
            '--input-formats',
            'md,txt',
            '--mode',
            'quiet',
        ]):
            with patch('convert.subprocess.run', side_effect=AssertionError('should not convert')):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 1)
        self.assertIn('общий путь результата', stderr.getvalue())
        self.assertFalse((source_directory / 'Книга.epub').exists())

    def test_non_epub_options_are_ignored_for_other_output_formats(self):
        source_path = self.directory / 'Книга.odt'
        source_path.write_bytes(b'')
        commands, _, stderr = self.run_conversion(
            'md',
            input_path=source_path,
            extra_args=('--toc', '--css', str(self.directory / 'missing.css')),
        )

        self.assertEqual(len(commands), 1)
        self.assertIn('будут пропущены', stderr)
        self.assertTrue((self.directory / 'Книга.md').is_file())

    def test_explicit_missing_media_directory_is_rejected(self):
        source_path = self.directory / 'Книга.md'
        source_path.write_text('# Книга\n', encoding='utf-8')
        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--format',
            'epub',
            '--media-dir',
            str(self.directory / 'несуществующая'),
        ]):
            with redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as exit_context:
                    convert.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn('папка медиа не найдена', stderr.getvalue())
        self.assertFalse((self.directory / 'Книга.epub').exists())

    def test_matching_source_and_output_formats_are_skipped_in_directory_scan(self):
        input_directory = self.directory / 'PDFs'
        input_directory.mkdir()
        pdf_path = input_directory / 'Already.pdf'
        pdf_path.write_bytes(b'original pdf')

        with patch.object(sys, 'argv', [
            'convert.py',
            str(input_directory),
            '--format',
            'pdf',
            '--input-formats',
            'pdf',
            '--mode',
            'summary',
        ]):
            with patch('convert.subprocess.run', side_effect=AssertionError('should not convert')):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    convert.main()

        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(stdout.getvalue().strip(), 'Already.pdf — Already.pdf — ПРОПУЩЕНО')
        self.assertEqual(pdf_path.read_bytes(), b'original pdf')

    def test_input_formats_reject_incompatible_source_output_pair(self):
        stderr = io.StringIO()
        with patch.object(sys, 'argv', [
            'convert.py', '--format', 'pdf', '--input-formats', 'images',
        ]):
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as exit_context:
                    convert.main()

        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn('нельзя преобразовать в pdf', stderr.getvalue())

    def test_image_folder_respects_no_recursive(self):
        image_directory = self.directory / 'Pictures'
        nested_directory = image_directory / 'nested'
        nested_directory.mkdir(parents=True)
        (image_directory / 'front.png').write_bytes(b'front')
        (nested_directory / 'page2.png').write_bytes(b'nested')

        def fake_run(command, **_kwargs):
            markdown_path = Path(command[command.index('--output') - 1])
            markdown = markdown_path.read_text(encoding='utf-8')
            self.assertIn('front.png', markdown)
            self.assertNotIn('nested/page2.png', markdown)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py', str(image_directory), '--format', 'epub', '--no-recursive',
            '--no-timestamp',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertTrue((self.directory / 'Pictures.epub').is_file())

    def test_mixed_document_and_image_folder_only_converts_document(self):
        input_directory = self.directory / 'Mixed'
        input_directory.mkdir()
        markdown_path = input_directory / 'Book.md'
        markdown_path.write_text('# Book\n', encoding='utf-8')
        (input_directory / 'cover.png').write_bytes(b'image')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', ['convert.py', str(input_directory), '--format', 'epub']):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 1)
        self.assertEqual(Path(commands[0][-3]), markdown_path)

    def test_pdf_input_is_extracted_and_converted_to_epub(self):
        pdf_path = self.directory / 'PDF книга.pdf'
        pdf_path.write_bytes(b'%PDF')
        extracted_pdf_text = (
            '# Заголовок\n\n---\n![[Вставки/Изображение.png]]\n\nТекст книги.\n'
        )
        normalized_pdf_text = extracted_pdf_text.replace('\n---\n', '\n***\n')
        cover_path = self.directory / 'Обложки' / 'PDF книга.jpg'
        cover_path.parent.mkdir()
        cover_path.write_bytes(b'cover image')
        commands = []
        temporary_inputs = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Title: Source PDF title\nAuthor: PDF author\nMetadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                self.assertEqual(command[1:4], ['-enc', 'UTF-8', '-nopgbrk'])
                Path(command[-1]).write_text(
                    extracted_pdf_text,
                    encoding='utf-8',
                )
                return

            pandoc_input = Path(command[command.index('--output') - 1])
            temporary_inputs.append(pandoc_input)
            self.assertTrue(pandoc_input.is_file())
            self.assertEqual(
                pandoc_input.read_text(encoding='utf-8'),
                normalized_pdf_text,
            )
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, 'argv', [
            'convert.py', str(pdf_path), '--format', 'epub', '--title', 'Моя книга',
            '--no-timestamp',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    convert.main()

        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(len(commands), 3)
        pandoc_command = commands[2]
        self.assertEqual(pandoc_command[0], 'pandoc')
        self.assertIn('--from=markdown', pandoc_command)
        self.assertIn('--to=epub', pandoc_command)
        self.assertIn(f'--epub-cover-image={cover_path}', pandoc_command)
        self.assertIn('title=Моя книга', pandoc_command)
        self.assertIn('author=PDF author', pandoc_command)
        self.assertIn(f'publisher={convert.DEFAULT_PUBLISHER}', pandoc_command)
        self.assertIn(f'rights={convert.DEFAULT_RIGHTS}', pandoc_command)
        self.assertIn('lang=ru', pandoc_command)
        self.assertEqual(
            Path(pandoc_command[pandoc_command.index('--output') + 1]),
            self.directory / 'PDF книга.epub',
        )
        self.assertTrue((self.directory / 'PDF книга.epub').is_file())
        self.assertFalse(temporary_inputs[0].exists())
        self.assertIn('Извлечение текста из PDF', stdout.getvalue())

    def test_pdf_directory_scan_includes_pdf_for_epub_output(self):
        books = self.directory / 'books'
        books.mkdir()
        pdf_path = books / 'Книга.pdf'
        pdf_path.write_bytes(b'%PDF')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Metadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                Path(command[-1]).write_text('Текст книги.\n', encoding='utf-8')
            else:
                Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py', str(books), '--format', 'epub', '--no-timestamp',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 3)
        self.assertEqual(commands[0][0], 'pdfinfo')
        self.assertEqual(commands[1][0], 'pdftotext')
        self.assertEqual(Path(commands[1][-2]), pdf_path)
        self.assertEqual(Path(commands[2][commands[2].index('--output') + 1]), books / 'Книга.epub')

    def test_missing_pdftotext_is_reported(self):
        pdf_path = self.directory / 'Книга.pdf'
        pdf_path.write_bytes(b'%PDF')

        def missing_pdftotext(_command, **_kwargs):
            raise FileNotFoundError

        _, _, stderr = self.run_conversion(
            'epub',
            input_path=pdf_path,
            run_side_effect=missing_pdftotext,
        )
        self.assertIn('pdftotext не найден', stderr)

    def test_scanned_pdf_pages_are_added_to_epub_as_images(self):
        pdf_path = self.directory / 'Скан.pdf'
        pdf_path.write_bytes(b'%PDF')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Metadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                Path(command[-1]).write_bytes(b'\n \n')
            elif command[0] == 'pdftoppm':
                self.assertIn('-png', command)
                self.assertIn('150', command)
                prefix = command[-1]
                Path(f'{prefix}-2.png').write_bytes(b'page 2')
                Path(f'{prefix}-1.png').write_bytes(b'page 1')
            else:
                markdown_path = Path(command[command.index('--output') - 1])
                markdown = markdown_path.read_text(encoding='utf-8')
                self.assertLess(markdown.index('(page-1.png)'), markdown.index('(page-2.png)'))
                self.assertTrue(any(argument.startswith('--resource-path=') for argument in command))
                Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        _, stdout, stderr = self.run_conversion(
            'epub',
            input_path=pdf_path,
            run_side_effect=fake_run,
        )
        self.assertEqual(stderr, '')
        self.assertEqual(
            [command[0] for command in commands],
            ['pdfinfo', 'pdftotext', 'pdftoppm', 'pandoc'],
        )
        self.assertIn('страницы PDF будут добавлены', stdout)
        self.assertTrue((self.directory / 'Скан.epub').is_file())

    def test_missing_pdftoppm_is_reported_for_scanned_pdf(self):
        pdf_path = self.directory / 'Скан.pdf'
        pdf_path.write_bytes(b'%PDF')

        def missing_pdftoppm(command, **_kwargs):
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Metadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                Path(command[-1]).write_bytes(b'')
                return
            raise FileNotFoundError

        _, _, stderr = self.run_conversion(
            'epub',
            input_path=pdf_path,
            run_side_effect=missing_pdftoppm,
        )
        self.assertIn('pdftoppm не найден', stderr)

    def test_pdf_directory_scan_includes_markdown_text_and_odt(self):
        books = self.directory / 'books'
        books.mkdir()
        markdown_path = books / 'Markdown.md'
        text_path = books / 'Text.txt'
        odt_path = books / 'Document.odt'
        markdown_path.write_text('# Markdown\n', encoding='utf-8')
        text_path.write_text('Plain text\n', encoding='utf-8')
        odt_path.write_bytes(b'')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(b'converted')

        args = ['convert.py', str(books), '--format', 'pdf']
        with patch.object(sys, 'argv', args):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        inputs = {Path(command[-3]) for command in commands}
        self.assertEqual(inputs, {markdown_path, text_path, odt_path})
        command_by_input = {Path(command[-3]): command for command in commands}
        self.assertIn('--from=markdown', command_by_input[markdown_path])
        self.assertIn('--from=markdown', command_by_input[text_path])
        self.assertIn('--from=odt', command_by_input[odt_path])

    def test_custom_output_path_is_passed_to_pandoc(self):
        output_path = self.directory / 'результат.txt'
        commands, _, stderr = self.run_conversion(
            'txt',
            explicit_output=output_path,
            no_timestamp=False,
        )
        self.assertEqual(stderr, '')
        self.assertEqual(
            Path(commands[0][commands[0].index('--output') + 1]),
            output_path,
        )
        self.assertTrue(output_path.is_file())

    def test_output_directory_is_created_and_used_for_batch(self):
        source_directory = self.directory / 'Books'
        source_directory.mkdir()
        (source_directory / 'First.md').write_text('# First\n', encoding='utf-8')
        (source_directory / 'Second.md').write_text('# Second\n', encoding='utf-8')
        output_directory = self.directory / 'Converted'
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_directory),
            '--format',
            'epub',
            '--input-formats',
            'md',
            '--output',
            str(output_directory),
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(len(commands), 2)
        output_paths = list(output_directory.glob('*.epub'))
        self.assertEqual(len(output_paths), 2)
        for source_name in ('First', 'Second'):
            output_path = next(output_directory.glob(f'{source_name}_(*).epub'))
            self.assertRegex(
                output_path.name,
                rf'^{source_name}_\(\d{{2}}:\d{{2}}_\d{{2}}\.\d{{2}}\.\d{{4}}\)\.epub$',
            )

    def test_relative_output_directory_is_resolved_from_source_file_folder(self):
        source_directory = self.directory / 'Source'
        source_directory.mkdir()
        source_path = source_directory / 'Book.md'
        source_path.write_text('# Book\n', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(source_path),
            '--format',
            'epub',
            '--output',
            'converted/',
            '--no-timestamp',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        expected_output = source_directory / 'converted' / 'Book.epub'
        self.assertEqual(len(commands), 1)
        self.assertEqual(Path(commands[0][commands[0].index('--output') + 1]), expected_output)
        self.assertTrue(expected_output.is_file())

    def test_batch_relative_output_directory_works_for_all_source_types(self):
        source_directory = self.directory / 'Books'
        source_directory.mkdir()
        markdown_path = source_directory / 'Markdown.md'
        text_path = source_directory / 'Plain.txt'
        pdf_path = source_directory / 'Scanned.pdf'
        odt_path = source_directory / 'Document.odt'
        image_directory = source_directory / 'Album'
        image_directory.mkdir()
        markdown_path.write_text('# Markdown\n', encoding='utf-8')
        text_path.write_text('Plain text\n', encoding='utf-8')
        pdf_path.write_bytes(b'%PDF')
        shutil.copyfile(Path(__file__).with_name('test_Draft.odt'), odt_path)
        (image_directory / 'page1.png').write_bytes(b'image')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            if command[0] == 'pdfinfo':
                return type('Result', (), {
                    'stdout': 'Metadata Stream: no\n',
                })()
            if command[0] == 'pdftotext':
                Path(command[-1]).write_text('Extracted text\n', encoding='utf-8')
            else:
                Path(command[command.index('--output') + 1]).write_bytes(self.epub_bytes)

        with patch.object(sys, 'argv', [
            'convert.py',
            str(markdown_path),
            str(text_path),
            str(pdf_path),
            str(odt_path),
            str(image_directory),
            '--format',
            'epub',
            '--output',
            'converted/',
            '--no-timestamp',
        ]):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    convert.main()

        expected_outputs = (
            source_directory / 'converted' / 'Markdown.epub',
            source_directory / 'converted' / 'Plain.epub',
            source_directory / 'converted' / 'Scanned.epub',
            source_directory / 'converted' / 'Document.epub',
            image_directory / 'converted' / 'Album.epub',
        )
        self.assertEqual(len(commands), 6)
        for expected_output in expected_outputs:
            with self.subTest(output=expected_output):
                self.assertTrue(expected_output.is_file())

    def test_missing_pandoc_is_reported(self):
        def missing_pandoc(_command, **_kwargs):
            raise FileNotFoundError

        _, _, stderr = self.run_conversion('pdf', run_side_effect=missing_pandoc)
        self.assertIn('Pandoc не найден', stderr)

    def test_log_file_appends_detailed_records_even_in_quiet_mode(self):
        log_path = self.directory / 'logs' / 'conversion.log'
        log_path.parent.mkdir()
        log_path.write_text('previous run\n', encoding='utf-8')

        _, stdout, stderr = self.run_conversion(
            'txt',
            mode='quiet',
            extra_args=('--log-file', str(log_path)),
        )

        log_contents = log_path.read_text(encoding='utf-8')
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')
        self.assertTrue(log_contents.startswith('previous run\n'))
        self.assertIn('Конвертация ODT в TXT с Pandoc', log_contents)
        self.assertIn('Книга.odt — Книга.txt — УСПЕШНО', log_contents)

    def test_quiet_mode_hides_progress_but_keeps_critical_errors(self):
        _, stdout, stderr = self.run_conversion('txt', mode='quiet')
        self.assertEqual(stdout, '')
        self.assertEqual(stderr, '')

        def missing_pandoc(_command, **_kwargs):
            raise FileNotFoundError

        _, stdout, stderr = self.run_conversion(
            'pdf',
            run_side_effect=missing_pandoc,
            mode='quiet',
        )
        self.assertEqual(stdout, '')
        self.assertIn('Pandoc не найден', stderr)

    def test_summary_mode_prints_source_target_and_result(self):
        _, stdout, stderr = self.run_conversion('txt', mode='summary')
        self.assertEqual(stdout.strip(), 'Книга.odt — Книга.txt — УСПЕШНО')
        self.assertEqual(stderr, '')

    def test_summary_mode_reports_failed_conversion(self):
        def missing_pandoc(_command, **_kwargs):
            raise FileNotFoundError

        _, stdout, stderr = self.run_conversion(
            'pdf',
            run_side_effect=missing_pandoc,
            mode='summary',
        )
        self.assertEqual(stdout.strip(), 'Книга.odt — Книга.pdf — ОШИБКА')
        self.assertIn('Pandoc не найден', stderr)

    def test_verbose_is_the_default_mode(self):
        _, stdout, _ = self.run_conversion('txt')
        self.assertIn('Конвертация ODT в TXT с Pandoc', stdout)

    def test_quiet_mode_suppresses_noncritical_errors(self):
        stderr = io.StringIO()
        with patch('convert.OUTPUT_MODE', 'quiet'):
            with redirect_stderr(stderr):
                convert.log_error('Обложка не найдена', critical=False)
        self.assertEqual(stderr.getvalue(), '')

    def test_exclude_glob_matches_paths_relative_to_input_directory(self):
        books = self.directory / 'books'
        (books / 'temp').mkdir(parents=True)
        (books / 'keep').mkdir()
        excluded_file = books / 'temp' / 'ignore.odt'
        excluded_named_file = books / 'keep' / 'ignore.odt'
        included_file = books / 'keep' / 'include.odt'
        excluded_file.write_bytes(b'')
        excluded_named_file.write_bytes(b'')
        included_file.write_bytes(b'')
        exclude_file = self.directory / 'exclude.txt'
        exclude_file.write_text('keep/ignore.odt\n', encoding='utf-8')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)
            Path(command[command.index('--output') + 1]).write_bytes(b'converted')

        args = [
            'convert.py',
            str(books),
            '--format',
            'txt',
            '--exclude',
            'temp/*',
            '--exclude-file',
            str(exclude_file),
        ]
        stdout = io.StringIO()
        with patch.object(sys, 'argv', args):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(stdout):
                    convert.main()

        converted_inputs = {Path(command[3]) for command in commands}
        self.assertEqual(converted_inputs, {included_file})

    def test_explicitly_excluded_input_directory_is_not_processed(self):
        books = self.directory / 'books'
        books.mkdir()
        (books / 'include.odt').write_bytes(b'')
        commands = []

        def fake_run(command, **_kwargs):
            commands.append(command)

        args = ['convert.py', str(books), '--format', 'txt', '--exclude', 'books']
        with patch.object(sys, 'argv', args):
            with patch('convert.subprocess.run', side_effect=fake_run):
                with redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as exit_context:
                        convert.main()

        self.assertEqual(exit_context.exception.code, 0)
        self.assertEqual(commands, [])

    def test_existing_output_is_skipped_by_default(self):
        output_path = self.directory / 'Книга.txt'
        output_path.write_text('keep this', encoding='utf-8')
        commands, stdout, _ = self.run_conversion('txt', mode='summary')

        self.assertEqual(commands, [])
        self.assertEqual(output_path.read_text(encoding='utf-8'), 'keep this')
        self.assertEqual(stdout.strip(), 'Книга.odt — Книга.txt — ПРОПУЩЕНО')

    def test_force_overwrites_without_prompt(self):
        output_path = self.directory / 'Книга.txt'
        output_path.write_text('old content', encoding='utf-8')
        with patch('builtins.input', side_effect=AssertionError('unexpected prompt')) as input_mock:
            commands, _, _ = self.run_conversion('txt', overwrite_options=('--force',))

        self.assertEqual(len(commands), 1)
        self.assertEqual(output_path.read_bytes(), b'converted')
        input_mock.assert_not_called()

    def test_ask_overwrite_replaces_only_after_confirmation(self):
        output_path = self.directory / 'Книга.txt'
        output_path.write_text('old content', encoding='utf-8')
        with patch('convert.sys.stdin') as stdin:
            stdin.isatty.return_value = True
            with patch('builtins.input', return_value='д') as input_mock:
                commands, _, _ = self.run_conversion(
                    'txt',
                    overwrite_options=('--ask-overwrite',),
                )

        self.assertEqual(len(commands), 1)
        self.assertEqual(output_path.read_bytes(), b'converted')
        input_mock.assert_called_once_with()

    def test_ask_overwrite_preserves_file_when_declined(self):
        output_path = self.directory / 'Книга.txt'
        output_path.write_text('keep this', encoding='utf-8')
        with patch('convert.sys.stdin') as stdin:
            stdin.isatty.return_value = True
            with patch('builtins.input', return_value='нет'):
                commands, _, _ = self.run_conversion(
                    'txt',
                    overwrite_options=('--ask-overwrite',),
                )

        self.assertEqual(commands, [])
        self.assertEqual(output_path.read_text(encoding='utf-8'), 'keep this')

    def test_ask_overwrite_skips_when_input_is_not_interactive(self):
        output_path = self.directory / 'Книга.txt'
        output_path.write_text('keep this', encoding='utf-8')
        with patch('convert.sys.stdin') as stdin:
            stdin.isatty.return_value = False
            commands, _, stderr = self.run_conversion(
                'txt',
                overwrite_options=('--ask-overwrite',),
            )

        self.assertEqual(commands, [])
        self.assertEqual(output_path.read_text(encoding='utf-8'), 'keep this')
        self.assertIn('Нельзя запросить перезапись', stderr)

    def test_epub_skips_existing_output_by_default_and_force_replaces_it(self):
        source = self.directory / 'Книга.odt'
        shutil.copyfile(Path(__file__).with_name('test_Draft.odt'), source)
        output_path = self.directory / 'Книга_(03:04_02.01.2025).epub'
        output_path.write_bytes(b'keep this')
        fixed_time = datetime(2025, 1, 2, 3, 4)

        with patch.object(sys, 'argv', [
            'convert.py', str(source), '--mode', 'summary',
        ]):
            with patch('convert.datetime') as datetime_mock:
                datetime_mock.now.return_value = fixed_time
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertEqual(output_path.read_bytes(), b'keep this')
        self.assertEqual(
            stdout.getvalue().strip(),
            'Книга.odt — Книга_(03:04_02.01.2025).epub — ПРОПУЩЕНО',
        )

        with patch.object(sys, 'argv', [
            'convert.py', str(source), '--mode', 'summary', '--force',
        ]):
            with patch('convert.datetime') as datetime_mock:
                datetime_mock.now.return_value = fixed_time
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    convert.main()

        self.assertTrue(zipfile.is_zipfile(output_path))
        self.assertEqual(
            stdout.getvalue().strip(),
            'Книга.odt — Книга_(03:04_02.01.2025).epub — УСПЕШНО',
        )

    def test_no_timestamp_keeps_odt_epub_output_name(self):
        source = self.directory / 'Книга.odt'
        shutil.copyfile(Path(__file__).with_name('test_Draft.odt'), source)
        stdout = io.StringIO()

        with patch.object(sys, 'argv', [
            'convert.py', str(source), '--mode', 'summary', '--no-timestamp',
        ]):
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                convert.main()

        output_path = self.directory / 'Книга.epub'
        self.assertTrue(zipfile.is_zipfile(output_path))
        self.assertEqual(
            stdout.getvalue().strip(),
            'Книга.odt — Книга.epub — УСПЕШНО',
        )


if __name__ == '__main__':
    unittest.main()
