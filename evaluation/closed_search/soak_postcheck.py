"""Проверка завершённого прогона: целостность SQLite и неизменность кода."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / '.runtime/closed-search-lab-20261028'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    if directory.parent != LAB.resolve() or not directory.name.startswith('soak-http-'):
        raise ValueError('Expected a local load-test directory')
    report = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    database = directory / 'indexes/soak-local.sqlite3'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        integrity = connection.execute('PRAGMA quick_check').fetchone()[0]
        contexts = connection.execute('SELECT count(*) FROM chat_context').fetchone()[0]
        state, payload, digest = connection.execute('SELECT state,payload,digest FROM index_state WHERE singleton=1').fetchone()
    checksum = hashlib.sha256(payload.encode()).hexdigest() == digest
    persisted = json.loads(payload)
    hashes = {}
    for name, expected in report.get('engineHashes', {}).items():
        if Path(name).name != name or not name.endswith('.py'):
            raise ValueError('Invalid code hash entry')
        hashes[name] = hashlib.sha256((ROOT / 'ai_core' / name).read_bytes()).hexdigest() == expected
    samples = [sample for sample in report['samples'] if 'rssBytes' in sample]
    result = {'completed': report.get('completed') is True, 'quickCheck': integrity, 'contexts': contexts,
              'state': state, 'payloadChecksum': checksum, 'sources': len(persisted['sources']),
              'passages': len(persisted['passages']), 'unchangedCode': hashes,
              'rssMiB': [round(min(sample['rssBytes'] for sample in samples) / 1048576, 2), round(max(sample['rssBytes'] for sample in samples) / 1048576, 2)],
              'privateMiB': [round(min(sample['privateBytes'] for sample in samples) / 1048576, 2), round(max(sample['privateBytes'] for sample in samples) / 1048576, 2)],
              'sampleErrors': len(report['samples']) - len(samples)}
    result['pass'] = (result['completed'] and integrity == 'ok' and contexts <= 1000 and state == 'ready' and checksum
                      and bool(hashes) and all(hashes.values()) and report['summary']['passed'] == report['summary']['requests'])
    (directory / 'postcheck.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result))
    if not result['pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
