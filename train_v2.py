# Copyright (c) 2019-present, HuggingFace Inc.
# All rights reserved. This source code is licensed under the BSD-style license found in the LICENSE file in the root directory of this source tree.
import os
import math
import logging
from pprint import pformat
from argparse import ArgumentParser
from collections import defaultdict
from itertools import chain
import json

import torch
from torch.nn.parallel import DistributedDataParallel
from torch.utils.data import DataLoader, TensorDataset

from ignite.engine import Engine, Events
from ignite.handlers import ModelCheckpoint
from ignite.metrics import Accuracy, Loss, MetricsLambda, RunningAverage
from ignite.contrib.handlers import ProgressBar, PiecewiseLinear
from ignite.contrib.handlers.tensorboard_logger import TensorboardLogger, OutputHandler, OptimizerParamsHandler

#from transformers import (AdamW, GPT2DoubleHeadsModel, GPT2Tokenizer, WEIGHTS_NAME, CONFIG_NAME)
from transformers import GPT2DoubleHeadsModel, GPT2Tokenizer,AdamW,WEIGHTS_NAME, CONFIG_NAME



import sys
sys.path.insert(0, '../transformers/examples/')

from tokenization_cn import GPT2Tokenizer_cn,GPT2BPETokenizer_CN

from utils import get_dataset, make_logdir



#SPECIAL_TOKENS = ["<bos>", "<eos>", "<speaker1>", "<speaker2>", "<pad>"]


#ATTR_TO_SPECIAL_TOKEN = {'additional_special_tokens': ('<speaker1>', '<speaker2>')}

SPECIAL_TOKENS = ["<convai_bos>", "<convai_eos>", "<speaker1>", "<speaker2>", "<convai_pad>"]


#ATTR_TO_SPECIAL_TOKEN = {'additional_special_tokens': ('<speaker1>', '<speaker2>')}
ATTR_TO_SPECIAL_TOKEN = {'bos_token': '<convai_bos>', 'eos_token': '<convai_eos>', 'pad_token': '<convai_pad>',
                         'additional_special_tokens': ('<speaker1>', '<speaker2>')}


MODEL_INPUTS = ["input_ids",
                "mc_token_ids",
                "lm_labels",
                "mc_labels",
                "token_type_ids"]

PADDED_INPUTS = ["input_ids",
                 "lm_labels",
                 "token_type_ids"]

MODEL_CLASSES = {
    'gpt2': (GPT2DoubleHeadsModel, GPT2Tokenizer),
    #'gpt2_cn': (GPT2DoubleHeadsModel, GPT2Tokenizer_cn),
    'gpt2_bpe_cn': (GPT2DoubleHeadsModel, GPT2BPETokenizer_CN),
}

logger = logging.getLogger(__file__)

def average_distributed_scalar(scalar, args):
    """ Average a scalar over the nodes if we are in distributed training. We use this for distributed evaluation. """
    if args.local_rank == -1:
        return scalar
    scalar_t = torch.tensor(scalar, dtype=torch.float, device=args.device) / torch.distributed.get_world_size()
    torch.distributed.all_reduce(scalar_t, op=torch.distributed.ReduceOp.SUM)
    return scalar_t.item()


def pad_dataset(dataset, padding=0):
    """ Pad the dataset. This could be optimized by defining a Dataset class and padding at the batch level, but this is simpler. """
    max_l = max(len(x) for x in dataset["input_ids"])
    for name in PADDED_INPUTS:
        dataset[name] = [x + [padding if name != "lm_labels" else -1] * (max_l - len(x)) for x in dataset[name]]
    return dataset


def add_special_tokens_(model, tokenizer):
    """ Add special tokens to the tokenizer and the model if they have not already been added. """
    orig_num_tokens = len(tokenizer)
    num_added_tokens = tokenizer.add_special_tokens(
        ATTR_TO_SPECIAL_TOKEN)  # returns 0 and doesn't add if they are already there

    new_num_tokens = orig_num_tokens + num_added_tokens

    if new_num_tokens > model.config.vocab_size:
        model.resize_token_embeddings(
            new_num_tokens=new_num_tokens)


def build_input_from_segments(persona, history, reply, tokenizer, lm_labels=False, with_eos=True):
    """ Build a sequence of input from 3 segments: persona, history and last reply. """
    bos, eos, speaker1, speaker2 = tokenizer.convert_tokens_to_ids(SPECIAL_TOKENS[:-1])

    instance = {}
    sequence = [[bos] + list(chain(*persona))] + history + [reply + ([eos] if with_eos else [])]
    sequence = [sequence[0]] + [[speaker2 if (len(sequence)-i) % 2 else speaker1] + s for i, s in enumerate(sequence[1:])]

    instance["input_ids"] = list(chain(*sequence))
    instance["token_type_ids"] = [speaker2 if i % 2 else speaker1 for i, s in enumerate(sequence) for _ in s]
    instance["mc_token_ids"] = len(instance["input_ids"]) - 1
    instance["lm_labels"] = [-1] * len(instance["input_ids"])
    if lm_labels:
        instance["lm_labels"] = ([-1] * sum(len(s) for s in sequence[:-1])) + [-1] + sequence[-1][1:]
    return instance, sequence  # TODO: second arg is never used, delete it



def get_data_loaders(args, tokenizer):
    """ Prepare the dataset for training and evaluation """
    tmp_dir = "/public/transfer-learning-conv-ai-tmp"
    if not os.path.exists(tmp_dir):
        os.makedirs(tmp_dir)

    save_inputs_name = os.path.join(tmp_dir, "debug.dump")
    save_tensors_name = os.path.join(tmp_dir, "debug.tensor.dump")

    if ((not os.path.isfile(save_tensors_name)) and
        (not os.path.isfile(save_inputs_name))):
        personachat = get_dataset(tokenizer, args.dataset_path, args.dataset_cache)
        too_long_dataset = 0
        logger.info("Build inputs and labels")
        datasets = {"train": defaultdict(list), "valid": defaultdict(list)}
        for dataset_name, dataset in personachat.items():
            num_candidates = len(dataset[0]["utterances"][0]["candidates"])
            if args.num_candidates > 0 :#and dataset_name == 'train':
                num_candidates = min(args.num_candidates, num_candidates)
            for dialog in dataset:
                persona = dialog["personality"].copy()
                for _ in range(args.personality_permutations):
                    for utterance in dialog["utterances"]:
                        history = utterance["history"][-(2*args.max_history+1):]
                        for j, candidate in enumerate(utterance["candidates"][-num_candidates:]):
                            lm_labels = bool(j == num_candidates-1)
                            instance, _ = build_input_from_segments(persona, history, candidate, tokenizer, lm_labels)

                            for input_name, input_array in instance.items():
                                datasets[dataset_name][input_name].append(input_array)
                        datasets[dataset_name]["mc_labels"].append(num_candidates - 1)
                        datasets[dataset_name]["n_candidates"] = num_candidates
                    persona = [persona[-1]] + persona[:-1]  # permuted personalities 重新排序personality

        logger.info(f"save {save_inputs_name} inputs and labels")
        with open(save_inputs_name, "w", encoding='utf8') as output_file:
            json.dump(datasets, output_file, ensure_ascii=False, indent=4, separators=(',', ': '))
    else:
        logger.info(f"load {save_inputs_name} inputs and labels")
        with open(save_inputs_name, "r", encoding='utf8') as input_file:
            datasets = json.load(input_file)

    logger.info("Pad inputs and convert to Tensor")
    tensor_datasets = {"train": [], "valid": []}

    if (not os.path.isfile(save_tensors_name)):
        for dataset_name, dataset in datasets.items():
            dataset = pad_dataset(dataset, padding=tokenizer.convert_tokens_to_ids(SPECIAL_TOKENS[-1]))

            for input_name in MODEL_INPUTS:
                tensor = torch.tensor(dataset[input_name])
                #tensor = torch.tensor(dataset[input_name], dtype=torch.int16)

                if input_name != "mc_labels":
                    tensor = tensor.view((-1, datasets[dataset_name]["n_candidates"]) + tensor.shape[1:])

                #print(tensor.shape)
                tensor_datasets[dataset_name].append(tensor)

        logger.info(f"save tensor {save_tensors_name}")
        torch.save(tensor_datasets, save_tensors_name)
    else:
        logger.info(f"load tensor {save_tensors_name}")
        tensor_datasets = torch.load(save_tensors_name)

    logger.info("Build train and validation dataloaders")
    train_dataset, valid_dataset = TensorDataset(*tensor_datasets["train"]), TensorDataset(*tensor_datasets["valid"])
    train_sampler = torch.utils.data.distributed.DistributedSampler(train_dataset) if args.distributed else None
    valid_sampler = torch.utils.data.distributed.DistributedSampler(valid_dataset) if args.distributed else None
    train_loader = DataLoader(train_dataset, sampler=train_sampler, batch_size=args.train_batch_size, shuffle=(not args.distributed))
    valid_loader = DataLoader(valid_dataset, sampler=valid_sampler, batch_size=args.valid_batch_size, shuffle=False)

    logger.info("Train dataset (Batch, Candidates, Seq length): {}".format(train_dataset.tensors[0].shape))
    logger.info("Valid dataset (Batch, Candidates, Seq length): {}".format(valid_dataset.tensors[0].shape))
    return train_loader, valid_loader, train_sampler, valid_sampler

def initialize_distributed(args):
    """Initialize torch.distributed."""

    print("initialize_distributed\n")
    
    rank = int(os.getenv('RANK', '0'))
    world_size = int(os.getenv("WORLD_SIZE", '1'))

    # Manually set the device ids.
    device = rank % torch.cuda.device_count()
    if args.local_rank is not None:
        device = args.local_rank
    torch.cuda.set_device(device)
    # Call the init process
    init_method = 'tcp://'
    master_ip = os.getenv('MASTER_ADDR', 'localhost')
    master_port = os.getenv('MASTER_PORT', '6000')
    init_method += master_ip + ':' + master_port
    torch.distributed.init_process_group(
        backend="nccl",
        world_size=world_size, rank=rank,
        init_method=init_method)


def train():
    parser = ArgumentParser()
    parser.add_argument("--model_type", default="",type=str, required=True,
                        help="Model type selected in the list: " + ", ".join(MODEL_CLASSES.keys()))

    parser.add_argument("--dataset_path", type=str, default="", help="Path or url of the dataset. If empty download from S3.")
    parser.add_argument("--dataset_cache", type=str, default='./dataset_cache', help="Path or url of the dataset cache")
    parser.add_argument("--model_checkpoint", type=str, default="openai-gpt", help="Path, url or short name of the model")
    parser.add_argument("--num_candidates", type=int, default=2, help="Number of candidates for training")
    parser.add_argument("--max_history", type=int, default=2, help="Number of previous exchanges to keep in history")
    parser.add_argument("--train_batch_size", type=int, default=4, help="Batch size for training")
    parser.add_argument("--valid_batch_size", type=int, default=4, help="Batch size for validation")
    parser.add_argument("--gradient_accumulation_steps", type=int, default=8, help="Accumulate gradients on several steps")
    parser.add_argument("--lr", type=float, default=6.25e-5, help="Learning rate")
    parser.add_argument("--lm_coef", type=float, default=1.0, help="LM loss coefficient")
    parser.add_argument("--mc_coef", type=float, default=1.0, help="Multiple-choice loss coefficient")
    parser.add_argument("--max_norm", type=float, default=1.0, help="Clipping gradient norm")
    parser.add_argument("--n_epochs", type=int, default=3, help="Number of training epochs")
    parser.add_argument("--personality_permutations", type=int, default=1, help="Number of permutations of personality sentences")
    parser.add_argument("--eval_before_start", action='store_true', help="If true start with a first evaluation before training")
    parser.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu", help="Device (cuda or cpu)")
    parser.add_argument("--fp16", type=str, default="", help="Set to O0, O1, O2 or O3 for fp16 training (see apex documentation)")
    parser.add_argument("--local_rank", type=int, default=0, help="Local rank for distributed training (-1: not distributed)")
    parser.add_argument("--save_path", type=str, default="runs", help="model save Path")
    args = parser.parse_args()

    # logging is set to INFO (resp. WARN) for main (resp. auxiliary) process. logger.info => log main process only, logger.warning => log all processes
    logging.basicConfig(level=logging.INFO if args.local_rank in [-1, 0] else logging.WARN)
    logger.warning("Running process %d", args.local_rank)  # This is a logger.warning: it will be printed by all distributed processes
    logger.info("Arguments: %s", pformat(args))

    # Initialize distributed training if needed
    args.distributed = (args.local_rank != -1)
    """
    args.distributed = (args.local_rank != -1)
    if args.distributed:
        torch.cuda.set_device(args.local_rank)
        args.device = torch.device("cuda", args.local_rank)
        torch.distributed.init_process_group(backend='nccl', init_method='env://')
    """
    initialize_distributed(args)

    logger.info("Prepare tokenizer, pretrained model and optimizer.")


    args.model_type = args.model_type.lower()
    model_class, tokenizer_class = MODEL_CLASSES[args.model_type]
    tokenizer = tokenizer_class.from_pretrained(args.model_checkpoint)
    model = model_class.from_pretrained(args.model_checkpoint)

    model.to(args.device)
    # Add special tokens if they are not already added
    add_special_tokens_(model, tokenizer)
    optimizer = AdamW(model.parameters(), lr=args.lr, correct_bias=True)

    # Prepare model for FP16 and distributed training if needed (order is important, distributed should be the last)
    if args.fp16:
        from apex import amp  # Apex is only required if we use fp16 training
        model, optimizer = amp.initialize(model, optimizer, opt_level=args.fp16)
        #model.half()
    if args.distributed:
        model = DistributedDataParallel(model, device_ids=[args.local_rank], output_device=args.local_rank)
        #model = torch.nn.DataParallel(model)
    logger.info("Prepare datasets")
    train_loader, val_loader, train_sampler, valid_sampler = get_data_loaders(args, tokenizer)

    # Training function and trainer
    def update(engine, batch):
        
        model.train()
        batch = tuple(input_tensor.to(args.device) for input_tensor in batch)
        input_ids, mc_token_ids, lm_labels, mc_labels, token_type_ids = batch
        
        (lm_loss), (mc_loss), *_ = model(
            input_ids, token_type_ids=token_type_ids, mc_token_ids=mc_token_ids,
            mc_labels=mc_labels, lm_labels=lm_labels
        )
        loss = (lm_loss * args.lm_coef + mc_loss * args.mc_coef) / args.gradient_accumulation_steps
        if args.fp16:
            with amp.scale_loss(loss, optimizer) as scaled_loss:
                scaled_loss.backward()
            torch.nn.utils.clip_grad_norm_(amp.master_params(optimizer), args.max_norm)
        else:
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.max_norm)
        if engine.state.iteration % args.gradient_accumulation_steps == 0:
            optimizer.step()
            optimizer.zero_grad()
        return loss.item()
    trainer = Engine(update)

    # Evaluation function and evaluator (evaluator output is the input of the metrics)
    def inference(engine, batch):
        model.eval()
        with torch.no_grad():
            batch = tuple(input_tensor.to(args.device) for input_tensor in batch)
            input_ids, mc_token_ids, lm_labels, mc_labels, token_type_ids = batch
            #logger.info(tokenizer.decode(input_ids[0, -1, :].tolist()))
            # if we dont send labels to model, it doesnt return losses
            lm_logits, mc_logits, *_ = model(
                input_ids, token_type_ids=token_type_ids, mc_token_ids=mc_token_ids,
            )
            lm_logits_flat_shifted = lm_logits[..., :-1, :].contiguous().view(-1, lm_logits.size(-1))
            lm_labels_flat_shifted = lm_labels[..., 1:].contiguous().view(-1)
            return (lm_logits_flat_shifted, mc_logits), (lm_labels_flat_shifted, mc_labels)
    evaluator = Engine(inference)

    # Attach evaluation to trainer: we evaluate when we start the training and at the end of each epoch
    trainer.add_event_handler(Events.EPOCH_COMPLETED, lambda _: evaluator.run(val_loader))
    if args.n_epochs < 1:
        trainer.add_event_handler(Events.COMPLETED, lambda _: evaluator.run(val_loader))
    if args.eval_before_start:
        trainer.add_event_handler(Events.STARTED, lambda _: evaluator.run(val_loader))

    # Make sure distributed data samplers split the dataset nicely between the distributed processes
    if args.distributed:
        trainer.add_event_handler(Events.EPOCH_STARTED, lambda engine: train_sampler.set_epoch(engine.state.epoch))
        evaluator.add_event_handler(Events.EPOCH_STARTED, lambda engine: valid_sampler.set_epoch(engine.state.epoch))

    # Linearly decrease the learning rate from lr to zero
    scheduler = PiecewiseLinear(optimizer, "lr", [(0, args.lr), (args.n_epochs * len(train_loader), 0.0)])
    trainer.add_event_handler(Events.ITERATION_STARTED, scheduler)

    # Prepare metrics - note how we compute distributed metrics
    RunningAverage(output_transform=lambda x: x).attach(trainer, "loss")
    metrics = {"nll": Loss(torch.nn.CrossEntropyLoss(ignore_index=-1), output_transform=lambda x: (x[0][0], x[1][0])),
               "accuracy": Accuracy(output_transform=lambda x: (x[0][1], x[1][1]))}
    metrics.update({"average_nll": MetricsLambda(average_distributed_scalar, metrics["nll"], args),
                    "average_accuracy": MetricsLambda(average_distributed_scalar, metrics["accuracy"], args)})
    metrics["average_ppl"] = MetricsLambda(math.exp, metrics["average_nll"])
    for name, metric in metrics.items():
        metric.attach(evaluator, name)

    # On the main process: add progress bar, tensorboard, checkpoints and save model, configuration and tokenizer before we start to train
    if args.local_rank in [-1, 0]:
        pbar = ProgressBar(persist=True)
        pbar.attach(trainer, metric_names=["loss"])
        evaluator.add_event_handler(Events.COMPLETED, lambda _: pbar.log_message("Validation: %s" % pformat(evaluator.state.metrics)))

        log_dir = make_logdir(args.save_path)
        tb_logger = TensorboardLogger(log_dir)

        tb_logger.attach(trainer, log_handler=OutputHandler(tag="training", metric_names=["loss"]), event_name=Events.ITERATION_COMPLETED)
        tb_logger.attach(trainer, log_handler=OptimizerParamsHandler(optimizer), event_name=Events.ITERATION_STARTED)
        tb_logger.attach(evaluator, log_handler=OutputHandler(tag="validation", metric_names=list(metrics.keys()), another_engine=trainer), event_name=Events.EPOCH_COMPLETED)

        checkpoint_handler = ModelCheckpoint(log_dir, 'checkpoint', save_interval=1, n_saved=3)
        trainer.add_event_handler(Events.EPOCH_COMPLETED, checkpoint_handler, {'mymodel': getattr(model, 'module', model)})  # "getattr" takes care of distributed encapsulation

        torch.save(args, log_dir + '/model_training_args.bin')
        getattr(model, 'module', model).config.to_json_file(os.path.join(log_dir, CONFIG_NAME))
        tokenizer.save_pretrained(log_dir)

    # Run the training
    trainer.run(train_loader, max_epochs=args.n_epochs)

    # On the main process: close tensorboard logger and rename the last checkpoint (for easy re-loading with OpenAIGPTModel.from_pretrained method)
    if args.local_rank in [-1, 0] and args.n_epochs > 0:
        os.rename(checkpoint_handler._saved[-1][1][-1], os.path.join(log_dir, WEIGHTS_NAME))  # TODO: PR in ignite to have better access to saved file paths (cleaner)
        tb_logger.close()

if __name__ == "__main__":
    train()
