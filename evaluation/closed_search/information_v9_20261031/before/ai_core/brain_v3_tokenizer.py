"""Собственные subword-токенизаторы для будущего Brain v3.

Модуль не использует готовые словари, веса или внешние tokenizers. Он строит
свой словарь только на разрешённой train-части корпуса и сохраняет отдельный
версионируемый JSON-артефакт. Валидация и test никогда не участвуют в build.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Iterable


FORMAT_VERSION = 1
SPECIAL_TOKENS = (
    "<pad>",
    "<unk>",
    "<bos>",
    "<eos>",
    "<sep>",
    "<role_user>",
    "<role_assistant>",
    "<end_turn>",
    "<memory>",
    "<tool>",
    "<tool_result>",
)
ROLE_TOKENS = {
    "user": "<role_user>",
    "assistant": "<role_assistant>",
}
BYTE_TOKEN_PREFIX = "<byte:"
BYTE_TOKENS = tuple(f"<byte:{value:02x}>" for value in range(256))
SEGMENT_PATTERN = re.compile(r"\s+|\w+|[^\w\s]", flags=re.UNICODE)
WORD_PATTERN = re.compile(r"\w+\Z", flags=re.UNICODE)


def _base_token_mapping() -> dict[str, int]:
    """Возвращает стабильные id служебных и byte-fallback токенов."""
    mapping = {
        token: index
        for index, token in enumerate(SPECIAL_TOKENS)
    }
    for byte_token in BYTE_TOKENS:
        mapping[byte_token] = len(mapping)
    return mapping


def _is_word(segment: str) -> bool:
    """Отделяет слова и числа от пробелов и пунктуации без потери символов."""
    return WORD_PATTERN.fullmatch(segment) is not None


def _merge_sequence(sequence: tuple[str, ...], pair: tuple[str, str]) -> tuple[str, ...]:
    """Объединяет непересекающиеся вхождения BPE-пары."""
    if len(sequence) < 2:
        return sequence
    merged = pair[0] + pair[1]
    result: list[str] = []
    index = 0
    while index < len(sequence):
        if (
            index + 1 < len(sequence)
            and sequence[index] == pair[0]
            and sequence[index + 1] == pair[1]
        ):
            result.append(merged)
            index += 2
        else:
            result.append(sequence[index])
            index += 1
    return tuple(result)


def _training_statistics(texts: Iterable[str]) -> tuple[Counter[str], Counter[str]]:
    """Считает символы и слова, не сохраняя полные исходные документы."""
    character_counts: Counter[str] = Counter()
    word_counts: Counter[str] = Counter()
    has_text = False
    for text in texts:
        if not isinstance(text, str):
            raise TypeError("Текст корпуса для токенизатора должен быть строкой.")
        if not text:
            continue
        has_text = True
        character_counts.update(text)
        for segment in SEGMENT_PATTERN.findall(text):
            if _is_word(segment):
                word_counts[segment] += 1
    if not has_text:
        raise ValueError("Для обучения токенизатора нужен хотя бы один непустой текст.")
    return character_counts, word_counts


class BrainV3Tokenizer:
    """Общая безопасная основа собственных токенизаторов Brain v3.

    Пользовательский текст всегда разбирается как обычные символы. Даже если
    пользователь напечатает строку `<role_user>`, она не превратится в служебную
    команду. Управляющие id добавляет только ``encode_dialogue``.
    """

    tokenizer_type = "brain-v3-base"

    def __init__(self, token_to_id: dict[str, int] | None = None) -> None:
        self.token_to_id = {
            str(token): int(identifier)
            for token, identifier in (token_to_id or _base_token_mapping()).items()
        }
        self.id_to_token = {
            identifier: token
            for token, identifier in self.token_to_id.items()
        }
        self._byte_values = {
            byte_token: value
            for value, byte_token in enumerate(BYTE_TOKENS)
        }
        self._word_cache: dict[str, tuple[str, ...]] = {}
        self._validate_mapping()

    def _validate_mapping(self) -> None:
        """Проверяет стабильность служебных id и отсутствие коллизий словаря."""
        if len(self.id_to_token) != len(self.token_to_id):
            raise ValueError("В словаре токенизатора есть повторяющиеся идентификаторы.")
        for expected_id, token in enumerate(SPECIAL_TOKENS):
            if self.token_to_id.get(token) != expected_id:
                raise ValueError(f"Служебный токен {token!r} имеет нестабильный id.")
        byte_offset = len(SPECIAL_TOKENS)
        for value, byte_token in enumerate(BYTE_TOKENS):
            if self.token_to_id.get(byte_token) != byte_offset + value:
                raise ValueError("Byte-fallback токены имеют нестабильные id.")

    @property
    def vocab_size(self) -> int:
        """Возвращает фактический размер словаря."""
        return len(self.token_to_id)

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
    def end_turn_id(self) -> int:
        return self.token_to_id["<end_turn>"]

    def control_id(self, token: str) -> int:
        """Возвращает id только известного внутреннего управляющего токена."""
        if token not in SPECIAL_TOKENS:
            raise ValueError("Неизвестный служебный токен Brain v3.")
        return self.token_to_id[token]

    def _reset_to_base(self) -> None:
        self.token_to_id = _base_token_mapping()
        self.id_to_token = {
            identifier: token
            for token, identifier in self.token_to_id.items()
        }
        self._word_cache.clear()

    def _register_token(self, token: str) -> None:
        if not token or token in self.token_to_id:
            return
        self.token_to_id[token] = len(self.token_to_id)
        self.id_to_token[len(self.id_to_token)] = token

    def _prepare_base_vocabulary(
        self,
        character_counts: Counter[str],
        vocabulary_size: int,
    ) -> None:
        """Добавляет весь алфавит train без чтения validation/test."""
        self._reset_to_base()
        characters = sorted(
            character_counts,
            key=lambda character: (-character_counts[character], character),
        )
        required_size = len(self.token_to_id) + len(characters)
        if vocabulary_size < required_size:
            raise ValueError(
                "Размер словаря меньше обязательных служебных, byte и train-символов."
            )
        for character in characters:
            self._register_token(character)

    def _byte_fallback(self, character: str) -> tuple[str, ...]:
        """Кодирует незнакомый Unicode-символ в UTF-8 байты без потери текста."""
        return tuple(
            BYTE_TOKENS[value]
            for value in character.encode("utf-8")
        )

    def _segment_word(self, word: str) -> tuple[str, ...]:
        """Переопределяется BPE и Unigram-подобным сегментатором."""
        raise NotImplementedError

    def tokenize(self, text: str) -> list[str]:
        """Разбивает текст без неявного распознавания служебных команд."""
        if not isinstance(text, str):
            raise TypeError("Текст для токенизации должен быть строкой.")
        tokens: list[str] = []
        for segment in SEGMENT_PATTERN.findall(text):
            if _is_word(segment):
                tokens.extend(self._segment_word(segment))
                continue
            for character in segment:
                if character in self.token_to_id:
                    tokens.append(character)
                else:
                    tokens.extend(self._byte_fallback(character))
        return tokens

    def encode(
        self,
        text: str,
        *,
        add_bos: bool = False,
        add_eos: bool = False,
        max_length: int | None = None,
    ) -> list[int]:
        """Преобразует текст в id и при необходимости добавляет границы фразы."""
        identifiers: list[int] = []
        if add_bos:
            identifiers.append(self.bos_id)
        identifiers.extend(
            self.token_to_id.get(token, self.unk_id)
            for token in self.tokenize(text)
        )
        if add_eos:
            identifiers.append(self.eos_id)
        if max_length is not None and len(identifiers) > max_length:
            identifiers = identifiers[:max_length]
            if add_eos and identifiers:
                identifiers[-1] = self.eos_id
        return identifiers

    def encode_dialogue(
        self,
        turns: Iterable[dict[str, str]],
        *,
        add_bos: bool = True,
        add_eos: bool = True,
        max_length: int | None = None,
    ) -> list[int]:
        """Кодирует размеченный диалог, добавляя роли только внутри программы."""
        identifiers: list[int] = [self.bos_id] if add_bos else []
        for turn in turns:
            if not isinstance(turn, dict):
                raise ValueError("Ход диалога должен быть объектом role/text.")
            role = turn.get("role")
            text = turn.get("text")
            role_token = ROLE_TOKENS.get(role)
            if role_token is None or not isinstance(text, str):
                raise ValueError("Ход диалога должен содержать разрешённую роль и текст.")
            identifiers.append(self.control_id(role_token))
            identifiers.extend(self.encode(text))
            identifiers.append(self.end_turn_id)
        if add_eos:
            identifiers.append(self.eos_id)
        if max_length is not None and len(identifiers) > max_length:
            identifiers = identifiers[:max_length]
            if add_eos and identifiers:
                identifiers[-1] = self.eos_id
        return identifiers

    def decode(self, identifiers: Iterable[int], *, skip_special_tokens: bool = True) -> str:
        """Собирает строку и восстанавливает byte-fallback без потери Unicode."""
        parts: list[str] = []
        byte_buffer = bytearray()

        def flush_bytes() -> None:
            if byte_buffer:
                parts.append(bytes(byte_buffer).decode("utf-8", errors="replace"))
                byte_buffer.clear()

        for raw_identifier in identifiers:
            token = self.id_to_token.get(int(raw_identifier), "<unk>")
            byte_value = self._byte_values.get(token)
            if byte_value is not None:
                byte_buffer.append(byte_value)
                continue
            flush_bytes()
            if token in SPECIAL_TOKENS:
                if not skip_special_tokens:
                    parts.append(token)
                continue
            parts.append(token)
        flush_bytes()
        return "".join(parts)

    def _base_payload(self) -> dict[str, object]:
        return {
            "format_version": FORMAT_VERSION,
            "tokenizer_type": self.tokenizer_type,
            "token_to_id": self.token_to_id,
        }

    def save(self, path: str | Path) -> None:
        """Сохраняет отдельный токенизатор в неизменяемый JSON-артефакт."""
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as file:
            json.dump(
                self._payload(),
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

    def _payload(self) -> dict[str, object]:
        return self._base_payload()


class BrainV3BPETokenizer(BrainV3Tokenizer):
    """Собственный BPE с границами слов и byte-fallback для Unicode."""

    tokenizer_type = "brain-v3-bpe"

    def __init__(
        self,
        token_to_id: dict[str, int] | None = None,
        merges: Iterable[Iterable[str]] | None = None,
    ) -> None:
        super().__init__(token_to_id)
        self.merges: list[tuple[str, str]] = []
        if merges is not None:
            for raw_pair in merges:
                pair = tuple(str(part) for part in raw_pair)
                if len(pair) != 2 or not pair[0] or not pair[1]:
                    raise ValueError("Каждое BPE-объединение должно иметь две части.")
                if pair[0] + pair[1] not in self.token_to_id:
                    raise ValueError("BPE-словарь не содержит результат объединения.")
                self.merges.append((pair[0], pair[1]))

    def build(
        self,
        texts: Iterable[str],
        *,
        vocabulary_size: int = 512,
        min_frequency: int = 2,
        max_piece_characters: int = 32,
    ) -> None:
        """Строит BPE только из train-текстов в воспроизводимом порядке."""
        if min_frequency < 2:
            raise ValueError("min_frequency BPE должен быть не меньше 2.")
        if max_piece_characters < 2:
            raise ValueError("max_piece_characters BPE должен быть не меньше 2.")
        character_counts, word_counts = _training_statistics(texts)
        self._prepare_base_vocabulary(character_counts, vocabulary_size)
        self.merges = []
        sequences: Counter[tuple[str, ...]] = Counter(
            {
                tuple(word): count
                for word, count in word_counts.items()
            }
        )
        while len(self.token_to_id) < vocabulary_size:
            pair_counts: Counter[tuple[str, str]] = Counter()
            for sequence, frequency in sequences.items():
                for pair in zip(sequence, sequence[1:]):
                    pair_counts[pair] += frequency
            candidates = [
                pair
                for pair, frequency in pair_counts.items()
                if frequency >= min_frequency
                and len(pair[0] + pair[1]) <= max_piece_characters
                and pair[0] + pair[1] not in self.token_to_id
                and pair[0] + pair[1] not in SPECIAL_TOKENS
                and pair[0] + pair[1] not in self._byte_values
            ]
            if not candidates:
                break
            best_pair = min(
                candidates,
                key=lambda pair: (-pair_counts[pair], pair[0], pair[1]),
            )
            self.merges.append(best_pair)
            self._register_token(best_pair[0] + best_pair[1])
            updated_sequences: Counter[tuple[str, ...]] = Counter()
            for sequence, frequency in sequences.items():
                updated_sequences[_merge_sequence(sequence, best_pair)] += frequency
            sequences = updated_sequences
        self._word_cache.clear()

    def _segment_word(self, word: str) -> tuple[str, ...]:
        cached = self._word_cache.get(word)
        if cached is not None:
            return cached
        sequence = tuple(word)
        for pair in self.merges:
            sequence = _merge_sequence(sequence, pair)
        tokens: list[str] = []
        for piece in sequence:
            if piece in self.token_to_id:
                tokens.append(piece)
            else:
                # Вход может содержать символ, которого не было в train.
                # Он сохраняется byte-fallback, а не превращается в <unk>.
                tokens.extend(self._byte_fallback(piece))
        result = tuple(tokens)
        self._word_cache[word] = result
        return result

    def _payload(self) -> dict[str, object]:
        payload = self._base_payload()
        payload["merges"] = [list(pair) for pair in self.merges]
        return payload


class BrainV3UnigramTokenizer(BrainV3Tokenizer):
    """Небольшой собственный Unigram-подобный сегментатор с Viterbi-разбором.

    Это не сторонняя библиотека и не готовый словарь. Кандидаты подслов
    собираются из train-слов, получают вероятностный score, а лучший разбор
    каждого нового слова выбирается динамическим программированием.
    """

    tokenizer_type = "brain-v3-unigram"

    def __init__(
        self,
        token_to_id: dict[str, int] | None = None,
        piece_scores: dict[str, float] | None = None,
    ) -> None:
        super().__init__(token_to_id)
        self.piece_scores = {
            str(piece): float(score)
            for piece, score in (piece_scores or {}).items()
        }
        for piece in self.piece_scores:
            if piece not in self.token_to_id:
                raise ValueError("Unigram score ссылается на отсутствующий токен.")
        self._max_piece_characters = max(
            (len(piece) for piece in self.piece_scores),
            default=1,
        )

    def build(
        self,
        texts: Iterable[str],
        *,
        vocabulary_size: int = 512,
        min_frequency: int = 2,
        max_piece_characters: int = 12,
    ) -> None:
        """Строит частотный Unigram-подобный словарь только на train-текстах."""
        if min_frequency < 2:
            raise ValueError("min_frequency Unigram должен быть не меньше 2.")
        if max_piece_characters < 2:
            raise ValueError("max_piece_characters Unigram должен быть не меньше 2.")
        character_counts, word_counts = _training_statistics(texts)
        self._prepare_base_vocabulary(character_counts, vocabulary_size)
        candidate_counts: Counter[str] = Counter()
        for word, frequency in word_counts.items():
            word_length = len(word)
            for start in range(word_length):
                maximum_end = min(word_length, start + max_piece_characters)
                for end in range(start + 2, maximum_end + 1):
                    candidate_counts[word[start:end]] += frequency
        remaining = vocabulary_size - len(self.token_to_id)
        candidates = [
            piece
            for piece, frequency in candidate_counts.items()
            if frequency >= min_frequency and piece not in self.token_to_id
        ]
        candidates.sort(
            key=lambda piece: (
                -(candidate_counts[piece] * len(piece)),
                -len(piece),
                piece,
            )
        )
        for piece in candidates[:remaining]:
            self._register_token(piece)

        token_counts: Counter[str] = Counter(character_counts)
        for piece in candidates[:remaining]:
            token_counts[piece] = candidate_counts[piece]
        normalizer = sum(token_counts.values()) + len(token_counts)
        self.piece_scores = {
            token: math.log((count + 1) / normalizer)
            for token, count in token_counts.items()
            if token in self.token_to_id
        }
        self._max_piece_characters = max(
            (len(piece) for piece in self.piece_scores),
            default=1,
        )
        self._word_cache.clear()

    def _segment_word(self, word: str) -> tuple[str, ...]:
        cached = self._word_cache.get(word)
        if cached is not None:
            return cached
        # При совершенно новом Unicode-символе byte-fallback важнее сжатия:
        # так текст восстанавливается точно и не появляется <unk>.
        if any(character not in self.token_to_id for character in word):
            result = tuple(
                token
                for character in word
                for token in (
                    (character,)
                    if character in self.token_to_id
                    else self._byte_fallback(character)
                )
            )
            self._word_cache[word] = result
            return result

        length = len(word)
        scores = [-math.inf] * (length + 1)
        previous = [-1] * (length + 1)
        scores[0] = 0.0
        for start in range(length):
            if not math.isfinite(scores[start]):
                continue
            maximum_end = min(length, start + self._max_piece_characters)
            for end in range(start + 1, maximum_end + 1):
                piece = word[start:end]
                piece_score = self.piece_scores.get(piece)
                if piece_score is None:
                    continue
                candidate_score = scores[start] + piece_score
                if candidate_score > scores[end]:
                    scores[end] = candidate_score
                    previous[end] = start
        if not math.isfinite(scores[length]):
            result = tuple(word)
            self._word_cache[word] = result
            return result

        result_reversed: list[str] = []
        end = length
        while end > 0:
            start = previous[end]
            if start < 0:
                raise RuntimeError("Unigram-сегментатор не смог восстановить путь.")
            result_reversed.append(word[start:end])
            end = start
        result = tuple(reversed(result_reversed))
        self._word_cache[word] = result
        return result

    def _payload(self) -> dict[str, object]:
        payload = self._base_payload()
        payload["piece_scores"] = self.piece_scores
        return payload


def load_brain_v3_tokenizer(path: str | Path) -> BrainV3Tokenizer:
    """Загружает ровно один собственный Brain v3 tokenizer по типу JSON."""
    with Path(path).open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
        raise ValueError("Файл Brain v3 tokenizer имеет неверную версию.")
    token_to_id = payload.get("token_to_id")
    if not isinstance(token_to_id, dict):
        raise ValueError("Файл Brain v3 tokenizer не содержит token_to_id.")
    tokenizer_type = payload.get("tokenizer_type")
    if tokenizer_type == BrainV3BPETokenizer.tokenizer_type:
        merges = payload.get("merges")
        if not isinstance(merges, list):
            raise ValueError("BPE tokenizer не содержит список merges.")
        return BrainV3BPETokenizer(token_to_id, merges)
    if tokenizer_type == BrainV3UnigramTokenizer.tokenizer_type:
        piece_scores = payload.get("piece_scores")
        if not isinstance(piece_scores, dict):
            raise ValueError("Unigram tokenizer не содержит piece_scores.")
        return BrainV3UnigramTokenizer(token_to_id, piece_scores)
    raise ValueError("Неизвестный тип Brain v3 tokenizer.")


def tokenizer_file_sha256(path: str | Path) -> str:
    """Возвращает SHA-256 артефакта для совместимости будущего checkpoint."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
