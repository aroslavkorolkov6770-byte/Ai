/* Готовность означает работу маршрутов, а не только открытый TCP-порт. */
const origin = 'http://127.0.0.1:3040';
const checks = [
    ['/api/auth/session', undefined],
    ['/api/storage?key=tea_hub_urgent_files_v1', undefined],
    ['/api/ai-chat', { messages: [{ role: 'user', content: 'Проверка готовности' }] }],
];

async function main() {
    const deadline = Date.now() + 90000;
    let last = 'not_started';
    while (Date.now() < deadline) {
        try {
            let ready = true;
            for (const [route, body] of checks) {
                const response = await fetch(origin + route, {
                    method: body ? 'POST' : 'GET', headers: { Origin: origin, 'Content-Type': 'application/json' },
                    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(15000),
                });
                last = route + ':' + response.status;
                if (response.status !== 401 || !response.headers.get('content-type')?.includes('application/json')) {
                    ready = false;
                    break;
                }
                const payload = await response.json();
                if (!payload || typeof payload !== 'object') {
                    ready = false;
                    break;
                }
            }
            if (ready) {
                console.log('Маршруты входа, материалов и чата готовы; запросы без сессии отклоняются.');
                return;
            }
        } catch (error) {
            last = error.name;
        }
        await new Promise(resolve => setTimeout(resolve, 2000));
    }
    throw new Error('Сайт не готов: ' + last + '. Проверьте site.stderr.log тестового стенда.');
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
