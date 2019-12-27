#/bin/bash
  /home/kangzh/miniconda3/envs/python3.6/bin/python \
  interact_v3.py \
  --model_type gpt2_bpe_cn \
  --model_checkpoint /home/kangzh/transfer-learning-conv-ai/model_checkpoint_345_32k_v7 \
  --dataset_cache /home/kangzh/transfer-learning-conv-ai/xinli001_jiandanxinli-qa.topics-convai-GPT2BPETokenizer_CN_32K_BPE-cache_v12.1/cache  \
  --min_length 125 \
  --max_length 1000  \
  --temperature 0.7 \
  --top_p 0.9

