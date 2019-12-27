#/bin/bash
/home/kangzh/miniconda3/envs/python3.6/bin/python \
  interact_v3.py \
  --model_type gpt2_bpe_cn \
  --model_checkpoint ./model_checkpoint_345_32k_v3_1 \
  --dataset_cache ./xinli001_jiandanxinli-qa.topics-convai-GPT2BPETokenizer_CN_32K_BPE-cache/cache  \
  --min_length 125 \
  --max_length 1000  \
  --temperature 0.7 \
  --top_p 0.9

