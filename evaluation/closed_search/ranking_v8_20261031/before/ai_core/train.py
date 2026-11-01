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
