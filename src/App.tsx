function App() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 text-white">
      <div className="container mx-auto px-4 py-10 max-w-4xl">
        
        {/* Header */}
        <div className="text-center mb-12">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-yellow-500/20 border border-yellow-500/30 mb-5">
            <span className="text-4xl">🐍</span>
          </div>
          <h1 className="text-5xl font-bold mb-3 bg-gradient-to-r from-yellow-400 to-orange-400 bg-clip-text text-transparent">
            odt2epub
          </h1>
          <p className="text-xl text-slate-400">
            Python CLI для конвертации ODT → EPUB
          </p>
          <p className="text-sm text-slate-500 mt-2">
            Сохранение форматирования • Обложка JPEG/PNG • Встроенные изображения
          </p>
        </div>

        {/* Terminal Preview */}
        <div className="mb-10 rounded-2xl overflow-hidden border border-slate-700 shadow-2xl">
          <div className="bg-slate-800 px-4 py-3 flex items-center gap-2 border-b border-slate-700">
            <div className="w-3 h-3 rounded-full bg-red-500"></div>
            <div className="w-3 h-3 rounded-full bg-yellow-500"></div>
            <div className="w-3 h-3 rounded-full bg-green-500"></div>
            <span className="ml-3 text-sm text-slate-400 font-mono">Terminal</span>
          </div>
          <div className="bg-slate-900 p-6 font-mono text-sm leading-relaxed overflow-x-auto">
            <div className="text-yellow-400">$ python convert.py -i book.odt -c cover.jpg -o book.epub</div>
            <div className="text-slate-400 mt-3">
              <pre className="whitespace-pre-wrap">{`
  ╔══════════════════════════════════════════╗
  ║       ODT → EPUB Converter v1.0.0       ║
  ╚══════════════════════════════════════════╝

  ✅ Обложка: cover.jpg (image/jpeg)

  [1/5] Чтение ODT файла...
  ✅ ODT прочитан (изображений в документе: 3)

  [2/5] Анализ стилей...
  ✅ Стили: 24 из styles.xml, 12 из content.xml
  ✅ Метаданные: "Моя книга" — Иван Иванов

  [3/5] Конвертация содержимого в XHTML...
  ✅ XHTML сгенерирован (15420 символов, 3 изображений)

  [4/5] Сборка EPUB...
  ✅ EPUB собран (245.3 КБ)

  [5/5] Сохранение файла...
  ✅ Файл сохранён: /home/user/book.epub

  ┌──────────────────────────────────────────┐
  │  📖 Моя книга
  │  ✍️  Иван Иванов
  │  🌐 RU
  │  📦 245.3 КБ
  │  📁 book.epub
  └──────────────────────────────────────────┘

  ✅ Конвертация завершена!`}</pre>
            </div>
          </div>
        </div>

        {/* Installation */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">📦</span> Установка
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5">
            <div className="font-mono text-sm">
              <div className="text-slate-400"># Скрипт использует только стандартные библиотеки Python!</div>
              <div className="text-slate-400"># Никаких зависимостей не требуется.</div>
              <div className="text-yellow-300 mt-3"># Просто скачайте convert.py и запустите:</div>
              <div className="text-yellow-300 mt-1">python convert.py --help</div>
            </div>
          </div>
        </div>

        {/* Usage */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">🚀</span> Использование
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5 font-mono text-sm space-y-3">
            <div>
              <div className="text-slate-400"># Базовая конвертация (обложка ищется автоматически)</div>
              <div className="text-yellow-300">python convert.py -i document.odt</div>
            </div>
            <div>
              <div className="text-slate-400"># С явной обложкой</div>
              <div className="text-yellow-300">python convert.py -i document.odt -c cover.jpg</div>
            </div>
            <div>
              <div className="text-slate-400"># Полные параметры</div>
              <div className="text-yellow-300">python convert.py -i document.odt -c cover.jpg -o output.epub -t "Название" -a "Автор" -l ru</div>
            </div>
            <div>
              <div className="text-slate-400"># Показать справку</div>
              <div className="text-yellow-300">python convert.py --help</div>
            </div>
          </div>
        </div>

        {/* Options Table */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">⚙️</span> Параметры
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800">
                  <th className="text-left px-5 py-3 text-slate-300 font-medium">Флаг</th>
                  <th className="text-left px-5 py-3 text-slate-300 font-medium">Описание</th>
                  <th className="text-left px-5 py-3 text-slate-300 font-medium">Обязательный</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-700/50">
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-i, --input</td>
                  <td className="px-5 py-3 text-slate-300">Путь к входному ODT файлу</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-red-500/20 text-red-300 rounded text-xs">Да</span></td>
                </tr>
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-c, --cover</td>
                  <td className="px-5 py-3 text-slate-300">Путь к обложке (JPEG/PNG). Если не указан, ищется файл с тем же именем, что и ODT</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-slate-600/50 text-slate-400 rounded text-xs">Нет</span></td>
                </tr>
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-o, --output</td>
                  <td className="px-5 py-3 text-slate-300">Путь для выходного EPUB (по умолчанию: имя ODT + .epub)</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-slate-600/50 text-slate-400 rounded text-xs">Нет</span></td>
                </tr>
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-t, --title</td>
                  <td className="px-5 py-3 text-slate-300">Название книги (по умолчанию: имя файла ODT без расширения)</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-slate-600/50 text-slate-400 rounded text-xs">Нет</span></td>
                </tr>
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-a, --author</td>
                  <td className="px-5 py-3 text-slate-300">Автор книги (по умолчанию из метаданных ODT)</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-slate-600/50 text-slate-400 rounded text-xs">Нет</span></td>
                </tr>
                <tr className="hover:bg-slate-700/20">
                  <td className="px-5 py-3 font-mono text-yellow-400">-l, --lang</td>
                  <td className="px-5 py-3 text-slate-300">Язык книги (ISO 639-1, по умолчанию: пусто)</td>
                  <td className="px-5 py-3"><span className="px-2 py-0.5 bg-slate-600/50 text-slate-400 rounded text-xs">Нет</span></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        {/* Features */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">✨</span> Возможности
          </h2>
          <div className="grid md:grid-cols-2 gap-4">
            {[
              { icon: '📝', title: 'Форматирование', desc: 'Заголовки, абзацы, жирный, курсив, подчёркивание, зачёркивание, цвет текста' },
              { icon: '📊', title: 'Таблицы', desc: 'Полная поддержка таблиц с объединением ячеек' },
              { icon: '📋', title: 'Списки', desc: 'Маркированные и нумерованные списки' },
              { icon: '🖼️', title: 'Изображения', desc: 'Встроенные изображения из ODT переносятся в EPUB' },
              { icon: '🎨', title: 'Обложка', desc: 'JPEG/PNG обложка с отдельной страницей' },
              { icon: '🔗', title: 'Ссылки', desc: 'Гиперссылки сохраняются' },
              { icon: '📐', title: 'Стили', desc: 'CSS-стили генерируются из ODT-стилей' },
              { icon: '📚', title: 'Метаданные', desc: 'Автор, название, язык, дата из ODT или CLI' },
            ].map((item, i) => (
              <div key={i} className="bg-slate-800/30 border border-slate-700/50 rounded-xl p-4 flex gap-3">
                <span className="text-2xl">{item.icon}</span>
                <div>
                  <h3 className="font-medium text-white">{item.title}</h3>
                  <p className="text-sm text-slate-400 mt-0.5">{item.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Requirements */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">🐍</span> Требования
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5">
            <ul className="space-y-2 text-slate-300">
              <li className="flex items-center gap-2">
                <span className="text-green-400">✓</span>
                <span>Python 3.6+</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="text-green-400">✓</span>
                <span>Стандартные библиотеки (zipfile, xml.etree.ElementTree, argparse)</span>
              </li>
              <li className="flex items-center gap-2">
                <span className="text-green-400">✓</span>
                <span>Без внешних зависимостей!</span>
              </li>
            </ul>
          </div>
        </div>

        {/* EPUB Structure */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">📁</span> Структура EPUB
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5 font-mono text-sm">
            <pre className="text-slate-300">{`book.epub (ZIP-архив)
├── mimetype
├── META-INF/
│   └── container.xml
└── OEBPS/
    ├── content.opf        (манифест книги)
    ├── toc.ncx            (таблица содержимого)
    ├── style.css          (стили документа)
    ├── cover.xhtml        (страница обложки)
    ├── chapter1.xhtml     (содержимое)
    └── images/
        ├── cover.jpg      (обложка)
        └── img_0.jpg      (изображения из ODT)`}</pre>
          </div>
        </div>

        {/* Code Example */}
        <div className="mb-8">
          <h2 className="text-2xl font-bold mb-4 flex items-center gap-2">
            <span className="text-yellow-400">💻</span> Пример кода
          </h2>
          <div className="bg-slate-800/50 border border-slate-700 rounded-xl p-5 font-mono text-sm overflow-x-auto">
            <pre className="text-slate-300">{`# Импорт и использование в вашем коде
import convert

# Парсинг ODT
odt_content = convert.parse_odt('document.odt')

# Анализ стилей
styles_map = convert.parse_styles(odt_content.styles_xml)
auto_styles_map = convert.parse_content_styles(odt_content.content_xml)

# Конвертация в XHTML
converter = convert.XHTMLConverter(styles_map, auto_styles_map, odt_content.images)
xhtml, css, image_files = converter.convert(odt_content.content_xml)

# Генерация EPUB
epub_buffer = convert.generate_epub(xhtml, css, image_files, {
    'title': 'Моя книга',
    'author': 'Автор',
    'date': '2024-01-01',
    'language': 'ru',
    'cover_image': cover_bytes,
    'cover_mime_type': 'image/jpeg',
})

# Сохранение
with open('output.epub', 'wb') as f:
    f.write(epub_buffer)`}</pre>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center text-slate-500 text-sm pt-8 border-t border-slate-800">
          <p>ODT → EPUB Converter CLI • Python</p>
          <p className="mt-1">Файл: <code className="text-yellow-400/70 bg-slate-800 px-2 py-0.5 rounded">convert.py</code></p>
        </div>
      </div>
    </div>
  );
}

export default App;
