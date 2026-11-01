"""Небольшая авторская языковая модель для первого этапа проекта."""

from __future__ import annotations

import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence


class TinyTransformer(nn.Module):
    """Маленький причинный Transformer без готовых весов.

    Модель предсказывает следующий токен, глядя только на уже увиденную часть
    последовательности. Все веса создаются и обучаются внутри нашего проекта.
    """

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        n_heads: int = 4,
        n_layers: int = 2,
        max_len: int = 128,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if embed_dim % n_heads != 0:
            raise ValueError("embed_dim должен делиться на n_heads без остатка.")

        self.max_len = max_len
        self.token_embedding = nn.Embedding(vocab_size, embed_dim)
        self.position_embedding = nn.Embedding(max_len, embed_dim)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=embed_dim,
            nhead=n_heads,
            dim_feedforward=embed_dim * 4,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.output_norm = nn.LayerNorm(embed_dim)
        self.output_head = nn.Linear(embed_dim, vocab_size)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Возвращает логиты следующего токена для каждой позиции."""
        if input_ids.ndim != 2:
            raise ValueError("input_ids должен иметь форму [batch, sequence].")
        batch_size, sequence_length = input_ids.shape
        if sequence_length > self.max_len:
            raise ValueError(
                f"Длина последовательности {sequence_length} больше max_len={self.max_len}."
            )

        positions = torch.arange(
            sequence_length,
            device=input_ids.device,
            dtype=torch.long,
        ).unsqueeze(0).expand(batch_size, sequence_length)
        hidden = self.token_embedding(input_ids) + self.position_embedding(positions)

        if attention_mask is None:
            attention_mask = torch.zeros_like(input_ids, dtype=torch.bool)

        # Верхнетреугольная маска запрещает смотреть в будущие токены.
        causal_mask = torch.triu(
            torch.ones(
                sequence_length,
                sequence_length,
                device=input_ids.device,
                dtype=torch.bool,
            ),
            diagonal=1,
        )
        hidden = self.transformer(
            hidden,
            mask=causal_mask,
            src_key_padding_mask=attention_mask,
        )
        return self.output_head(self.output_norm(hidden))


class TinySeq2SeqTransformer(nn.Module):
    """Собственная encoder-decoder модель для связки «вопрос → ответ».

    Кодировщик читает вопрос целиком, а декодировщик по одному символу
    строит ответ. Такая схема лучше отделяет вход от цели обучения, чем
    прежний единый поток токенов.
    """

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        n_heads: int = 4,
        n_layers: int = 2,
        max_len: int = 192,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if embed_dim % n_heads != 0:
            raise ValueError("embed_dim должен делиться на n_heads без остатка.")

        self.max_len = max_len
        self.token_embedding = nn.Embedding(vocab_size, embed_dim)
        self.position_embedding = nn.Embedding(max_len, embed_dim)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=embed_dim,
            nhead=n_heads,
            dim_feedforward=embed_dim * 4,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
        )
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=embed_dim,
            nhead=n_heads,
            dim_feedforward=embed_dim * 4,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)
        self.output_norm = nn.LayerNorm(embed_dim)
        self.output_head = nn.Linear(embed_dim, vocab_size)

    def _embed(self, token_ids: torch.Tensor) -> torch.Tensor:
        """Добавляет позиционную информацию к токенам."""
        batch_size, sequence_length = token_ids.shape
        if sequence_length > self.max_len:
            raise ValueError(
                f"Длина последовательности {sequence_length} больше max_len={self.max_len}."
            )
        positions = torch.arange(
            sequence_length,
            device=token_ids.device,
            dtype=torch.long,
        ).unsqueeze(0).expand(batch_size, sequence_length)
        return self.token_embedding(token_ids) + self.position_embedding(positions)

    def encode(
        self,
        encoder_input_ids: torch.Tensor,
        encoder_attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Кодирует вопрос в последовательность скрытых состояний."""
        if encoder_attention_mask is None:
            encoder_attention_mask = torch.zeros_like(
                encoder_input_ids,
                dtype=torch.bool,
            )
        hidden = self._embed(encoder_input_ids)
        return self.encoder(
            hidden,
            src_key_padding_mask=encoder_attention_mask,
        )

    def decode(
        self,
        decoder_input_ids: torch.Tensor,
        memory: torch.Tensor,
        memory_attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Предсказывает следующий символ ответа по вопросу и префиксу."""
        if memory_attention_mask is None:
            memory_attention_mask = torch.zeros(
                memory.shape[:2],
                dtype=torch.bool,
                device=memory.device,
            )
        target_hidden = self._embed(decoder_input_ids)
        target_length = decoder_input_ids.shape[1]
        causal_mask = torch.triu(
            torch.ones(
                target_length,
                target_length,
                device=decoder_input_ids.device,
                dtype=torch.bool,
            ),
            diagonal=1,
        )
        target_padding_mask = decoder_input_ids.eq(0)
        hidden = self.decoder(
            target_hidden,
            memory,
            tgt_mask=causal_mask,
            tgt_key_padding_mask=target_padding_mask,
            memory_key_padding_mask=memory_attention_mask,
        )
        return self.output_head(self.output_norm(hidden))

    def forward(
        self,
        encoder_input_ids: torch.Tensor,
        decoder_input_ids: torch.Tensor,
        encoder_attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Возвращает логиты для всех позиций ответа."""
        memory = self.encode(encoder_input_ids, encoder_attention_mask)
        return self.decode(decoder_input_ids, memory, encoder_attention_mask)


class TinyGRUAttentionSeq2Seq(nn.Module):
    """Собственная GRU encoder-decoder модель с механизмом внимания.

    Двунаправленный кодировщик читает вопрос целиком. Декодировщик получает
    начальное состояние из вопроса, а attention на каждом шаге выбирает
    наиболее полезные скрытые состояния кодировщика. Готовые веса или чужие
    модели здесь не используются.
    """

    def __init__(
        self,
        vocab_size: int,
        embed_dim: int = 128,
        hidden_dim: int = 128,
        n_layers: int = 2,
        max_len: int = 192,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        if vocab_size < 1:
            raise ValueError("vocab_size должен быть положительным.")
        if embed_dim < 1 or hidden_dim < 1 or n_layers < 1:
            raise ValueError(
                "embed_dim, hidden_dim и n_layers должны быть положительными."
            )
        if max_len < 2:
            raise ValueError("max_len должен быть не меньше 2.")
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout должен быть от 0.0 включительно до 1.0.")

        self.max_len = max_len
        recurrent_dropout = dropout if n_layers > 1 else 0.0
        self.token_embedding = nn.Embedding(
            vocab_size,
            embed_dim,
            padding_idx=0,
        )
        self.embedding_dropout = nn.Dropout(dropout)
        self.encoder = nn.GRU(
            input_size=embed_dim,
            hidden_size=hidden_dim,
            num_layers=n_layers,
            batch_first=True,
            dropout=recurrent_dropout,
            bidirectional=True,
        )
        self.decoder = nn.GRU(
            input_size=embed_dim,
            hidden_size=hidden_dim,
            num_layers=n_layers,
            batch_first=True,
            dropout=recurrent_dropout,
        )
        self.memory_projection = nn.Linear(hidden_dim * 2, hidden_dim)
        self.hidden_bridge = nn.Linear(hidden_dim * 2, hidden_dim)
        self.attention_fusion = nn.Linear(hidden_dim * 2, hidden_dim)
        self.output_norm = nn.LayerNorm(hidden_dim)
        self.output_dropout = nn.Dropout(dropout)
        self.output_head = nn.Linear(hidden_dim, vocab_size)
        self.attention_scale = hidden_dim ** -0.5
        self.n_layers = n_layers

    def _validate_token_ids(self, token_ids: torch.Tensor, name: str) -> None:
        """Проверяет форму и длину входной последовательности."""
        if token_ids.ndim != 2:
            raise ValueError(f"{name} должен иметь форму [batch, sequence].")
        if token_ids.shape[1] < 1:
            raise ValueError(f"{name} не должен быть пустым.")
        if token_ids.shape[1] > self.max_len:
            raise ValueError(
                f"Длина {name} {token_ids.shape[1]} больше max_len={self.max_len}."
            )

    def encode(
        self,
        encoder_input_ids: torch.Tensor,
        encoder_attention_mask: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Кодирует вопрос и готовит начальное состояние декодировщика."""
        self._validate_token_ids(encoder_input_ids, "encoder_input_ids")
        if encoder_attention_mask is None:
            encoder_attention_mask = encoder_input_ids.eq(0)
        if encoder_attention_mask.shape != encoder_input_ids.shape:
            raise ValueError(
                "encoder_attention_mask должен совпадать по форме с входом."
            )

        valid_lengths = (~encoder_attention_mask).sum(dim=1)
        if torch.any(valid_lengths < 1):
            raise ValueError("Каждый вопрос должен содержать хотя бы один токен.")

        embedded = self.embedding_dropout(self.token_embedding(encoder_input_ids))
        packed = pack_padded_sequence(
            embedded,
            valid_lengths.detach().cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        packed_outputs, bidirectional_hidden = self.encoder(packed)
        encoder_outputs, _ = pad_packed_sequence(
            packed_outputs,
            batch_first=True,
            total_length=encoder_input_ids.shape[1],
        )

        batch_size = encoder_input_ids.shape[0]
        hidden_by_direction = bidirectional_hidden.reshape(
            self.n_layers,
            2,
            batch_size,
            -1,
        )
        forward_hidden = hidden_by_direction[:, 0]
        backward_hidden = hidden_by_direction[:, 1]
        decoder_hidden = torch.tanh(
            self.hidden_bridge(torch.cat((forward_hidden, backward_hidden), dim=-1))
        )
        memory = torch.tanh(self.memory_projection(encoder_outputs))
        return memory, decoder_hidden, encoder_attention_mask

    def decode(
        self,
        decoder_input_ids: torch.Tensor,
        encoder_state: tuple[torch.Tensor, torch.Tensor, torch.Tensor],
        memory_attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Строит логиты ответа и применяет внимание к токенам вопроса."""
        self._validate_token_ids(decoder_input_ids, "decoder_input_ids")
        memory, decoder_hidden, stored_mask = encoder_state
        attention_mask = (
            memory_attention_mask
            if memory_attention_mask is not None
            else stored_mask
        )
        if attention_mask.shape != memory.shape[:2]:
            raise ValueError(
                "Маска памяти должна совпадать с первыми двумя измерениями памяти."
            )

        embedded = self.embedding_dropout(self.token_embedding(decoder_input_ids))
        decoder_outputs, _ = self.decoder(embedded, decoder_hidden)
        attention_scores = torch.bmm(
            decoder_outputs,
            memory.transpose(1, 2),
        ) * self.attention_scale
        attention_scores = attention_scores.masked_fill(
            attention_mask.unsqueeze(1),
            torch.finfo(attention_scores.dtype).min,
        )
        attention_weights = torch.softmax(attention_scores, dim=-1)
        context = torch.bmm(attention_weights, memory)
        fused = torch.tanh(
            self.attention_fusion(torch.cat((decoder_outputs, context), dim=-1))
        )
        return self.output_head(
            self.output_dropout(self.output_norm(fused))
        )

    def forward(
        self,
        encoder_input_ids: torch.Tensor,
        decoder_input_ids: torch.Tensor,
        encoder_attention_mask: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Возвращает логиты для всех позиций ответа."""
        encoder_state = self.encode(
            encoder_input_ids,
            encoder_attention_mask,
        )
        return self.decode(decoder_input_ids, encoder_state)
