"""Измеряет потерю вопроса при обучении и влияние штрафов декодирования."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_core.conversation_context import build_context, encode_prompt_with_context
from ai_core.dialogue_data import load_dialogue_examples, format_dialogue_prompt
from ai_core.tokenizer import load_tokenizer
from ai_core.training_corpus import WEB_DIALOGUE_DATASET_PATH


def main() -> None:
    tokenizer = load_tokenizer(ROOT / "model_artifacts/tokenizer.json")
    examples = load_dialogue_examples(WEB_DIALOGUE_DATASET_PATH)
    differences, truncated, lost_question = 0, 0, 0
    for example in examples:
        full_text = format_dialogue_prompt(example)
        old_ids = tokenizer.encode(full_text, add_bos=True, max_length=127)
        api_ids = encode_prompt_with_context(
            tokenizer, example.prompt, build_context(example.messages),
            max_prompt_length=127,
        )
        differences += old_ids != api_ids
        truncated += len(tokenizer.encode(full_text, add_bos=True)) > 127
        lost_question += not tokenizer.decode(old_ids).endswith(example.prompt)
    print(json.dumps({
        "checkpoint": "tiny-gru-bpe-web-v1.3-dialogue",
        "web_dialogues": len(examples),
        "truncated_training_prompts": truncated,
        "training_prompts_missing_complete_current_question": lost_question,
        "train_inference_encoding_mismatches": differences,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
