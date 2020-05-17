We create a Hierarchical Transformer for multi-document summarization and an attention-aware inference algorithm for neural abstractive summarization, we apply this inference algorithm to multi-document summarization, but it can also be used in a single-document summarization straightforwardly.

Preparation
-------
 You can find the best checkpoints from https://pan.baidu.com/s/1Jiccf2_f9zJ4CB7e0V2coA code: e3vd, inclduing the best ckpt for the summarization model and ckpts for the attention prediction model.
 
 The ranked WikiSum dataset from https://github.com/nlpyang/hiersumm
 
 You should dowload above checkpoints and dataset and put them in your project.


Summarization Model
----------
 Traning
 -----
    python main.py --mode train --ckpt_path ./checkpoints/train_large_p_3d_30 --batch_size 16 --epoch 5 --para_len 100 --para_num 30
   
   This is to train Parallel HT, we use default hyper-parameters of the model defined in the paper.
