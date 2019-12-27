python3 \
    -m torch.distributed.launch  --nnodes 1  --nproc_per_node 8 \
	train.py \
	--model_type gpt2_bpe_cn \
	--model_checkpoint /public/transfer-learning-conv-ai/model_checkpoint_345_32k \
	--dataset_cache /public/transfer-learning-conv-ai/xinli-qa-dialog-convai-GPT2BPETokenizer_CN_32K_BPE-cache/cache \
    --save_path /public/transfer-learning-conv-ai/runs \
	--train_batch_size 1 \
	--valid_batch_size 1 \
	--lr 6.25e-5 \
	--n_epochs 50 \
	--fp16 "O2" \
#-m torch.distributed.launch  --nnodes 1  --nproc_per_node 8 \
