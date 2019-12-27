#/bin/bash
  /home/kangzh/miniconda3/envs/python3.6/bin/python \
  interact_v4.py \
  --model_type gpt2_bpe_cn \
  --model_checkpoint /home/kangzh/transfer-learning-conv-ai/model_checkpoint_345_32k_0565000_Distractor_test \
  --dataset_cache /home/kangzh/transfer-learning-conv-ai/dataset_cache_GPT2BPETokenizer_CN_Distractor_test/cache  \
  --min_length 125 \
  --max_length 1000  \
  --temperature 0.7 \
  --top_p 0.9

