"""
transfer_learning_conv_ai 交互式对话命令行程序

(修改 huggingface 的 interact 例子，适合我们自己修改过的版本)
"""

import logging
import random
import signal
import sys
import warnings
from argparse import ArgumentParser
from itertools import chain
from pprint import pformat

import torch
import torch.nn.functional as F

from transformers import (CONFIG_NAME, WEIGHTS_NAME, AdamW,
                          GPT2DoubleHeadsModel, GPT2Tokenizer)
from utils import download_pretrained_model, get_dataset_personalities

SPECIAL_TOKENS = None
build_input_from_segments = None
add_special_tokens_ = None


def setup_args():
    parser = ArgumentParser()
    parser.add_argument("--train_mod", type=str, default='train_v2',
                        help="本程序需要引用的 train 模组名称。  (default=%(default)s)")
    parser.add_argument("--dataset_path", type=str, default="",
                        help="Path or url of the dataset. (default=%(default)s)")
    parser.add_argument("--dataset_cache", type=str, default='./dataset_cache',
                        help="Path or url of the dataset cache (default=%(default)s)")
    parser.add_argument("--model_checkpoint", type=str, required=True,
                        default="", help="Path, url or short name of the model (default=%(default)s)")
    parser.add_argument("--max_history", type=int, default=2,
                        help="Number of previous utterances to keep in history (default=%(default)s)")
    parser.add_argument("--device", type=str, choices=['cuda', 'cpu'],
                        default="cuda" if torch.cuda.is_available() else "cpu",
                        help="Device (default=%(default)s)")
    parser.add_argument("--no_sample", action='store_true',
                        help="Set to use greedy decoding instead of sampling (default=%(default)s)")
    parser.add_argument("--max_length", type=int, default=20,
                        help="Maximum length of the output utterances (default=%(default)s)")
    parser.add_argument("--min_length", type=int, default=1,
                        help="Minimum length of the output utterances (default=%(default)s)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed (default=%(default)s)")
    parser.add_argument("--temperature", type=float,
                        default=0.7, help="Sampling softmax temperature (default=%(default)s)")
    parser.add_argument("--top_k", type=int, default=0,
                        help="Filter top-k tokens before sampling (<=0: no filtering) (default=%(default)s)")
    parser.add_argument("--top_p", type=float, default=0.9,
                        help="Nucleus filtering (top-p) before sampling (<=0.0: no filtering) (default=%(default)s)")
    return parser.parse_args()


def top_filtering(logits, top_k=0, top_p=0.0, threshold=-float('Inf'), filter_value=-float('Inf')):
    """ Filter a distribution of logits using top-k, top-p (nucleus) and/or threshold filtering
        Args:
            logits: logits distribution shape (vocabulary size)
            top_k: <=0: no filtering, >0: keep only top k tokens with highest probability.
            top_p: <=0.0: no filtering, >0.0: keep only a subset S of candidates, where S is the smallest subset
                whose total probability mass is greater than or equal to the threshold top_p.
                In practice, we select the highest probability tokens whose cumulative probability mass exceeds
                the threshold top_p.
            threshold: a minimal threshold to keep logits
    """
    assert logits.dim() == 1  # Only work for batch size 1 for now - could update but it would obfuscate a bit the code
    top_k = min(top_k, logits.size(-1))
    if top_k > 0:
        # Remove all tokens with a probability less than the last token in the top-k tokens
        indices_to_remove = logits < torch.topk(logits, top_k)[
            0][..., -1, None]
        logits[indices_to_remove] = filter_value

    if top_p > 0.0:
        # Compute cumulative probabilities of sorted tokens
        sorted_logits, sorted_indices = torch.sort(logits, descending=True)
        cumulative_probabilities = torch.cumsum(
            F.softmax(sorted_logits, dim=-1), dim=-1)

        # Remove tokens with cumulative probability above the threshold
        sorted_indices_to_remove = cumulative_probabilities > top_p
        # Shift the indices to the right to keep also the first token above the threshold
        sorted_indices_to_remove[...,
                                 1:] = sorted_indices_to_remove[..., :-1].clone()
        sorted_indices_to_remove[..., 0] = 0

        # Back to unsorted indices and set them to -infinity
        indices_to_remove = sorted_indices[sorted_indices_to_remove]
        logits[indices_to_remove] = filter_value

    indices_to_remove = logits < threshold
    logits[indices_to_remove] = filter_value

    return logits


def sample_sequence(personality, history, tokenizer, model, args, current_output=None):
    special_tokens_ids = tokenizer.convert_tokens_to_ids(SPECIAL_TOKENS)
    if current_output is None:
        current_output = []

    for i in range(args.max_length):
        instance, _ = build_input_from_segments(
            personality, history, current_output, tokenizer, with_eos=False)

        input_ids = torch.tensor(
            instance["input_ids"], device=args.device).unsqueeze(0)
        token_type_ids = torch.tensor(
            instance["token_type_ids"], device=args.device).unsqueeze(0)

        logits = model(input_ids, token_type_ids=token_type_ids)
        if isinstance(logits, tuple):  # for gpt2 and maybe others
            logits = logits[0]
        logits = logits[0, -1, :] / args.temperature
        logits = top_filtering(logits, top_k=args.top_k, top_p=args.top_p)
        probs = F.softmax(logits, dim=-1)

        prev = torch.topk(probs, 1)[1] \
            if args.no_sample \
            else torch.multinomial(probs, 1)
        if i < args.min_length and prev.item() in special_tokens_ids:
            while prev.item() in special_tokens_ids:
                if probs.max().item() == 1:
                    warnings.warn(
                        "Warning: model generating special token with probability 1.")
                    break  # avoid infinitely looping over special token
                prev = torch.multinomial(probs, num_samples=1)

        if prev.item() in special_tokens_ids:
            break
        current_output.append(prev.item())

    return current_output


def sample_generate(personality, history, tokenizer, model, args, current_output=None):
    special_tokens_ids = tokenizer.convert_tokens_to_ids(SPECIAL_TOKENS)
    if current_output is None:
        current_output = []

    for i in range(args.max_length):
        instance, _ = build_input_from_segments(
            personality, history, current_output, tokenizer, with_eos=False)

        input_ids = torch.tensor(
            instance["input_ids"], device=args.device).unsqueeze(0)
        token_type_ids = torch.tensor(
            instance["token_type_ids"], device=args.device).unsqueeze(0)

        logits = model(input_ids, token_type_ids=token_type_ids)
        if isinstance(logits, tuple):  # for gpt2 and maybe others
            logits = logits[0]
        logits = logits[0, -1, :] / args.temperature
        logits = top_filtering(logits, top_k=args.top_k, top_p=args.top_p)
        probs = F.softmax(logits, dim=-1)

        prev = torch.topk(probs, 1)[1] \
            if args.no_sample \
            else torch.multinomial(probs, 1)
        if i < args.min_length and prev.item() in special_tokens_ids:
            while prev.item() in special_tokens_ids:
                if probs.max().item() == 1:
                    warnings.warn(
                        "Warning: model generating special token with probability 1.")
                    break  # avoid infinitely looping over special token
                prev = torch.multinomial(probs, num_samples=1)

        if prev.item() in special_tokens_ids:
            break
        current_output.append(prev.item())
        yield prev.item()


def main(args):
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__file__)
    logger.info(pformat(args))

    random.seed(args.seed)
    torch.random.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)

    logger.info("Import %s", args.train_mod)
    train_mod = __import__(
        args.train_mod, globals(), locals(),
        ['GPT2BPETokenizer_CN', 'SPECIAL_TOKENS',
            'build_input_from_segments', 'add_special_tokens_'],
        0
    )
    global SPECIAL_TOKENS, build_input_from_segments, add_special_tokens_
    GPT2BPETokenizer_CN = train_mod.GPT2BPETokenizer_CN
    SPECIAL_TOKENS = train_mod.SPECIAL_TOKENS
    build_input_from_segments = train_mod.build_input_from_segments
    add_special_tokens_ = train_mod.add_special_tokens_

    logger.info("Get pretrained model and tokenizer")
    model_class, tokenizer_class = GPT2DoubleHeadsModel, GPT2BPETokenizer_CN

    logger.info("load tokenizer....")
    tokenizer = tokenizer_class.from_pretrained(args.model_checkpoint)

    logger.info("load model....")
    model = model_class.from_pretrained(args.model_checkpoint)
    model.to(args.device)
    add_special_tokens_(model, tokenizer)
    n_positions = len(model.transformer.wpe.weight)

    logger.info("Sample a personality")
    personalities = get_dataset_personalities(
        tokenizer, args.dataset_path, args.dataset_cache)
    personality = random.choice(personalities)
    logger.info("Selected personality: %s",
                tokenizer.decode(chain(*personality)))

    # HUP 清空 history
    def sighup_fn(signum, frame):
        logger.info('Signal %s!', signum)
        nonlocal history
        history = []

    signal.signal(signal.SIGHUP, sighup_fn)

    history = []
    try:
        while True:
            raw_text = input(">>> ").strip()
            while not raw_text:
                print('Prompt should not be empty!')
                raw_text = input(">>> ").strip()
            history.append(tokenizer.encode(raw_text))
            with torch.no_grad():
                out_ids = []
                for out_id in sample_generate(personality, history, tokenizer, model, args):
                    out_ids.append(out_id)
                    out_text = tokenizer.decode(
                        [out_id], skip_special_tokens=True
                    )
                    print(out_text.strip(), end='')
                print()
            history.append(out_ids)
            history = history[-(2*args.max_history+1):]
            history_length = sum(len(ids) for ids in history)
            if history_length >= n_positions:
                warnings.warn('历史数据 tokens 长度 %s 大于等于 %s，程序将崩溃！', history_length, n_positions)
    except KeyboardInterrupt:
        logger.warning('KeyboardInterrupt')


if __name__ == "__main__":
    args = setup_args()
    code = main(args)
    exit(code)