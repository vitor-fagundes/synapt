// Teste de ponta a ponta do simulador web (site/).
//
// Uso (na pasta tools/):  npm install && node test_site_e2e.mjs <dir-capturas>
// Sem playwright instalado em tools/, o teste é PULADO com aviso (código 0).
// Serve site/ com python3 -m http.server em 127.0.0.1 e grava as capturas em
// <dir-capturas> (fora de site/). Código de saída 1 se algum critério falhar.
import { spawn } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SITE = path.resolve(HERE, '..', 'site');
const OUT = path.resolve(process.argv[2] || path.join(HERE, '..', '.e2e-capturas'));
if (OUT === SITE || OUT.startsWith(SITE + path.sep)) { console.error('O diretório de capturas não pode ficar dentro de site/.'); process.exit(2); }

let chromium;
try { ({ chromium } = await import('playwright')); }
catch { console.log('PULADO: playwright não instalado em tools/ (rode `npm install` em tools/).'); process.exit(0); }

mkdirSync(OUT, { recursive: true });
const PORT = 8000 + Math.floor(Math.random() * 900);
const server = spawn('python3', ['-m', 'http.server', String(PORT), '--bind', '127.0.0.1'], { cwd: SITE, stdio: 'ignore' });
const BASE = `http://127.0.0.1:${PORT}/`;
const results = [];
const check = (name, ok, extra = '') => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${extra ? ' — ' + extra : ''}`); };

async function waitServer() {
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch(BASE + 'data/index.json'); if (r.ok) return; } catch { }
    await new Promise(r => setTimeout(r, 100));
  }
  throw new Error('servidor local não respondeu');
}

function csvFromGroup(id, run, mutate) {
  const g = JSON.parse(readFileSync(path.join(SITE, 'data', id + '.json'), 'utf8'));
  const d = g.runs[run - 1].d;
  const head = 'timestamp,qi,sr,tii,arf,meanRho,netLearning,pThreat,totalOrphans,actionsReallocate,actionsRecluster,actionsDoNothing,cycleS1,cycleS2,anomalies,qDoNothing,qReallocate,qRecluster';
  const rows = [];
  for (let k = 0; k < 145; k++) rows.push([170 + 5 * k, ...d.map(c => c[k])].join(','));
  if (mutate) mutate(rows);
  return head + '\n' + rows.join('\n') + '\n';
}

async function newPage(browser, opts, errors) {
  const ctx = await browser.newContext(opts);
  const page = await ctx.newPage();
  if (errors) {
    page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
    page.on('pageerror', e => errors.push(`pageerror: ${e.message}`));
  }
  return { ctx, page };
}
const ready = page => page.waitForFunction(() => document.querySelectorAll('#kpis .kpi').length === 8 && document.querySelector('#cmp tbody tr'), null, { timeout: 15000 });

let code = 0;
try {
  await waitServer();
  const browser = await chromium.launch();
  const errors = [];

  for (const [w, h, label] of [[1366, 900, 'desktop'], [390, 844, '390px']]) {
    for (const scheme of ['light', 'dark']) {
      const { ctx, page } = await newPage(browser, { viewport: { width: w, height: h }, colorScheme: scheme, deviceScaleFactor: 1 }, errors);
      await page.goto(BASE);
      await ready(page);
      await page.evaluate(() => document.fonts.ready);
      // avança alguns ciclos para a captura mostrar a 1ª onda
      for (let i = 0; i < 28; i++) await page.click('#next');
      const errBanner = await page.$eval('#err', e => e.classList.contains('on'));
      check(`${label}/${scheme}: sem faixa de erro`, !errBanner);
      if (w === 390) {
        const sw = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
        check(`${label}/${scheme}: sem rolagem horizontal`, sw[0] <= sw[1], `scrollWidth ${sw[0]} / innerWidth ${sw[1]}`);
      }
      await page.screenshot({ path: path.join(OUT, `${label}-${scheme}.png`), fullPage: true });
      await page.screenshot({ path: path.join(OUT, `${label}-${scheme}-topo.png`) });
      await ctx.close();
    }
  }

  // interações principais
  {
    const { ctx, page } = await newPage(browser, { viewport: { width: 1366, height: 900 } }, errors);
    await page.goto(BASE); await ready(page);
    for (let i = 0; i < 28; i++) await page.click('#next');
    const cyc = await page.textContent('#cyc'), clk = await page.textContent('#clk');
    check('replay: 28 × próximo → ciclo 29 de 145 e t = 310 s', cyc.includes('ciclo 29 de 145') && clk.trim() === 't = 310 s', `${clk} | ${cyc}`);
    await page.click('#segN button[data-n="300"]'); await page.click('#segC button[data-c="no"]');
    await page.check('#mean'); await page.selectOption('#met', 'pt');
    await page.waitForTimeout(300);
    const notes = await page.textContent('#notes');
    check('N=300: aviso pré/pós-calibragem', notes.includes('pré-calibragem') && notes.includes('pós-calibragem'));
    const thr = await page.textContent('#thr');
    check('300 sem falhas: limiar 0,200 / ξ −0,050', thr.includes('0,200') && thr.includes('-0,050'), thr);
    await page.click('#segC button[data-c="with"]'); await page.waitForTimeout(200);
    check('300 com falhas: limiar 0,245 / ξ −0,033', (await page.textContent('#thr')).includes('0,245'));
    await page.click('#segN button[data-n="250"]'); await page.click('#segC button[data-c="random"]'); await page.waitForTimeout(300);
    const wv = await page.$$eval('#chart .wv line', l => l.length);
    check('250 aleatório em modo média: sem marcação de ondas', wv === 0, `${wv} linhas`);
    await page.uncheck('#mean'); await page.selectOption('#run', '7'); await page.waitForTimeout(200);
    const wv2 = await page.$$eval('#chart .wv line', l => l.length);
    check('250 aleatório, rodada 7: 3 ondas marcadas', wv2 === 3, `${wv2} linhas`);
    const disabled = await page.click('#segN button[data-n="200"]').then(() => page.$eval('#segC button[data-c="random"]', b => b.disabled));
    check('N=200: botão "falhas aleatórias" desabilitado', disabled);
    await page.click('#pp'); await page.waitForTimeout(1600); await page.click('#pp');
    const k = await page.$eval('#tl', e => +e.value);
    check('reproduzir/pausar avança o replay', k > 0, `ciclo ${k + 1}`);

    // upload de CSV
    const csv = csvFromGroup('200_with_failures', 2);
    await page.setInputFiles('#fi', { name: 'IntuitiveStats.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) });
    await page.click('#ld'); await page.waitForTimeout(300);
    const msg1 = await page.textContent('#fmsg'), cyc2 = await page.textContent('#cyc');
    check('upload CSV válido', msg1.includes('Carregado') && cyc2.includes('arquivo local'), msg1);
    const bad = csvFromGroup('200_with_failures', 2, rows => { const t = rows[10]; rows[10] = rows[11]; rows[11] = t; });
    await page.setInputFiles('#fi', { name: 'IntuitiveStats.csv', mimeType: 'text/csv', buffer: Buffer.from(bad) });
    await page.click('#ld'); await page.waitForTimeout(200);
    const msg2 = await page.textContent('#fmsg');
    check('upload CSV com timestamps fora de ordem → erro', msg2.includes('estritamente crescente'), msg2);
    await page.setInputFiles('#fi', { name: 'trace.json', mimeType: 'application/json', buffer: Buffer.from('{"model":"synapt"}') });
    await page.click('#ld'); await page.waitForTimeout(200);
    const msg3 = await page.textContent('#fmsg');
    check('upload .json → mensagem da Fase B', msg3.includes('Fase B'), msg3);
    await page.click('#localexit'); await page.waitForTimeout(200);
    check('sair do arquivo local volta aos dados publicados', !(await page.textContent('#cyc')).includes('arquivo local'));
    await ctx.close();
  }
  check('zero erros de console/pageerror nas páginas normais', errors.length === 0, errors.slice(0, 5).join(' | '));

  // falhas de fetch (erros esperados; contexto separado)
  for (const [label, handler, expect] of [
    ['index.json com falha de rede', route => route.abort(), 'falha de rede'],
    ['index.json com HTTP 404', route => route.fulfill({ status: 404, body: 'x' }), 'HTTP 404'],
    ['index.json inválido', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{x' }), 'JSON inválido'],
    ['index.json fora do schema', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{"v":1,"t0":170,"dt":5,"nc":145,"cols":[],"grupos":[]}' }), 'formato inesperado'],
  ]) {
    const { ctx, page } = await newPage(browser, { viewport: { width: 1366, height: 900 } }, null);
    await page.route('**/data/index.json', handler);
    await page.goto(BASE);
    await page.waitForSelector('#err.on', { timeout: 10000 }).catch(() => { });
    const on = await page.$eval('#err', e => e.classList.contains('on'));
    const txt = await page.textContent('#errmsg');
    check(`${label} → faixa de erro visível`, on && txt.includes(expect) && txt.includes('data/index.json'), txt);
    const kpis = await page.$$eval('#kpis .kpi', l => l.length);
    check(`${label} → nenhum dado exibido`, kpis === 0);
    if (label.includes('rede')) await page.screenshot({ path: path.join(OUT, 'erro-fetch.png') });
    await ctx.close();
  }
  // grupo indisponível depois da carga inicial: faixa de erro, painéis vazios, sem pageerror
  {
    const errs = [];
    const { ctx, page } = await newPage(browser, { viewport: { width: 1366, height: 900 } }, null);
    page.on('pageerror', e => errs.push(e.message));
    await page.route('**/data/300_no_failures.json', route => route.fulfill({ status: 404, body: 'x' }));
    await page.goto(BASE); await ready(page);
    await page.click('#segN button[data-n="300"]'); await page.click('#segC button[data-c="no"]');
    await page.waitForSelector('#err.on', { timeout: 10000 }).catch(() => { });
    await page.click('#next'); await page.click('#next'); await page.click('#pp'); await page.waitForTimeout(800); await page.click('#pp');
    const txt = await page.textContent('#errmsg');
    const kpis = await page.$$eval('#kpis .kpi', l => l.length);
    check('grupo com HTTP 404 → faixa de erro com a URL', txt.includes('data/300_no_failures.json') && txt.includes('HTTP 404'), txt);
    check('grupo com HTTP 404 → painéis vazios', kpis === 0, `${kpis} KPIs`);
    check('grupo com HTTP 404 → nenhum pageerror ao navegar', errs.length === 0, errs.join(' | '));
    await page.click('#segC button[data-c="with"]'); await page.waitForTimeout(300);
    check('N=300 com falhas: tabela comparativa ainda acusa o 300_no_failures indisponível', (await page.textContent('#errmsg')).includes('300_no_failures'));
    await page.click('#segN button[data-n="200"]'); await page.waitForTimeout(300);
    check('voltar a N=200 (todos disponíveis) recupera a página', !(await page.$eval('#err', e => e.classList.contains('on'))) && (await page.$$eval('#kpis .kpi', l => l.length)) === 8);
    await ctx.close();
  }
  await browser.close();
} catch (e) {
  console.error('ERRO no teste:', e);
  code = 1;
} finally {
  server.kill();
}
const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} critérios passaram; capturas em ${OUT}`);
process.exit(code || (failed ? 1 : 0));
