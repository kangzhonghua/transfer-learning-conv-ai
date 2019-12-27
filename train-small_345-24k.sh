python3 \
	train.py \
	--model_type gpt2_bpe_cn \
	--model_checkpoint ./model_checkpoint_small_345_24k \
	--dataset_cache ./dataset_cache_GPT2BPETokenizer_CN_24K_BPE/cache \
	--train_batch_size 1 \
	--valid_batch_size 1 \
	--lr 6.25e-5 \
	--n_epochs 50 \
	--fp16 "O2" \

    #-m torch.distributed.launch  --nnodes 1  --nproc_per_node 8 \
