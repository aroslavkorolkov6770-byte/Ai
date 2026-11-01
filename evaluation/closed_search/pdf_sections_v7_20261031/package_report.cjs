/* Собирает полный исходный код и перечень хешей для локальной проверки.
 * Не изменяет ядро, первый экзамен, журналы ответов или рабочую LMS.
 */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

function main() {
  const here = __dirname;
  const root = path.resolve(here, '../../..');
  const before = path.join(here, 'before/ai_core');
  const relative = file => path.relative(root, file).replaceAll('\\', '/');
  const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const names = fs.readdirSync(path.join(root, 'ai_core')).filter(name => name.endsWith('.py')).sort();
  const changed = names.filter(name => !fs.existsSync(path.join(before, name)) || hash(path.join(before, name)) !== hash(path.join(root, 'ai_core', name)));
  const files = changed.map(name => path.join(root, 'ai_core', name));
  files.push(path.join(root, 'tests/test_site_pdf_sections.py'));
  for (const name of ['run.py', 'postcheck.py', 'grade.cjs', 'package_report.cjs']) files.push(path.join(here, name));
  const start = JSON.parse(fs.readFileSync(path.join(here, 'results/verified/START.json'), 'utf8'));
  for (const [name, expected] of Object.entries(start.runtime)) {
    if (hash(path.join(root, name)) !== expected) throw new Error('Core changed after run: ' + name);
  }
  const manifest = {
    at: new Date().toISOString(), root, final_run: 'results/verified',
    runtime_count: Object.keys(start.runtime).length,
    changed_core: changed.map(name => ({
      path: 'ai_core/' + name,
      before: fs.existsSync(path.join(before, name)) ? hash(path.join(before, name)) : null,
      after: hash(path.join(root, 'ai_core', name))
    })),
    full_code_files: files.map(file => ({ path: relative(file), sha256: hash(file) })),
    checks: ['tests-verified.txt', 'verified/answers.jsonl', 'verified/judgments.json', 'verified/postcheck.json', 'verified/summary.json']
      .map(name => ({ path: 'results/' + name, sha256: hash(path.join(here, 'results', name)) }))
  };
  const md = ['# Разделы PDF и уточнения: полный код', '',
    'Проект: C:/Users/KROLL/Desktop/Ai. Полные текущие исходники изменённых файлов, нового теста и проверочных скриптов. Содержимое не сокращено. Для ядра проверено совпадение с хешами итогового прогона verified. Снимок прежнего ядра сохранён в evaluation/closed_search/pdf_sections_v7_20261031/before/ai_core.', '',
    'Отчёт и ограничения: PDF_SECTIONS_V7_REPORT_2026_10_31.md. Текущий результат 72/100 не является приёмкой универсального качества; рабочая LMS автоматически не обновлялась.', ''];
  for (const file of files) {
    const body = fs.readFileSync(file, 'utf8');
    md.push('## ' + relative(file), '', 'Полный путь: ' + file + '.', '', 'SHA-256: `' + hash(file) + '`.', '',
      '````' + (file.endsWith('.py') ? 'python' : 'javascript'), body, '````', '');
  }
  fs.writeFileSync(path.join(root, 'PDF_SECTIONS_V7_FULL_CODE_2026_10_31.md'), md.join('\n'));
  fs.writeFileSync(path.join(here, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
  console.log(JSON.stringify({ changed_core: changed.length, full_files: files.length, runtime_count: manifest.runtime_count }));
}
try { main(); } catch (error) { console.error(error.stack); process.exitCode = 1; }
