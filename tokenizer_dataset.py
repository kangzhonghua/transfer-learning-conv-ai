import glob
import os
import time
import json
from tqdm import tqdm
from multiprocessing import Pool
import sys

sys.path.insert(0, '../transformers/examples/')

from tokenization_cn import GPT2Tokenizer_cn
print("load tokenizer ...\n")
tokenizer = GPT2Tokenizer_cn.from_pretrained("./model_checkpoint")

base_dir = "/home/Public/data/transfer-learning/output/output-qa/xinli001_jiandanxinli-convai"
output_dir = "/home/Public/data/transfer-learning/output/output-qa/xinli001_jiandanxinli-convai-bpe"

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

start = time.time()

processes = 32


# MAX_LINE_LEN = 655350000

def proc_json(tokenizer, json_filename, bpe_filename):
    if not os.path.exists(json_filename):
        return

    with open(json_filename, "r", encoding="utf-8") as f:
        personachat = json.loads(f.read())

    def tokenize(obj):
        if isinstance(obj, str):
            ids = tokenizer.convert_tokens_to_ids(tokenizer.tokenize(obj))
            return ids
        if isinstance(obj, dict):
            return dict((n, tokenize(o)) for n, o in obj.items())
        return list(tokenize(o) for o in obj)

    personachat = tokenize(personachat)

    with open(os.path.join(output_dir, bpe_filename), "w", encoding='utf8') as output_file:
        output_file.write(json.dumps(personachat, ensure_ascii=False, indent=4, separators=(',', ': ')))


def chunks(l, n):
    out = []
    for i in range(0, len(l), n):
        out.append(l[i:i + n])
    return out


def process(args):
    i, file_arg_list = args
    good_files = 0


    for file_arg in file_arg_list:
        path, short_name = os.path.split(file_arg)
        name, ext = os.path.splitext(short_name)

        proc_json(tokenizer, file_arg, os.path.join(output_dir, f"{name}_bpe.json"))
        good_files += 1

    return good_files


#json_file_list = ["/home/Public/data/transfer-learning/output/output-qa/xinli001_jiandanxinli-convai/xinli_qax_0_convai.json"]
json_file_list = glob.glob(os.path.join(base_dir, "*.json"))

json_file_count = len(json_file_list)
print(f"json file list size:{json_file_count}\n")

file_chunks = chunks(json_file_list, 1)
print("file_chunks size:", len(file_chunks))

start = time.time()
pool = Pool(processes=processes)
good = 0
for g in tqdm(pool.imap(process, enumerate(file_chunks)), total=len(file_chunks)):
    good += g

end = time.time()

print("Done! In {:.2f}s, {} / {} good files.".format(end - start, str(good), str(len(file_chunks))))