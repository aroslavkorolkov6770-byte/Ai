"""Простая собственная токенизация для первого учебного прототипа.

Токенизатор намеренно не использует готовые модели или внешние словари.
Он разбивает текст на слова, числа и отдельные знаки препинания, после чего
строит словарь только по нашему датасету.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable


SPECIAL_TOKENS = ("<pad>", "<unk>", "<bos>", "<eos>", "<sep>")
DEFAULT_BPE_VOCAB_SIZE = 512
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)
NO_SPACE_BEFORE = {".", ",", "!", "?", ";", ":", "%", ")", "]", "}", "»", "…"}
NO_SPACE_AFTER = {"(", "[", "{", "«"}


class WordTokenizer:
    """Словарный токенизатор с сохранением регистра и знаков препинания."""

    def __init__(self, token_to_id: dict[str, int] | None = None) -> None:
        if token_to_id is None:
            token_to_id = {
                token: index for index, token in enumerate(SPECIAL_TOKENS)
            }

        self.token_to_id = {str(token): int(index) for token, index in token_to_id.items()}
        self.id_to_token = {index: token for token, index in self.token_to_id.items()}
        self._validate_special_tokens()

    def _validate_special_tokens(self) -> None:
        """Проверяет, что служебные токены имеют стабильные идентификаторы."""
        for expected_id, token in enumerate(SPECIAL_TOKENS):
            actual_id = self.token_to_id.get(token)
            if actual_id != expected_id:
                raise ValueError(
                    f"Служебный токен {token!r} должен иметь id {expected_id}, "
                    f"получен {actual_id}."
                )

    @property
    def pad_id(self) -> int:
        return self.token_to_id["<pad>"]

    @property
    def unk_id(self) -> int:
        return self.token_to_id["<unk>"]

    @property
    def bos_id(self) -> int:
        return self.token_to_id["<bos>"]

    @property
    def eos_id(self) -> int:
        return self.token_to_id["<eos>"]

    @property
    def sep_id(self) -> int:
        return self.token_to_id["<sep>"]

    @property
    def vocab_size(self) -> int:
        return len(self.token_to_id)

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """Делит текст на слова, числа и знаки препинания."""
        if not isinstance(text, str):
            raise TypeError("Текст для токенизации должен быть строкой.")
        return TOKEN_PATTERN.findall(text.strip())

    def build(self, texts: Iterable[str], limit: int | None = None) -> None:
        """Строит словарь по переданным текстам в воспроизводимом порядке."""
        counter: Counter[str] = Counter()
        for text in texts:
            counter.update(self.tokenize(text))

        available_tokens = [
            (token, count)
            for token, count in counter.most_common()
            if token not in self.token_to_id
        ]
        if limit is not None:
            available_tokens = available_tokens[:limit]

        for token, _ in available_tokens:
            new_id = len(self.token_to_id)
            self.token_to_id[token] = new_id
            self.id_to_token[new_id] = token

    def encode(
        self,
        text: str,
        *,
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        """Преобразует текст в идентификаторы словаря."""
        tokens = self.tokenize(text)
        ids: list[int] = []
        if add_bos:
            ids.append(self.bos_id)
        ids.extend(self.token_to_id.get(token, self.unk_id) for token in tokens)
        if add_eos:
            ids.append(self.eos_id)

        if max_length is not None and len(ids) > max_length:
            ids = ids[:max_length]
            if add_eos and ids:
                ids[-1] = self.eos_id

        return ids

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = True) -> str:
        """Собирает идентификаторы обратно в читаемый текст."""
        tokens: list[str] = []
        special_set = set(SPECIAL_TOKENS)
        for raw_id in ids:
            token = self.id_to_token.get(int(raw_id), "<unk>")
            if skip_special_tokens and token in special_set:
                continue
            tokens.append(token)

        result = ""
        for token in tokens:
            if not result:
                result = token
            elif token in NO_SPACE_BEFORE or token in {"'", "’"}:
                result += token
            elif result[-1:] in {"'", "’"} or result[-1:] in NO_SPACE_AFTER:
                result += token
            else:
                result += " " + token
        return result.strip()

    def save(self, path: str | Path) -> None:
        """Сохраняет словарь в JSON для последующего использования API."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as file:
            json.dump(
                {"tokenizer_type": "word", "token_to_id": self.token_to_id},
                file,
                ensure_ascii=False,
                indent=2,
            )

    @classmethod
    def load(cls, path: str | Path) -> "WordTokenizer":
        """Загружает словарь, созданный методом :meth:`save`."""
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get("token_to_id"), dict):
            raise ValueError("Файл словаря имеет неверный формат.")
        return cls(data["token_to_id"])


class CharacterTokenizer:
    """Символьный токенизатор для маленького русскоязычного датасета.

    В отличие от словарного варианта, он умеет собирать незнакомые слова из
    отдельных символов. Это увеличивает длину последовательности, но сильно
    уменьшает словарь и снижает количество неизвестных токенов.
    """

    def __init__(self, token_to_id: dict[str, int] | None = None) -> None:
        if token_to_id is None:
            token_to_id = {
                token: index for index, token in enumerate(SPECIAL_TOKENS)
            }

        self.token_to_id = {str(token): int(index) for token, index in token_to_id.items()}
        self.id_to_token = {index: token for token, index in self.token_to_id.items()}
        self._validate_special_tokens()

    def _validate_special_tokens(self) -> None:
        """Проверяет одинаковые идентификаторы служебных токенов."""
        for expected_id, token in enumerate(SPECIAL_TOKENS):
            actual_id = self.token_to_id.get(token)
            if actual_id != expected_id:
                raise ValueError(
                    f"Служебный токен {token!r} должен иметь id {expected_id}, "
                    f"получен {actual_id}."
                )

    @property
    def pad_id(self) -> int:
        return self.token_to_id["<pad>"]

    @property
    def unk_id(self) -> int:
        return self.token_to_id["<unk>"]

    @property
    def bos_id(self) -> int:
        return self.token_to_id["<bos>"]

    @property
    def eos_id(self) -> int:
        return self.token_to_id["<eos>"]

    @property
    def sep_id(self) -> int:
        return self.token_to_id["<sep>"]

    @property
    def vocab_size(self) -> int:
        return len(self.token_to_id)

    @staticmethod
    def tokenize(text: str) -> list[str]:
        """Возвращает каждый символ, включая пробелы между словами."""
        if not isinstance(text, str):
            raise TypeError("Текст для токенизации должен быть строкой.")
        return list(text)

    def build(self, texts: Iterable[str], limit: int | None = None) -> None:
        """Строит компактный алфавит по вопросам и ответам."""
        counter: Counter[str] = Counter()
        for text in texts:
            counter.update(self.tokenize(text))

        available_tokens = [
            (token, count)
            for token, count in counter.most_common()
            if token not in self.token_to_id
        ]
        if limit is not None:
            available_tokens = available_tokens[:limit]

        for token, _ in available_tokens:
            new_id = len(self.token_to_id)
            self.token_to_id[token] = new_id
            self.id_to_token[new_id] = token

    def encode(
        self,
        text: str,
        *,
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        """Преобразует символы текста в идентификаторы."""
        ids: list[int] = []
        if add_bos:
            ids.append(self.bos_id)
        ids.extend(self.token_to_id.get(token, self.unk_id) for token in self.tokenize(text))
        if add_eos:
            ids.append(self.eos_id)

        if max_length is not None and len(ids) > max_length:
            ids = ids[:max_length]
            if add_eos and ids:
                ids[-1] = self.eos_id
        return ids

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = True) -> str:
        """Собирает символы в исходную строку."""
        special_set = set(SPECIAL_TOKENS)
        tokens: list[str] = []
        for raw_id in ids:
            token = self.id_to_token.get(int(raw_id), "<unk>")
            if skip_special_tokens and token in special_set:
                continue
            tokens.append(token)
        return "".join(tokens).strip()

    def save(self, path: str | Path) -> None:
        """Сохраняет символьный алфавит в JSON."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as file:
            json.dump(
                {"tokenizer_type": "character", "token_to_id": self.token_to_id},
                file,
                ensure_ascii=False,
                indent=2,
            )

    @classmethod
    def load(cls, path: str | Path) -> "CharacterTokenizer":
        """Загружает символьный алфавит."""
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict) or not isinstance(data.get("token_to_id"), dict):
            raise ValueError("Файл символьного словаря имеет неверный формат.")
        return cls(data["token_to_id"])


class SubwordBPETokenizer:
    """Собственный BPE-токенизатор, обучаемый только на локальных текстах.

    Сначала текст состоит из отдельных символов. Затем самые частые соседние
    пары последовательно объединяются в подслова. Это сокращает длину входа,
    но сохраняет возможность собирать редкие слова из маленьких частей.
    """

    def __init__(
        self,
        token_to_id: dict[str, int] | None = None,
        merges: Iterable[Iterable[str]] | None = None,
    ) -> None:
        if token_to_id is None:
            token_to_id = {
                token: index for index, token in enumerate(SPECIAL_TOKENS)
            }
        self.token_to_id = {
            str(token): int(index)
            for token, index in token_to_id.items()
        }
        self.id_to_token = {
            index: token for token, index in self.token_to_id.items()
        }
        self.merges: list[tuple[str, str]] = []
        if merges is not None:
            for raw_pair in merges:
                pair = tuple(str(part) for part in raw_pair)
                if len(pair) != 2 or not pair[0] or not pair[1]:
                    raise ValueError("Каждое BPE-объединение должно содержать два токена.")
                self.merges.append((pair[0], pair[1]))
        self._validate_special_tokens()

    def _validate_special_tokens(self) -> None:
        """Проверяет стабильные идентификаторы служебных токенов."""
        for expected_id, token in enumerate(SPECIAL_TOKENS):
            actual_id = self.token_to_id.get(token)
            if actual_id != expected_id:
                raise ValueError(
                    f"Служебный токен {token!r} должен иметь id {expected_id}, "
                    f"получен {actual_id}."
                )

    @property
    def pad_id(self) -> int:
        return self.token_to_id["<pad>"]

    @property
    def unk_id(self) -> int:
        return self.token_to_id["<unk>"]

    @property
    def bos_id(self) -> int:
        return self.token_to_id["<bos>"]

    @property
    def eos_id(self) -> int:
        return self.token_to_id["<eos>"]

    @property
    def sep_id(self) -> int:
        return self.token_to_id["<sep>"]

    @property
    def vocab_size(self) -> int:
        return len(self.token_to_id)

    @staticmethod
    def _merge_sequence(
        sequence: list[str],
        pair: tuple[str, str],
    ) -> list[str]:
        """Объединяет все непересекающиеся вхождения одной пары."""
        if len(sequence) < 2:
            return sequence
        merged_token = pair[0] + pair[1]
        result: list[str] = []
        index = 0
        while index < len(sequence):
            if (
                index + 1 < len(sequence)
                and sequence[index] == pair[0]
                and sequence[index + 1] == pair[1]
            ):
                result.append(merged_token)
                index += 2
            else:
                result.append(sequence[index])
                index += 1
        return result

    def _apply_merges(self, text: str) -> list[str]:
        """Применяет изученные пары в том же порядке, что при обучении."""
        sequence = list(text)
        for pair in self.merges:
            sequence = self._merge_sequence(sequence, pair)
        return sequence

    def build(
        self,
        texts: Iterable[str],
        limit: int | None = DEFAULT_BPE_VOCAB_SIZE,
        *,
        min_frequency: int = 2,
    ) -> None:
        """Обучает BPE-объединения в воспроизводимом порядке."""
        if min_frequency < 2:
            raise ValueError("min_frequency должен быть не меньше 2.")
        target_vocab_size = limit or DEFAULT_BPE_VOCAB_SIZE
        if target_vocab_size <= len(SPECIAL_TOKENS):
            raise ValueError("Размер BPE-словаря слишком мал.")

        text_list: list[str] = []
        for text in texts:
            if not isinstance(text, str):
                raise TypeError("Текст для токенизации должен быть строкой.")
            if text:
                text_list.append(text)
        if not text_list:
            raise ValueError("Для обучения BPE нужен хотя бы один непустой текст.")

        character_counts: Counter[str] = Counter(
            character
            for text in text_list
            for character in text
        )
        characters = sorted(
            character_counts,
            key=lambda character: (-character_counts[character], character),
        )
        missing_characters = [
            character
            for character in characters
            if character not in self.token_to_id
        ]
        if len(self.token_to_id) + len(missing_characters) > target_vocab_size:
            raise ValueError(
                "Размер BPE-словаря меньше числа обязательных символов датасета."
            )
        for character in missing_characters:
            new_id = len(self.token_to_id)
            self.token_to_id[character] = new_id
            self.id_to_token[new_id] = character

        sequences = [self._apply_merges(text) for text in text_list]
        while len(self.token_to_id) < target_vocab_size:
            pair_counts: Counter[tuple[str, str]] = Counter()
            for sequence in sequences:
                pair_counts.update(zip(sequence, sequence[1:]))

            candidates = [
                pair
                for pair, frequency in pair_counts.items()
                if frequency >= min_frequency
                and pair[0] + pair[1] not in self.token_to_id
                and pair[0] + pair[1] not in SPECIAL_TOKENS
            ]
            if not candidates:
                break
            best_pair = min(
                candidates,
                key=lambda pair: (
                    -pair_counts[pair],
                    pair[0],
                    pair[1],
                ),
            )
            merged_token = best_pair[0] + best_pair[1]
            self.merges.append(best_pair)
            new_id = len(self.token_to_id)
            self.token_to_id[merged_token] = new_id
            self.id_to_token[new_id] = merged_token
            sequences = [
                self._merge_sequence(sequence, best_pair)
                for sequence in sequences
            ]

    def encode(
        self,
        text: str,
        *,
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        """Преобразует текст в идентификаторы символов и подслов."""
        if not isinstance(text, str):
            raise TypeError("Текст для токенизации должен быть строкой.")
        ids: list[int] = []
        if add_bos:
            ids.append(self.bos_id)
        ids.extend(
            self.token_to_id.get(token, self.unk_id)
            for token in self._apply_merges(text)
        )
        if add_eos:
            ids.append(self.eos_id)
        if max_length is not None and len(ids) > max_length:
            ids = ids[:max_length]
            if add_eos and ids:
                ids[-1] = self.eos_id
        return ids

    def decode(self, ids: Iterable[int], *, skip_special_tokens: bool = True) -> str:
        """Склеивает подслова обратно в строку без потери пробелов."""
        special_set = set(SPECIAL_TOKENS)
        tokens: list[str] = []
        for raw_id in ids:
            token = self.id_to_token.get(int(raw_id), "<unk>")
            if skip_special_tokens and token in special_set:
                continue
            tokens.append(token)
        return "".join(tokens).strip()

    def save(self, path: str | Path) -> None:
        """Сохраняет BPE-словарь и порядок объединений в JSON."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as file:
            json.dump(
                {
                    "tokenizer_type": "subword-bpe",
                    "token_to_id": self.token_to_id,
                    "merges": [list(pair) for pair in self.merges],
                },
                file,
                ensure_ascii=False,
                indent=2,
            )

    @classmethod
    def load(cls, path: str | Path) -> "SubwordBPETokenizer":
        """Загружает собственный BPE-словарь и список объединений."""
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("token_to_id"), dict)
            or not isinstance(data.get("merges"), list)
        ):
            raise ValueError("Файл BPE-словаря имеет неверный формат.")
        return cls(data["token_to_id"], data["merges"])


def load_tokenizer(
    path: str | Path,
) -> WordTokenizer | CharacterTokenizer | SubwordBPETokenizer:
    """Загружает нужный тип токенизатора по метаданным файла."""
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    tokenizer_type = data.get("tokenizer_type", "word")
    if tokenizer_type == "character":
        return CharacterTokenizer(data["token_to_id"])
    if tokenizer_type == "word":
        return WordTokenizer(data["token_to_id"])
    if tokenizer_type == "subword-bpe":
        return SubwordBPETokenizer(data["token_to_id"], data.get("merges", []))
    raise ValueError(f"Неизвестный тип токенизатора: {tokenizer_type!r}.")
