# MOT Russian — staging import

Источник: [mot-rus-golosameriki-cc-by-v1](https://github.com/bltlab/mot/releases/download/v1.11/rus_golosameriki.tgz).
Лицензия: [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).
SHA-256 archive: `2587c8ab3f10290c5d81a491e7fc78f0d58d825fecb577e466c354ee8f7a2259`.
SHA-256 staging JSONL: `b7223156697cea93214ea6dd403177fc20876947909f2e040b9539be57fd2b9d`.
Members: 100752; JSON прочитано: 100747.
Записей создано: 98412; отклонено: 2339; split: {'test': 3871, 'train': 90694, 'validation': 3847}.
Достигнут выходной лимит: `True`; причины: {'credential_like': 1, 'email_like': 11, 'empty_content': 1, 'exact_duplicate': 1380, 'near_duplicate_candidate': 493, 'non_regular_member': 5, 'phone_like': 167, 'url_like': 281}.

Output остаётся staging-кандидатом. Он не меняет active manifest и перед любым promotion обязан пройти independent audit и active preflight.
