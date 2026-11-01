/* Продолжение только неотвеченных вопросов; первый журнал остаётся неизменным. */
const { fs, path, root, lab, call, login } = require('./lab_client.cjs');
const { createHash } = require('node:crypto');
const directory = path.join(lab, 'uep-acceptance');
const digest = data => createHash('sha256').update(data).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function wait(seconds) {
    for (let remaining = seconds; remaining > 0; remaining -= 30) {
        console.log(JSON.stringify({ event: 'rate-limit-wait', remainingSeconds: remaining }));
        await pause(Math.min(remaining, 30) * 1000);
    }
}

async function verify(frozen, specBytes, spec) {
    if (digest(specBytes) !== frozen.specSHA256) throw new Error('Frozen questions changed');
    if (digest(await fs.readFile(spec.source)) !== frozen.sourceSHA256) throw new Error('Original PDF changed');
    for (const entry of frozen.files) {
        if (digest(await fs.readFile(path.join(root, entry.file))) !== entry.sha256) {
            throw new Error('AI/site code changed: ' + entry.file);
        }
    }
}

async function main() {
    const frozen = JSON.parse(await fs.readFile(path.join(directory, 'frozen.json'), 'utf8'));
    const specBytes = await fs.readFile(path.join(__dirname, 'uep_40_20261030.json'));
    const spec = JSON.parse(specBytes);
    const firstBytes = await fs.readFile(path.join(directory, 'answers.json'));
    const first = JSON.parse(firstBytes);
    await verify(frozen, specBytes, spec);
    const output = path.join(directory, 'answers-continuation.json');
    const continuation = { startedAt: new Date().toISOString(), originalAnswersSHA256: digest(firstBytes),
        reason: 'First runner stopped after 30 answers on HTTP 429; missing Retry-After had been read as zero. No AI changes.',
        cases: [], waits: [] };
    await fs.writeFile(output, JSON.stringify(continuation, null, 2), { flag: 'wx' });
    // Ожидаем целое окно лимита. Тот же профиль, без сброса счётчиков и смены сервера.
    const remainingSeconds = Math.max(0, Math.ceil((Date.parse(first.startedAt) + 310000 - Date.now()) / 1000));
    if (remainingSeconds) await wait(remainingSeconds);
    const cookie = await login('closed-b');
    const completed = new Set(first.cases.map(item => item.id));
    for (const item of spec.cases.filter(item => !completed.has(item.id))) {
        const request = { chatId: 'uep-remaining-' + Date.now() + '-' + item.id,
            documentId: frozen.documentId, messages: [{ role: 'user', content: item.question }] };
        let result, started, attempts = 0;
        while (attempts < 4) {
            started = performance.now();
            result = await call('/api/ai-chat', request, cookie);
            attempts++;
            if (result.response.status !== 429) break;
            const header = result.response.headers.get('retry-after');
            const numeric = header === null ? NaN : Number(header);
            const seconds = Number.isFinite(numeric) && numeric >= 0 ? Math.ceil(numeric) + 1 : 310;
            if (seconds > 900) throw new Error('Rate limit requires longer pause: ' + seconds);
            continuation.waits.push({ id: item.id, at: new Date().toISOString(), header, seconds });
            await fs.writeFile(output, JSON.stringify(continuation, null, 2));
            await wait(seconds);
        }
        if (result.response.status === 429) throw new Error('Still limited; checkpoint preserved');
        continuation.cases.push({ id: item.id, request, http: result.response.status,
            ms: Math.round(performance.now() - started), attempts, response: result.value });
        await fs.writeFile(output, JSON.stringify(continuation, null, 2));
        console.log(JSON.stringify({ id: item.id, http: result.response.status, status: result.value.search_status }));
    }
    await verify(frozen, specBytes, spec);
    const combined = { ...first, cases: [...first.cases, ...continuation.cases],
        completedAt: new Date().toISOString(), continuation,
        codeUnchanged: true, originalUnchanged: true, specUnchanged: true,
        firstAnswersUnchanged: digest(await fs.readFile(path.join(directory, 'answers.json'))) === digest(firstBytes) };
    if (combined.cases.length !== 40 || new Set(combined.cases.map(item => item.id)).size !== 40) {
        throw new Error('Expected exactly 40 unique answers');
    }
    await fs.writeFile(path.join(directory, 'answers-final.json'), JSON.stringify(combined, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ event: 'complete', count: combined.cases.length, codeUnchanged: true, originalUnchanged: true }));
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
