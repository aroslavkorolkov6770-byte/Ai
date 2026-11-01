# Полные листинги проверки обучения — 31.10.2026

Все четыре файла приведены полностью. Активные веса не изменены.

## ai_core/train.py

````python
"""Обучение маленькой собственной языковой модели на JSONL-датасете."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import sys
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

try:
    from .dataset_tools import (
        DatasetSplit,
        dataset_fingerprint,
        prompt_overlap,
        split_dataset,
    )
    from .data_quality import TRAINING_TOPIC_CHOICES, filter_pairs_by_topic
    from .dialogue_data import (
        dialogue_dataset_fingerprint,
        dialogue_examples_to_pairs,
        load_dialogue_examples,
    )
    from .training_corpus import (
        DATASET_PATH,
        WEB_DIALOGUE_DATASET_PATH,
        WEB_DIALOGUE_EXTENDED_DATASET_PATH,
        WEB_FOUNDATION_DATASET_PATH,
        load_primary_training_pairs,
        load_training_pairs_for_topic,
        load_web_foundation_pairs,
    )
    from .model import (
        TinyGRUAttentionSeq2Seq,
        TinySeq2SeqTransformer,
        TinyTransformer,
    )
    from .tokenizer import (
        SPECIAL_TOKENS,
        CharacterTokenizer,
        SubwordBPETokenizer,
    )
    from .training_data import TrainingPair
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_core.dataset_tools import (
        DatasetSplit,
        dataset_fingerprint,
        prompt_overlap,
        split_dataset,
    )
    from ai_core.data_quality import TRAINING_TOPIC_CHOICES, filter_pairs_by_topic
    from ai_core.dialogue_data import (
        dialogue_dataset_fingerprint,
        dialogue_examples_to_pairs,
        load_dialogue_examples,
    )
    from ai_core.training_corpus import (
        DATASET_PATH,
        WEB_DIALOGUE_DATASET_PATH,
        WEB_DIALOGUE_EXTENDED_DATASET_PATH,
        WEB_FOUNDATION_DATASET_PATH,
        load_primary_training_pairs,
        load_training_pairs_for_topic,
        load_web_foundation_pairs,
    )
    from ai_core.model import (
        TinyGRUAttentionSeq2Seq,
        TinySeq2SeqTransformer,
        TinyTransformer,
    )
    from ai_core.tokenizer import (
        SPECIAL_TOKENS,
        CharacterTokenizer,
        SubwordBPETokenizer,
    )
    from ai_core.training_data import TrainingPair


PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Импорт после блока совместимости поддерживает и `python -m`, и прямой запуск.
from ai_core.training_preflight import (
    analyze_training_preflight,
    make_run_directory,
    require_valid_preflight,
)

DIALOGUE_DATASET_PATH = PROJECT_ROOT / "training_data" / "dialogue_examples.jsonl"
ARTIFACTS_DIR = PROJECT_ROOT / "model_artifacts"
CHECKPOINT_PATH = ARTIFACTS_DIR / "tiny_transformer.pt"
TOKENIZER_PATH = ARTIFACTS_DIR / "tokenizer.json"
METRICS_PATH = ARTIFACTS_DIR / "training_metrics.json"

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
DEFAULT_MAX_LEN = 192
DEFAULT_EMBED_DIM = 128
DEFAULT_HEADS = 4
DEFAULT_LAYERS = 2
DEFAULT_DROPOUT = 0.1
DEFAULT_SEED = 42
DEFAULT_ARCHITECTURE = "transformer"
DEFAULT_TOKENIZER_TYPE = "character"
DEFAULT_TOKENIZER_VOCAB_SIZE = 512
DEFAULT_TRAINING_TOPIC = "all"
DEFAULT_MODEL_VERSION = "tiny-transformer-seq2seq-v0.4-dialogue"

TrainingTokenizer = CharacterTokenizer | SubwordBPETokenizer


class LanguageModelDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Готовит контекст вопроса и целевые токены ответа."""

    def __init__(
        self,
        pairs: list[TrainingPair],
        tokenizer: TrainingTokenizer,
        max_len: int,
    ) -> None:
        self.examples: list[tuple[torch.Tensor, torch.Tensor]] = []
        for pair in pairs:
            input_ids, labels = self._encode_pair(pair, tokenizer, max_len)
            self.examples.append(
                (
                    torch.tensor(input_ids, dtype=torch.long),
                    torch.tensor(labels, dtype=torch.long),
                )
            )

    @staticmethod
    def _encode_pair(
        pair: TrainingPair,
        tokenizer: TrainingTokenizer,
        max_len: int,
    ) -> tuple[list[int], list[int]]:
        """Строит последовательность BOS + вопрос + SEP + ответ + EOS."""
        prompt_ids = tokenizer.encode(pair.prompt)
        completion_ids = tokenizer.encode(pair.completion)

        # Оставляем место под BOS, SEP и EOS. При переполнении сначала
        # уменьшаем ответ, а затем вопрос, чтобы контекст SEP сохранился.
        max_content_len = max_len - 3
        if max_content_len < 2:
            raise ValueError("max_len должен быть не меньше 5.")
        if len(prompt_ids) + len(completion_ids) > max_content_len:
            completion_limit = max(1, max_content_len - len(prompt_ids))
            completion_ids = completion_ids[:completion_limit]
        if len(prompt_ids) + len(completion_ids) > max_content_len:
            prompt_limit = max(1, max_content_len - len(completion_ids))
            prompt_ids = prompt_ids[:prompt_limit]

        sequence = [tokenizer.bos_id]
        sequence.extend(prompt_ids)
        separator_position = len(sequence)
        sequence.append(tokenizer.sep_id)
        sequence.extend(completion_ids)
        sequence.append(tokenizer.eos_id)

        input_ids = sequence[:-1]
        labels = [-100] * len(input_ids)
        # Модель получает вопрос как контекст, но ошибка считается только
        # для токенов ответа и EOS.
        for position in range(separator_position, len(input_ids)):
            labels[position] = sequence[position + 1]
        return input_ids, labels

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.examples[index]


class QuestionAnswerDataset(
    Dataset[tuple[torch.Tensor, torch.Tensor, torch.Tensor]]
):
    """Готовит отдельные входы кодировщика и цели декодировщика."""

    def __init__(
        self,
        pairs: list[TrainingPair],
        tokenizer: TrainingTokenizer,
        max_len: int,
    ) -> None:
        self.examples: list[
            tuple[torch.Tensor, torch.Tensor, torch.Tensor]
        ] = []
        for pair in pairs:
            encoder_ids, decoder_ids, labels = self._encode_pair(
                pair,
                tokenizer,
                max_len,
            )
            self.examples.append(
                (
                    torch.tensor(encoder_ids, dtype=torch.long),
                    torch.tensor(decoder_ids, dtype=torch.long),
                    torch.tensor(labels, dtype=torch.long),
                )
            )

    @staticmethod
    def _encode_pair(
        pair: TrainingPair,
        tokenizer: TrainingTokenizer,
        max_len: int,
    ) -> tuple[list[int], list[int], list[int]]:
        """Формирует вход вопроса и сдвинутую последовательность ответа."""
        encoder_ids = tokenizer.encode(
            pair.prompt,
            add_bos=True,
            max_length=max(2, max_len - 1),
        )
        encoder_ids.append(tokenizer.sep_id)
        if len(encoder_ids) > max_len:
            encoder_ids = encoder_ids[:max_len]

        target_ids = tokenizer.encode(
            pair.completion,
            add_bos=True,
            add_eos=True,
            max_length=max_len,
        )
        decoder_ids = target_ids[:-1]
        labels = target_ids[1:]
        return encoder_ids, decoder_ids, labels

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(
        self,
        index: int,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        return self.examples[index]


def collate_batch(
    batch: list[tuple[torch.Tensor, torch.Tensor]],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Дополняет короткие последовательности PAD-токенами."""
    max_length = max(input_ids.size(0) for input_ids, _ in batch)
    input_batch = torch.full(
        (len(batch), max_length),
        fill_value=0,
        dtype=torch.long,
    )
    label_batch = torch.full(
        (len(batch), max_length),
        fill_value=-100,
        dtype=torch.long,
    )
    for row, (input_ids, labels) in enumerate(batch):
        length = input_ids.size(0)
        input_batch[row, :length] = input_ids
        label_batch[row, :length] = labels
    padding_mask = input_batch.eq(0)
    return input_batch, label_batch, padding_mask


def collate_seq2seq_batch(
    batch: list[tuple[torch.Tensor, torch.Tensor, torch.Tensor]],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Дополняет вопрос и ответ раздельно, сохраняя маску вопроса."""
    max_encoder_length = max(item[0].size(0) for item in batch)
    max_decoder_length = max(item[1].size(0) for item in batch)
    encoder_batch = torch.full(
        (len(batch), max_encoder_length),
        fill_value=0,
        dtype=torch.long,
    )
    decoder_batch = torch.full(
        (len(batch), max_decoder_length),
        fill_value=0,
        dtype=torch.long,
    )
    label_batch = torch.full(
        (len(batch), max_decoder_length),
        fill_value=-100,
        dtype=torch.long,
    )
    for row, (encoder_ids, decoder_ids, labels) in enumerate(batch):
        encoder_batch[row, :encoder_ids.size(0)] = encoder_ids
        decoder_batch[row, :decoder_ids.size(0)] = decoder_ids
        label_batch[row, :labels.size(0)] = labels
    encoder_padding_mask = encoder_batch.eq(0)
    return encoder_batch, decoder_batch, label_batch, encoder_padding_mask


def build_tokenizer(
    pairs: list[TrainingPair],
    *,
    tokenizer_type: str = DEFAULT_TOKENIZER_TYPE,
    tokenizer_vocab_size: int = DEFAULT_TOKENIZER_VOCAB_SIZE,
) -> TrainingTokenizer:
    """Строит выбранный собственный токенизатор только по train-части."""
    normalized_type = tokenizer_type.strip().lower()
    if normalized_type == "bpe":
        tokenizer: TrainingTokenizer = SubwordBPETokenizer()
    elif normalized_type == "character":
        tokenizer = CharacterTokenizer()
    else:
        raise ValueError("tokenizer_type должен быть character или bpe.")
    all_texts = [text for pair in pairs for text in (pair.prompt, pair.completion)]
    if isinstance(tokenizer, SubwordBPETokenizer):
        tokenizer.build(all_texts, limit=tokenizer_vocab_size)
    else:
        tokenizer.build(all_texts)
    return tokenizer


def split_pairs(
    pairs: list[TrainingPair],
    validation_ratio: float,
    seed: int,
) -> tuple[list[TrainingPair], list[TrainingPair]]:
    """Сохраняет старый интерфейс, используя безопасное разделение данных."""
    split = split_dataset(
        pairs,
        validation_ratio=validation_ratio,
        test_ratio=0.0,
        seed=seed,
    )
    return split.train, split.validation


def evaluate(
    model: TinyTransformer,
    loader: DataLoader,
    criterion: nn.Module,
) -> dict[str, float]:
    """Считает ошибку и посимвольную точность на отложенной части."""
    model.eval()
    total_loss = 0.0
    batches = 0
    correct_tokens = 0
    total_target_tokens = 0
    with torch.no_grad():
        for input_ids, labels, padding_mask in loader:
            input_ids = input_ids.to(DEVICE)
            labels = labels.to(DEVICE)
            padding_mask = padding_mask.to(DEVICE)
            logits = model(input_ids, attention_mask=padding_mask)
            loss = criterion(logits.reshape(-1, logits.size(-1)), labels.reshape(-1))
            total_loss += float(loss.item())
            batches += 1
            predictions = logits.argmax(dim=-1)
            target_mask = labels.ne(-100)
            correct_tokens += int((predictions[target_mask] == labels[target_mask]).sum().item())
            total_target_tokens += int(target_mask.sum().item())
    return {
        "loss": total_loss / max(1, batches),
        "token_accuracy": correct_tokens / max(1, total_target_tokens),
    }


def evaluate_seq2seq(
    model: TinySeq2SeqTransformer | TinyGRUAttentionSeq2Seq,
    loader: DataLoader,
    criterion: nn.Module,
) -> dict[str, float]:
    """Считает loss и точность символов для encoder-decoder модели."""
    model.eval()
    total_loss = 0.0
    batches = 0
    correct_tokens = 0
    total_target_tokens = 0
    with torch.no_grad():
        for encoder_ids, decoder_ids, labels, encoder_padding_mask in loader:
            encoder_ids = encoder_ids.to(DEVICE)
            decoder_ids = decoder_ids.to(DEVICE)
            labels = labels.to(DEVICE)
            encoder_padding_mask = encoder_padding_mask.to(DEVICE)
            logits = model(
                encoder_ids,
                decoder_ids,
                encoder_attention_mask=encoder_padding_mask,
            )
            loss = criterion(logits.reshape(-1, logits.size(-1)), labels.reshape(-1))
            total_loss += float(loss.item())
            batches += 1
            predictions = logits.argmax(dim=-1)
            target_mask = labels.ne(-100)
            correct_tokens += int(
                (predictions[target_mask] == labels[target_mask]).sum().item()
            )
            total_target_tokens += int(target_mask.sum().item())
    return {
        "loss": total_loss / max(1, batches),
        "token_accuracy": correct_tokens / max(1, total_target_tokens),
    }


def train_model(
    *,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    max_len: int,
    validation_ratio: float,
    test_ratio: float,
    seed: int,
    dropout: float = DEFAULT_DROPOUT,
    embed_dim: int = DEFAULT_EMBED_DIM,
    n_layers: int = DEFAULT_LAYERS,
    architecture: str = DEFAULT_ARCHITECTURE,
    tokenizer_type: str = DEFAULT_TOKENIZER_TYPE,
    tokenizer_vocab_size: int = DEFAULT_TOKENIZER_VOCAB_SIZE,
    training_topic: str = DEFAULT_TRAINING_TOPIC,
    include_extended_web_dialogue: bool = False,
    model_version: str = DEFAULT_MODEL_VERSION,
    output_dir: str | Path | None = None,
    preflight_only: bool = False,
) -> None:
    """Запускает обучение и сохраняет все нужные артефакты локально."""
    if epochs < 1 or batch_size < 1:
        raise ValueError("epochs и batch_size должны быть положительными.")
    if max_len < 5:
        raise ValueError("max_len должен быть не меньше 5.")
    if not 0.0 <= dropout < 1.0:
        raise ValueError("dropout должен быть от 0.0 включительно до 1.0.")
    if embed_dim < 1 or n_layers < 1:
        raise ValueError("embed_dim и n_layers должны быть положительными.")
    normalized_architecture = architecture.strip().lower()
    if normalized_architecture not in {"transformer", "gru"}:
        raise ValueError("architecture должен быть transformer или gru.")
    if (
        normalized_architecture == "transformer"
        and embed_dim % DEFAULT_HEADS != 0
    ):
        raise ValueError(
            "embed_dim для transformer должен делиться на число голов внимания."
        )
    normalized_tokenizer_type = tokenizer_type.strip().lower()
    if normalized_tokenizer_type not in {"character", "bpe"}:
        raise ValueError("tokenizer_type должен быть character или bpe.")
    if tokenizer_vocab_size <= len(SPECIAL_TOKENS):
        raise ValueError("tokenizer_vocab_size слишком мал.")
    normalized_training_topic = training_topic.strip().lower()
    if normalized_training_topic not in TRAINING_TOPIC_CHOICES:
        choices = ", ".join(TRAINING_TOPIC_CHOICES)
        raise ValueError(
            f"training_topic должен быть одной из тем: {choices}."
        )
    if not model_version.strip():
        raise ValueError("Версия модели не может быть пустой.")

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    source_primary_pairs = load_primary_training_pairs()
    source_web_foundation_pairs = load_web_foundation_pairs()
    source_pairs = [*source_primary_pairs, *source_web_foundation_pairs]
    pairs = load_training_pairs_for_topic(normalized_training_topic)
    if not pairs:
        raise ValueError(
            f"Для темы {normalized_training_topic!r} не найдено обучающих примеров."
        )
    dialogue_examples = (
        load_dialogue_examples(DIALOGUE_DATASET_PATH)
        if DIALOGUE_DATASET_PATH.exists()
        else []
    )
    web_dialogue_examples_primary = (
        load_dialogue_examples(WEB_DIALOGUE_DATASET_PATH)
        if WEB_DIALOGUE_DATASET_PATH.exists()
        else []
    )
    web_dialogue_examples_extended = (
        load_dialogue_examples(WEB_DIALOGUE_EXTENDED_DATASET_PATH)
        if include_extended_web_dialogue
        and WEB_DIALOGUE_EXTENDED_DATASET_PATH.exists()
        else []
    )
    web_dialogue_examples = [
        *web_dialogue_examples_primary,
        *web_dialogue_examples_extended,
    ]
    all_dialogue_examples = [*dialogue_examples, *web_dialogue_examples]
    source_dialogue_pairs = dialogue_examples_to_pairs(dialogue_examples)
    web_dialogue_pairs = dialogue_examples_to_pairs(web_dialogue_examples)
    dialogue_pairs = filter_pairs_by_topic(
        source_dialogue_pairs,
        normalized_training_topic,
    )
    if normalized_training_topic in {"all", "web"}:
        dialogue_pairs = [*dialogue_pairs, *web_dialogue_pairs]
    split: DatasetSplit = split_dataset(
        pairs,
        validation_ratio=validation_ratio,
        test_ratio=test_ratio,
        seed=seed,
    )
    # Базовые validation/test остаются чистыми: многоходовые примеры добавляются
    # только к обучающей части, чтобы отдельно измерять качество на исходных QA.
    train_pairs = [*split.train, *dialogue_pairs]
    validation_pairs = split.validation
    test_pairs = split.test
    tokenizer = build_tokenizer(
        train_pairs,
        tokenizer_type=normalized_tokenizer_type,
        tokenizer_vocab_size=tokenizer_vocab_size,
    )
    # Проверяем реальную смесь после добавления диалогов. Старые Dataset-классы
    # сохраняются для совместимости, но новое обучение не принимает обрезание.
    preflight = analyze_training_preflight(
        {"train": train_pairs, "validation": validation_pairs, "test": test_pairs},
        tokenizer, max_len, all_dialogue_examples,
    )
    run_dir = make_run_directory(output_dir)
    checkpoint_path = run_dir / CHECKPOINT_PATH.name
    tokenizer_path = run_dir / TOKENIZER_PATH.name
    metrics_path = run_dir / METRICS_PATH.name
    preflight.update({"seed": seed, "topic": normalized_training_topic,
                      "tokenizer_type": normalized_tokenizer_type,
                      "vocab_size": tokenizer.vocab_size,
                      "model_version": model_version.strip(),
                      "include_extended_web_dialogue": include_extended_web_dialogue})
    (run_dir / "training_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Проверка входных данных: {run_dir / 'training_preflight.json'}")
    if preflight_only:
        print(f"Проверки пройдены: {preflight['checks_passed']}. Обучение не запускалось.")
        return
    require_valid_preflight(preflight)
    train_dataset = QuestionAnswerDataset(train_pairs, tokenizer, max_len)
    validation_dataset = QuestionAnswerDataset(validation_pairs, tokenizer, max_len)
    test_dataset = QuestionAnswerDataset(test_pairs, tokenizer, max_len)
    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        shuffle=True,
        collate_fn=collate_seq2seq_batch,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=batch_size,
        shuffle=False,
        collate_fn=collate_seq2seq_batch,
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=batch_size,
        shuffle=False,
        collate_fn=collate_seq2seq_batch,
    )

    if normalized_architecture == "gru":
        checkpoint_architecture = "gru-attention-seq2seq"
        model_config = {
            "vocab_size": tokenizer.vocab_size,
            "embed_dim": embed_dim,
            "hidden_dim": embed_dim,
            "n_layers": n_layers,
            "max_len": max_len,
            "dropout": dropout,
        }
        model = TinyGRUAttentionSeq2Seq(**model_config).to(DEVICE)
    else:
        checkpoint_architecture = "seq2seq"
        model_config = {
            "vocab_size": tokenizer.vocab_size,
            "embed_dim": embed_dim,
            "n_heads": DEFAULT_HEADS,
            "n_layers": n_layers,
            "max_len": max_len,
            "dropout": dropout,
        }
        model = TinySeq2SeqTransformer(**model_config).to(DEVICE)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="min",
        factor=0.5,
        patience=2,
        min_lr=1e-5,
    )
    criterion = nn.CrossEntropyLoss(ignore_index=-100)
    history: list[dict[str, float | int]] = []
    best_model_state: dict[str, torch.Tensor] | None = None
    best_validation_loss = float("inf")
    best_validation_accuracy = 0.0
    best_epoch = 0

    print(f"Устройство: {DEVICE}")
    print(f"Архитектура: {checkpoint_architecture}")
    print(
        f"Размер сети: {embed_dim} признаков, "
        f"слоёв: {n_layers}, dropout: {dropout}"
    )
    print(
        f"Тема: {normalized_training_topic}; примеров: {len(pairs)} "
        f"из {len(source_pairs)}, диалоговых примеров: "
        f"{len(dialogue_pairs)}, словарь: {tokenizer.vocab_size} токенов"
    )
    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        batches = 0
        correct_train_tokens = 0
        total_train_target_tokens = 0
        for encoder_ids, decoder_ids, labels, encoder_padding_mask in train_loader:
            encoder_ids = encoder_ids.to(DEVICE)
            decoder_ids = decoder_ids.to(DEVICE)
            labels = labels.to(DEVICE)
            encoder_padding_mask = encoder_padding_mask.to(DEVICE)

            logits = model(
                encoder_ids,
                decoder_ids,
                encoder_attention_mask=encoder_padding_mask,
            )
            loss = criterion(logits.reshape(-1, logits.size(-1)), labels.reshape(-1))
            predictions = logits.argmax(dim=-1)
            target_mask = labels.ne(-100)
            correct_train_tokens += int(
                (predictions[target_mask] == labels[target_mask]).sum().item()
            )
            total_train_target_tokens += int(target_mask.sum().item())
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += float(loss.item())
            batches += 1

        train_loss = total_loss / max(1, batches)
        train_accuracy = correct_train_tokens / max(1, total_train_target_tokens)
        validation_metrics = evaluate_seq2seq(model, validation_loader, criterion)
        current_learning_rate = float(optimizer.param_groups[0]["lr"])
        history.append(
            {
                "epoch": epoch,
                "train_loss": train_loss,
                "train_token_accuracy": train_accuracy,
                "validation_loss": validation_metrics["loss"],
                "validation_token_accuracy": validation_metrics["token_accuracy"],
                "learning_rate": current_learning_rate,
            }
        )
        scheduler.step(validation_metrics["loss"])
        if validation_metrics["loss"] < best_validation_loss:
            # Храним копию лучшего состояния, чтобы финальный checkpoint не
            # заменился последней, но уже переобученной эпохой.
            best_model_state = {
                key: value.detach().cpu().clone()
                for key, value in deepcopy(model.state_dict()).items()
            }
            best_validation_loss = validation_metrics["loss"]
            best_validation_accuracy = validation_metrics["token_accuracy"]
            best_epoch = epoch
        print(
            f"Эпоха {epoch}/{epochs}: "
            f"train_loss={train_loss:.4f}, "
            f"train_accuracy={train_accuracy:.4f}, "
            f"validation_loss={validation_metrics['loss']:.4f}, "
            f"validation_accuracy={validation_metrics['token_accuracy']:.4f}, "
            f"learning_rate={current_learning_rate:.6f}"
        )

    if best_model_state is None:
        raise RuntimeError("Не удалось сохранить состояние лучшей эпохи.")
    # Проверяем итоговое качество именно на лучшей сохранённой эпохе.
    model.load_state_dict(best_model_state)
    test_metrics = evaluate_seq2seq(model, test_loader, criterion)

    # Checkpoint кандидата никогда не активируется самим обучением.
    tokenizer.save(tokenizer_path)
    torch.save(
        {
            "architecture": checkpoint_architecture,
            "model_version": model_version.strip(),
            "training_topic": normalized_training_topic,
            "model_config": model_config,
            "selected_epoch": best_epoch,
            "model_state": best_model_state,
        },
        checkpoint_path,
    )
    with metrics_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "dataset_path": str(DATASET_PATH),
                "web_foundation_dataset_path": str(WEB_FOUNDATION_DATASET_PATH),
                "source_dataset_fingerprint": dataset_fingerprint(source_pairs),
                "primary_dataset_fingerprint": dataset_fingerprint(
                    source_primary_pairs
                ),
                "web_foundation_dataset_fingerprint": (
                    dataset_fingerprint(source_web_foundation_pairs)
                    if source_web_foundation_pairs
                    else None
                ),
                "dataset_fingerprint": dataset_fingerprint(pairs),
                "dialogue_dataset_path": str(DIALOGUE_DATASET_PATH),
                "web_dialogue_dataset_path": str(WEB_DIALOGUE_DATASET_PATH),
                "web_dialogue_extended_dataset_path": str(
                    WEB_DIALOGUE_EXTENDED_DATASET_PATH
                ),
                "dialogue_dataset_fingerprint": dialogue_dataset_fingerprint(
                    all_dialogue_examples
                ),
                "primary_dialogue_dataset_fingerprint": dialogue_dataset_fingerprint(
                    dialogue_examples
                ),
                "web_dialogue_dataset_fingerprint": (
                    dialogue_dataset_fingerprint(web_dialogue_examples)
                    if web_dialogue_examples
                    else None
                ),
                "web_dialogue_extended_dataset_fingerprint": (
                    dialogue_dataset_fingerprint(web_dialogue_examples_extended)
                    if web_dialogue_examples_extended
                    else None
                ),
                "include_extended_web_dialogue": include_extended_web_dialogue,
                "training_topic": normalized_training_topic,
                "dialogue_examples": len(dialogue_pairs),
                "dialogue_examples_source": len(all_dialogue_examples),
                "primary_dialogue_examples_source": len(dialogue_examples),
                "web_dialogue_examples_source": len(web_dialogue_examples),
                "web_dialogue_extended_examples_source": len(
                    web_dialogue_examples_extended
                ),
                "source_examples": len(source_pairs),
                "source_primary_examples": len(source_primary_pairs),
                "source_web_foundation_examples": len(
                    source_web_foundation_pairs
                ),
                "examples": len(pairs),
                "train_examples": len(split.train),
                "training_examples_total": len(train_pairs),
                "validation_examples": len(validation_pairs),
                "test_examples": len(test_pairs),
                "model_version": model_version.strip(),
                "training_preflight": preflight,
                "tokenizer_type": normalized_tokenizer_type,
                "tokenizer_vocab_size_requested": tokenizer_vocab_size,
                "tokenizer_vocab_size_actual": tokenizer.vocab_size,
                "architecture": checkpoint_architecture,
                "model_config": model_config,
                "learning_rate_initial": learning_rate,
                "device": str(DEVICE),
                "split": {
                    "seed": seed,
                    "validation_ratio": validation_ratio,
                    "test_ratio": test_ratio,
                    "prompt_overlap_count": len(prompt_overlap(split)),
                },
                "epochs_requested": epochs,
                "epochs_completed": len(history),
                "selected_epoch": best_epoch,
                "best_validation_loss": best_validation_loss,
                "best_validation_token_accuracy": best_validation_accuracy,
                "test_loss": test_metrics["loss"],
                "test_token_accuracy": test_metrics["token_accuracy"],
                "scheduler": {
                    "type": "ReduceLROnPlateau",
                    "factor": 0.5,
                    "patience": 2,
                    "min_lr": 1e-5,
                },
                "history": history,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Словарь кандидата сохранён: {tokenizer_path}")
    print(f"Модель кандидата сохранена: {checkpoint_path}")
    print(
        f"Тест: loss={test_metrics['loss']:.4f}, "
        f"accuracy={test_metrics['token_accuracy']:.4f}"
    )


def parse_args() -> argparse.Namespace:
    """Читает параметры командной строки для эксперимента."""
    parser = argparse.ArgumentParser(description="Обучение собственной маленькой модели")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--max-len", type=int, default=DEFAULT_MAX_LEN)
    parser.add_argument("--validation-ratio", type=float, default=0.1)
    parser.add_argument("--test-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Новая отдельная папка кандидата; существующая не перезаписывается.")
    parser.add_argument("--preflight-only", action="store_true",
                        help="Проверить данные без создания модели и обучения.")
    parser.add_argument("--dropout", type=float, default=DEFAULT_DROPOUT)
    parser.add_argument(
        "--embed-dim",
        type=int,
        default=DEFAULT_EMBED_DIM,
        help="Размер внутреннего представления токенов и скрытого состояния.",
    )
    parser.add_argument(
        "--layers",
        type=int,
        default=DEFAULT_LAYERS,
        help="Количество слоёв encoder и decoder.",
    )
    parser.add_argument(
        "--architecture",
        choices=("transformer", "gru"),
        default=DEFAULT_ARCHITECTURE,
        help="Собственная encoder-decoder архитектура для эксперимента.",
    )
    parser.add_argument(
        "--tokenizer",
        choices=("character", "bpe"),
        default=DEFAULT_TOKENIZER_TYPE,
        help="Собственная схема разбиения текста.",
    )
    parser.add_argument(
        "--tokenizer-vocab-size",
        type=int,
        default=DEFAULT_TOKENIZER_VOCAB_SIZE,
        help="Целевой размер BPE-словаря.",
    )
    parser.add_argument(
        "--topic",
        choices=TRAINING_TOPIC_CHOICES,
        default=DEFAULT_TRAINING_TOPIC,
        help="Обратимо выбирает тематическую часть исходного датасета.",
    )
    parser.add_argument(
        "--model-version",
        type=str,
        default=DEFAULT_MODEL_VERSION,
    )
    parser.add_argument(
        "--include-extended-web-dialogue",
        action="store_true",
        help=(
            "Подключить экспериментальный web_dialogue_examples_v2.jsonl "
            "к train-части. По умолчанию выключено."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    train_model(
        epochs=arguments.epochs,
        batch_size=arguments.batch_size,
        learning_rate=arguments.learning_rate,
        max_len=arguments.max_len,
        validation_ratio=arguments.validation_ratio,
        test_ratio=arguments.test_ratio,
        seed=arguments.seed,
        output_dir=arguments.output_dir,
        preflight_only=arguments.preflight_only,
        dropout=arguments.dropout,
        embed_dim=arguments.embed_dim,
        n_layers=arguments.layers,
        architecture=arguments.architecture,
        tokenizer_type=arguments.tokenizer,
        tokenizer_vocab_size=arguments.tokenizer_vocab_size,
        training_topic=arguments.topic,
        include_extended_web_dialogue=arguments.include_extended_web_dialogue,
        model_version=arguments.model_version,
    )

````

## ai_core/training_preflight.py

````python
"""Проверка учебных выборок до создания модели и изменения весов.

Проверка техническая: она не обещает хорошего языка или достоверных ответов.
В отчёте сохраняются количества и отпечатки, а не тексты учебных сообщений.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import uuid4

from .dataset_tools import dataset_fingerprint, normalize_prompt
from .conversation_context import build_context
from .dialogue_data import DialogueExample, format_dialogue_prompt
from .training_data import TrainingPair


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_run_directory(requested: str | Path | None = None) -> Path:
    """Каждый опыт получает новую папку; действующие веса не перезаписываются."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = Path(requested) if requested else (
        PROJECT_ROOT / "model_candidates" / f"run-{stamp}-{uuid4().hex[:8]}"
    )
    destination = destination.resolve()
    active = (PROJECT_ROOT / "model_artifacts").resolve()
    if destination == active or active in destination.parents:
        raise ValueError("Каталог действующей модели нельзя использовать для эксперимента.")
    # exist_ok=False защищает и предыдущий опыт от случайной перезаписи.
    destination.mkdir(parents=True, exist_ok=False)
    return destination


def _fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_prompt(text).encode("utf-8")).hexdigest()


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def analyze_training_preflight(
    splits: dict[str, list[TrainingPair]],
    tokenizer: Any,
    max_len: int,
    dialogues: list[DialogueExample] | None = None,
) -> dict[str, Any]:
    """Проверяет фактический train после добавления диалогов, без обрезания.

    Здесь измеряется encoder-decoder формат старого train.py: вопрос + BOS/SEP
    и ответ + BOS/EOS имеют отдельный лимит. Для decoder-only нужен другой
    бюджет общей последовательности — этот отчёт не следует применять к нему.
    """
    if max_len < 5:
        raise ValueError("max_len должен быть не меньше 5.")
    if set(splits) != {"train", "validation", "test"}:
        raise ValueError("Нужны ровно train, validation и test.")
    summaries: dict[str, Any] = {}
    prompt_sets: dict[str, set[str]] = {}
    reasons: list[str] = []
    for name, pairs in splits.items():
        prompt_lengths: list[int] = []
        target_lengths: list[int] = []
        examples_over_limit = 0
        unknown_prompt = unknown_target = 0
        prompt_sets[name] = {_fingerprint(pair.prompt) for pair in pairs}
        for pair in pairs:
            # max_length специально не передаётся: сначала видим весь пример.
            prompt_ids = tokenizer.encode(pair.prompt)
            target_ids = tokenizer.encode(pair.completion)
            prompt_length, target_length = len(prompt_ids) + 2, len(target_ids) + 2
            prompt_lengths.append(prompt_length)
            target_lengths.append(target_length)
            examples_over_limit += int(max(prompt_length, target_length) > max_len)
            unknown_prompt += prompt_ids.count(tokenizer.unk_id)
            unknown_target += target_ids.count(tokenizer.unk_id)
        source_overflow = sum(length > max_len for length in prompt_lengths)
        target_overflow = sum(length > max_len for length in target_lengths)
        summaries[name] = {
            "examples": len(pairs), "fingerprint": dataset_fingerprint(pairs),
            "prompt_tokens": sum(prompt_lengths), "target_tokens": sum(target_lengths),
            "prompt_max": max(prompt_lengths, default=0),
            "target_max": max(target_lengths, default=0),
            "prompt_p95": _percentile(prompt_lengths, .95),
            "target_p95": _percentile(target_lengths, .95),
            "prompt_over_limit": source_overflow, "target_over_limit": target_overflow,
            "examples_over_limit": examples_over_limit,
            "unknown_prompt_tokens": unknown_prompt, "unknown_target_tokens": unknown_target,
        }
        if not pairs:
            reasons.append(f"empty_{name}")
        if examples_over_limit:
            reasons.append(f"truncation_{name}")
        if name == "train" and (unknown_prompt or unknown_target):
            reasons.append("unknown_tokens_in_train")

    # Проверяем именно итоговые части, а не базовый split до добавления диалогов.
    cross_split = []
    for left, right in combinations(splits, 2):
        overlap = prompt_sets[left] & prompt_sets[right]
        if overlap:
            cross_split.append({"left": left, "right": right, "count": len(overlap),
                                "sample_hashes": sorted(overlap)[:10]})
    if cross_split:
        reasons.append("cross_split_prompt_overlap")

    # Проверочный вопрос может находиться внутри истории учебного диалога,
    # хотя целый многоходовый prompt не совпадает с ним строка в строку.
    # Считаем это сигналом загрязнения оценки, даже если ответ в истории иной.
    dialogue_overlap: Counter[str] = Counter()
    overlap_examples = []
    for index, example in enumerate(dialogues or []):
        if _fingerprint(format_dialogue_prompt(example)) not in prompt_sets["train"]:
            continue
        # Форматтер оставляет только недавнюю историю. Отброшенная старая
        # реплика не считается утечкой через вход текущего учебного примера.
        visible_lines = set(build_context(example.messages).splitlines())
        prompts = {example.prompt}
        for message in example.messages:
            if message["role"] != "user":
                continue
            full_line = build_context([message], max_characters=max(1600, len(message["content"]) + 64))
            if full_line in visible_lines:
                prompts.add(message["content"])
        fingerprints = {_fingerprint(prompt) for prompt in prompts}
        for heldout in ("validation", "test"):
            found = fingerprints & prompt_sets[heldout]
            dialogue_overlap[heldout] += len(found)
            if found and len(overlap_examples) < 20:
                overlap_examples.append({"dialogue_index": index, "heldout": heldout,
                                         "prompt_hashes": sorted(found)[:10]})
    if any(dialogue_overlap.values()):
        reasons.append("dialogue_history_overlaps_holdout")

    return {
        "format_version": 1, "encoding": "encoder-decoder",
        "max_len": max_len, "splits": summaries,
        "cross_split_prompt_overlap": cross_split,
        "dialogue_history_overlap": dict(dialogue_overlap),
        "dialogue_history_overlap_examples": overlap_examples,
        "checks_passed": not reasons, "blocking_reasons": reasons,
        "quality_approved": False,
        "limitations": [
            "Техническая проверка не подтверждает качество языка, фактов или лицензии.",
            "Совпадение вопросов не заменяет проверку близких дубликатов и семейств источников.",
            "Лимит измерен в токенах данного токенизатора, включая служебные токены.",
        ],
    }


def require_valid_preflight(report: dict[str, Any]) -> None:
    """Не запускает оптимизатор на молча повреждённых или смешанных данных."""
    if not report["checks_passed"]:
        raise ValueError("Обучение остановлено до изменения весов: " +
                         ", ".join(report["blocking_reasons"]))

````

## evaluation/audit_training_state.py

````python
"""Инвентаризация существующих учебных файлов и весов без обучения и сети."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit(output):
    manifest_path = ROOT / "data/brain_v3/corpus_manifest.v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = []
    # Потоковое чтение не держит большой корпус в RAM. Тексты в отчёт не пишем.
    for source in manifest["sources"]:
        path = manifest_path.parent / source["raw_path"]
        counts, types, tiers = Counter(), Counter(), Counter()
        turns = 0
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                counts[record["split"]] += 1
                types[record["record_type"]] += 1
                tiers[record["quality_tier"]] += 1
                turns += len(record.get("turns", []))
        digest = digest_file(path)
        sources.append({"source_id": source["source_id"], "records": sum(counts.values()),
                        "splits": dict(counts), "record_types": dict(types),
                        "quality_tiers": dict(tiers), "dialogue_turns": turns,
                        "sha256": digest, "matches_manifest": digest == source["raw_sha256"],
                        "declared_training_approval": source["approved_for_training"]})
    artifact = ROOT / "model_artifacts/tiny_transformer.pt"
    checkpoint = torch.load(artifact, map_location="cpu", weights_only=True)
    metrics = json.loads((artifact.parent / "training_metrics.json").read_text(encoding="utf-8"))
    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(), "manifest_sha256": digest_file(manifest_path),
        "sources": sources, "corpus_records": sum(s["records"] for s in sources),
        "all_source_hashes_match": all(s["matches_manifest"] for s in sources),
        "active_checkpoint": {"sha256": digest_file(artifact),
            "version": checkpoint["model_version"], "architecture": checkpoint["architecture"],
            "parameters": sum(value.numel() for value in checkpoint["model_state"].values()),
            "config": checkpoint["model_config"],
            "reported_train_examples": metrics["training_examples_total"],
            "reported_test_token_accuracy": metrics["test_token_accuracy"]},
        "runtime": {"torch": torch.__version__, "cuda_available": torch.cuda.is_available()},
        "limitations": ["Это инвентаризация; смысловая оценка и near-duplicate audit не запускались повторно.",
                        "Одобрение источника в манифесте не означает ручную проверку каждой записи.",
                        "Точность токенов из старого отчёта не равна качеству свободных ответов."],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "training_state.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"records": report["corpus_records"], "source_hashes_match": report["all_source_hashes_match"],
                      "parameters": report["active_checkpoint"]["parameters"], "cuda": report["runtime"]["cuda_available"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit(args.output)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        parser.exit(1, f"Инвентаризация не завершена: {error}\n")

````

## tests/test_training_preflight.py

````python
"""Проверки реальных рисков обучения: потеря текста, утечка и запись весов."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_core.dialogue_data import DialogueExample, dialogue_examples_to_pairs
from ai_core.tokenizer import CharacterTokenizer
from ai_core.training_data import TrainingPair
from ai_core.training_preflight import (
    PROJECT_ROOT, analyze_training_preflight, make_run_directory, require_valid_preflight,
)


class TrainingPreflightTests(unittest.TestCase):
    def setUp(self):
        self.splits = {
            "train": [TrainingPair("abc 1", "cab")],
            "validation": [TrainingPair("abc 2", "bac")],
            "test": [TrainingPair("abc 3", "cba")],
        }
        self.tokenizer = CharacterTokenizer()
        self.tokenizer.build(["abc 123 длинный вопрос ответ"])

    def analyze(self, max_len=100, dialogues=None):
        return analyze_training_preflight(self.splits, self.tokenizer, max_len, dialogues)

    def test_valid_data_does_not_claim_quality_approval(self):
        report = self.analyze()
        self.assertTrue(report["checks_passed"])
        self.assertFalse(report["quality_approved"])
        require_valid_preflight(report)

    def test_long_target_is_counted_before_eos_replacement(self):
        self.splits["train"][0] = TrainingPair("abc", "cab" * 30)
        report = self.analyze(20)
        self.assertEqual(report["splits"]["train"]["target_max"], 92)
        self.assertEqual(report["splits"]["train"]["target_over_limit"], 1)
        with self.assertRaisesRegex(ValueError, "truncation_train"):
            require_valid_preflight(report)

    def test_long_prompt_is_not_silently_accepted(self):
        self.splits["train"][0] = TrainingPair("abc" * 20, "cab")
        self.assertEqual(self.analyze(20)["splits"]["train"]["prompt_over_limit"], 1)

    def test_service_tokens_count_at_exact_boundary(self):
        self.splits["train"][0] = TrainingPair("abc", "cab")
        report = self.analyze(5)
        self.assertEqual(report["splits"]["train"]["examples_over_limit"], 0)

    def test_final_train_overlap_after_appending_dialogue_is_detected(self):
        self.splits["train"].append(TrainingPair("ABC 2!", "cab"))
        report = self.analyze()
        self.assertIn("cross_split_prompt_overlap", report["blocking_reasons"])
        self.assertEqual(report["cross_split_prompt_overlap"][0]["count"], 1)

    def test_validation_test_overlap_is_also_detected(self):
        self.splits["test"] = list(self.splits["validation"])
        self.assertIn("cross_split_prompt_overlap", self.analyze()["blocking_reasons"])

    def test_heldout_question_inside_history_is_detected(self):
        dialogue = DialogueExample(
            messages=({"role": "user", "content": "abc 2"},
                      {"role": "assistant", "content": "bac"}),
            prompt="abc 1", completion="cab",
        )
        self.splits["train"].extend(dialogue_examples_to_pairs([dialogue]))
        texts = [value for pairs in self.splits.values() for pair in pairs
                 for value in (pair.prompt, pair.completion)]
        self.tokenizer.build(texts)
        report = self.analyze(1000, [dialogue])
        self.assertEqual(report["dialogue_history_overlap"]["validation"], 1)
        self.assertIn("dialogue_history_overlaps_holdout", report["blocking_reasons"])

    def test_unused_dialogue_does_not_contaminate_selected_training(self):
        unused = DialogueExample(messages=({"role": "user", "content": "abc 2"},),
                                 prompt="not selected", completion="cab")
        self.assertTrue(self.analyze(dialogues=[unused])["checks_passed"])

    def test_history_discarded_by_formatter_is_not_a_false_leak(self):
        messages = [{"role": "user", "content": "abc 2"},
                    {"role": "assistant", "content": "bac"}]
        messages.extend({"role": "user" if i % 2 == 0 else "assistant", "content": "abc 1"}
                        for i in range(6))
        example = DialogueExample(tuple(messages), "abc 1", "cab")
        self.splits["train"].extend(dialogue_examples_to_pairs([example]))
        self.tokenizer.build([self.splits["train"][-1].prompt])
        self.assertTrue(self.analyze(1000, [example])["checks_passed"])

    def test_invalid_recipe_stops_before_model_creation(self):
        import ai_core.train as trainer
        pairs = [TrainingPair(f"abcdef0123456789 {i}", "abc" * 20) for i in range(8)]
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(trainer, "load_primary_training_pairs", return_value=pairs), \
                patch.object(trainer, "load_web_foundation_pairs", return_value=[]), \
                patch.object(trainer, "load_training_pairs_for_topic", return_value=pairs), \
                patch.object(trainer, "load_dialogue_examples", return_value=[]), \
                patch.object(trainer, "TinyGRUAttentionSeq2Seq") as constructor:
            with self.assertRaisesRegex(ValueError, "truncation_train"):
                trainer.train_model(epochs=1, batch_size=2, learning_rate=.001, max_len=16,
                                    validation_ratio=.125, test_ratio=.125, seed=42,
                                    architecture="gru", output_dir=Path(directory) / "blocked")
            constructor.assert_not_called()

    def test_report_does_not_contain_prompt_text(self):
        self.splits["train"].append(TrainingPair("abc 2", "cab"))
        self.assertNotIn("abc 2", json.dumps(self.analyze(), ensure_ascii=False))

    def test_empty_heldout_is_explicit(self):
        self.splits["test"] = []
        self.assertIn("empty_test", self.analyze()["blocking_reasons"])

    def test_candidate_cannot_overwrite_active_or_existing_directory(self):
        with self.assertRaises(ValueError):
            make_run_directory(PROJECT_ROOT / "model_artifacts" / "another")
        with tempfile.TemporaryDirectory() as directory:
            candidate = make_run_directory(Path(directory) / "candidate")
            self.assertTrue(candidate.is_dir())
            with self.assertRaises(FileExistsError):
                make_run_directory(candidate)

    def test_real_trainer_preflight_and_one_epoch_preserve_active_checkpoint(self):
        # Малый учебный опыт во временной папке проверяет весь путь сохранения.
        # Он не служит обучением продукта и не использует пользовательские чаты.
        import torch
        import ai_core.train as trainer
        pairs = [TrainingPair(f"abcdef0123456789 {i}", "abc") for i in range(8)]
        checkpoint = PROJECT_ROOT / "model_artifacts" / "tiny_transformer.pt"
        before = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        old_threads = torch.get_num_threads()
        torch.set_num_threads(2)
        try:
            with tempfile.TemporaryDirectory() as directory, \
                    patch.object(trainer, "load_primary_training_pairs", return_value=pairs), \
                    patch.object(trainer, "load_web_foundation_pairs", return_value=[]), \
                    patch.object(trainer, "load_training_pairs_for_topic", return_value=pairs), \
                    patch.object(trainer, "load_dialogue_examples", return_value=[]):
                parameters = dict(epochs=1, batch_size=2, learning_rate=.001, max_len=32,
                                  validation_ratio=.125, test_ratio=.125, seed=42,
                                  architecture="gru", embed_dim=8, n_layers=1)
                diagnostic = Path(directory) / "diagnostic"
                trainer.train_model(**parameters, output_dir=diagnostic, preflight_only=True)
                self.assertEqual([p.name for p in diagnostic.iterdir()], ["training_preflight.json"])
                run = Path(directory) / "candidate"
                trainer.train_model(**parameters, output_dir=run)
                saved = torch.load(run / "tiny_transformer.pt", map_location="cpu", weights_only=True)
                self.assertEqual(saved["selected_epoch"], 1)
                self.assertTrue((run / "tokenizer.json").exists())
                metrics = json.loads((run / "training_metrics.json").read_text(encoding="utf-8"))
                self.assertTrue(metrics["training_preflight"]["checks_passed"])
        finally:
            torch.set_num_threads(old_threads)
        self.assertEqual(hashlib.sha256(checkpoint.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()

````

