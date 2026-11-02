import { useState, useCallback } from 'react';
import { parseODT, parseStyles, parseContentStyles, extractMeta } from './utils/odtParser';
import { convertODTToXHTML } from './utils/odtToXhtml';
import { generateEpub, downloadEpub } from './utils/epubGenerator';

type ConvertStatus = 'idle' | 'loading' | 'converting' | 'done' | 'error';

function App() {
  const [odtFile, setOdtFile] = useState<File | null>(null);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [author, setAuthor] = useState('');
  const [language, setLanguage] = useState('ru');
  const [status, setStatus] = useState<ConvertStatus>('idle');
  const [errorMessage, setErrorMessage] = useState('');
  const [progress, setProgress] = useState('');

  const handleOdtChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setOdtFile(file);
      // Try to extract title from file
      if (!title) {
        setTitle(file.name.replace(/\.odt$/i, ''));
      }
    }
  }, [title]);

  const handleCoverChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setCoverFile(file);
      const reader = new FileReader();
      reader.onload = (ev) => {
        setCoverPreview(ev.target?.result as string);
      };
      reader.readAsDataURL(file);
    }
  }, []);

  const handleConvert = useCallback(async () => {
    if (!odtFile) {
      setErrorMessage('Пожалуйста, выберите ODT файл');
      setStatus('error');
      return;
    }

    try {
      setStatus('loading');
      setProgress('Чтение ODT файла...');

      // Parse ODT
      const odtContent = await parseODT(odtFile);
      
      setProgress('Анализ стилей...');
      const stylesMap = parseStyles(odtContent.stylesXml);
      const autoStylesMap = parseContentStyles(odtContent.contentXml);
      
      // Extract metadata
      const meta = extractMeta(odtContent.metaXml);
      if (!title) setTitle(meta.title);
      if (!author) setAuthor(meta.author);

      setStatus('converting');
      setProgress('Конвертация содержимого...');

      // Convert to XHTML
      const convertResult = convertODTToXHTML(
        odtContent.contentXml,
        stylesMap,
        autoStylesMap,
        odtContent.images
      );

      setProgress('Создание EPUB...');

      // Get cover image data
      let coverImageData: Uint8Array | undefined;
      let coverMimeType: string | undefined;
      if (coverFile) {
        const buffer = await coverFile.arrayBuffer();
        coverImageData = new Uint8Array(buffer);
        coverMimeType = coverFile.type;
      }

      // Generate EPUB
      const epubBlob = await generateEpub(convertResult, {
        title: title || meta.title || 'Untitled',
        author: author || meta.author || 'Unknown',
        date: meta.date,
        language,
        coverImage: coverImageData,
        coverMimeType,
      });

      setProgress('Скачивание...');
      const filename = (title || meta.title || 'document').replace(/[^a-zA-Zа-яА-Я0-9_-]/g, '_') + '.epub';
      downloadEpub(epubBlob, filename);

      setStatus('done');
      setProgress('');
    } catch (err) {
      console.error(err);
      setErrorMessage(err instanceof Error ? err.message : 'Произошла ошибка при конвертации');
      setStatus('error');
      setProgress('');
    }
  }, [odtFile, coverFile, title, author, language]);

  const handleReset = () => {
    setOdtFile(null);
    setCoverFile(null);
    setCoverPreview(null);
    setTitle('');
    setAuthor('');
    setStatus('idle');
    setErrorMessage('');
    setProgress('');
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900">
      <div className="container mx-auto px-4 py-8 max-w-4xl">
        {/* Header */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-purple-600/30 backdrop-blur-sm mb-4">
            <svg className="w-8 h-8 text-purple-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
            </svg>
          </div>
          <h1 className="text-4xl font-bold text-white mb-2">
            ODT → EPUB Конвертер
          </h1>
          <p className="text-purple-200/70 text-lg">
            Конвертация документов ODT в формат EPUB с сохранением форматирования
          </p>
        </div>

        {/* Main Card */}
        <div className="bg-white/5 backdrop-blur-xl rounded-3xl border border-white/10 p-8 shadow-2xl">
          
          {/* File Upload Section */}
          <div className="grid md:grid-cols-2 gap-6 mb-8">
            {/* ODT File Upload */}
            <div>
              <label className="block text-sm font-medium text-purple-200 mb-2">
                📄 ODT Документ
              </label>
              <div className={`relative border-2 border-dashed rounded-2xl p-6 text-center transition-all cursor-pointer hover:border-purple-400 hover:bg-purple-500/5 ${
                odtFile ? 'border-green-400/50 bg-green-500/5' : 'border-white/20'
              }`}>
                <input
                  type="file"
                  accept=".odt"
                  onChange={handleOdtChange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                {odtFile ? (
                  <div>
                    <div className="text-3xl mb-2">✅</div>
                    <p className="text-green-300 font-medium text-sm truncate">{odtFile.name}</p>
                    <p className="text-green-400/60 text-xs mt-1">
                      {(odtFile.size / 1024).toFixed(1)} КБ
                    </p>
                  </div>
                ) : (
                  <div>
                    <div className="text-3xl mb-2">📁</div>
                    <p className="text-purple-200/80 text-sm">Нажмите или перетащите ODT файл</p>
                    <p className="text-purple-300/50 text-xs mt-1">Поддерживается формат .odt</p>
                  </div>
                )}
              </div>
            </div>

            {/* Cover Image Upload */}
            <div>
              <label className="block text-sm font-medium text-purple-200 mb-2">
                🖼️ Обложка (JPEG/PNG)
              </label>
              <div className={`relative border-2 border-dashed rounded-2xl p-6 text-center transition-all cursor-pointer hover:border-purple-400 hover:bg-purple-500/5 ${
                coverFile ? 'border-green-400/50 bg-green-500/5' : 'border-white/20'
              }`}>
                <input
                  type="file"
                  accept="image/jpeg,image/jpg,image/png"
                  onChange={handleCoverChange}
                  className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                />
                {coverPreview ? (
                  <div>
                    <img src={coverPreview} alt="Cover preview" className="w-16 h-20 object-cover rounded-lg mx-auto mb-2 shadow-lg" />
                    <p className="text-green-300 font-medium text-xs truncate">{coverFile?.name}</p>
                  </div>
                ) : (
                  <div>
                    <div className="text-3xl mb-2">🎨</div>
                    <p className="text-purple-200/80 text-sm">Загрузите обложку (необязательно)</p>
                    <p className="text-purple-300/50 text-xs mt-1">JPEG или PNG</p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Metadata Section */}
          <div className="grid md:grid-cols-3 gap-4 mb-8">
            <div>
              <label className="block text-sm font-medium text-purple-200 mb-2">
                Название книги
              </label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Введите название..."
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white placeholder-purple-300/40 focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-transparent transition-all"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-purple-200 mb-2">
                Автор
              </label>
              <input
                type="text"
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                placeholder="Имя автора..."
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white placeholder-purple-300/40 focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-transparent transition-all"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-purple-200 mb-2">
                Язык
              </label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                className="w-full px-4 py-3 bg-white/5 border border-white/10 rounded-xl text-white focus:outline-none focus:ring-2 focus:ring-purple-500/50 focus:border-transparent transition-all"
              >
                <option value="ru" className="bg-slate-800">Русский</option>
                <option value="en" className="bg-slate-800">English</option>
                <option value="de" className="bg-slate-800">Deutsch</option>
                <option value="fr" className="bg-slate-800">Français</option>
                <option value="es" className="bg-slate-800">Español</option>
                <option value="uk" className="bg-slate-800">Українська</option>
              </select>
            </div>
          </div>

          {/* Progress / Status */}
          {(status === 'loading' || status === 'converting') && (
            <div className="mb-6 p-4 bg-blue-500/10 border border-blue-400/20 rounded-xl">
              <div className="flex items-center gap-3">
                <div className="animate-spin w-5 h-5 border-2 border-blue-400 border-t-transparent rounded-full"></div>
                <span className="text-blue-200">{progress}</span>
              </div>
              <div className="mt-3 w-full bg-blue-900/30 rounded-full h-2">
                <div className="bg-blue-400 h-2 rounded-full transition-all duration-500 animate-pulse" style={{width: status === 'converting' ? '70%' : '30%'}}></div>
              </div>
            </div>
          )}

          {status === 'done' && (
            <div className="mb-6 p-4 bg-green-500/10 border border-green-400/20 rounded-xl">
              <div className="flex items-center gap-3">
                <span className="text-2xl">🎉</span>
                <span className="text-green-200 font-medium">EPUB файл успешно создан и скачан!</span>
              </div>
            </div>
          )}

          {status === 'error' && (
            <div className="mb-6 p-4 bg-red-500/10 border border-red-400/20 rounded-xl">
              <div className="flex items-center gap-3">
                <span className="text-2xl">❌</span>
                <span className="text-red-200">{errorMessage}</span>
              </div>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-4">
            <button
              onClick={handleConvert}
              disabled={!odtFile || status === 'loading' || status === 'converting'}
              className="flex-1 py-4 px-6 bg-gradient-to-r from-purple-600 to-pink-600 hover:from-purple-500 hover:to-pink-500 disabled:from-gray-600 disabled:to-gray-700 disabled:cursor-not-allowed text-white font-semibold rounded-xl shadow-lg shadow-purple-500/25 transition-all duration-200 transform hover:scale-[1.02] active:scale-[0.98]"
            >
              {status === 'loading' || status === 'converting' ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="animate-spin w-5 h-5 border-2 border-white border-t-transparent rounded-full"></div>
                  Конвертация...
                </span>
              ) : (
                <span className="flex items-center justify-center gap-2">
                  <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Конвертировать в EPUB
                </span>
              )}
            </button>
            
            <button
              onClick={handleReset}
              className="py-4 px-6 bg-white/5 hover:bg-white/10 border border-white/10 text-purple-200 font-medium rounded-xl transition-all duration-200"
            >
              Сброс
            </button>
          </div>
        </div>

        {/* Info Section */}
        <div className="mt-8 grid md:grid-cols-3 gap-4">
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl border border-white/10 p-5">
            <div className="text-2xl mb-2">📝</div>
            <h3 className="text-white font-medium mb-1">Форматирование</h3>
            <p className="text-purple-200/60 text-sm">
              Сохраняются заголовки, списки, таблицы, жирный/курсив/подчёркивание
            </p>
          </div>
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl border border-white/10 p-5">
            <div className="text-2xl mb-2">🖼️</div>
            <h3 className="text-white font-medium mb-1">Изображения</h3>
            <p className="text-purple-200/60 text-sm">
              Встроенные изображения из ODT переносятся в EPUB
            </p>
          </div>
          <div className="bg-white/5 backdrop-blur-sm rounded-2xl border border-white/10 p-5">
            <div className="text-2xl mb-2">📚</div>
            <h3 className="text-white font-medium mb-1">Обложка</h3>
            <p className="text-purple-200/60 text-sm">
              JPEG/PNG изображение будет использовано как обложка книги
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="text-center mt-8 text-purple-300/40 text-sm">
          Конвертация выполняется полностью в браузере. Файлы не загружаются на сервер.
        </div>
      </div>
    </div>
  );
}

export default App;
