We create a Hierarchical Transformer for multi-document summarization and an attention-aware inference algorithm for neural abstractive summarization, we apply this inference algorithm to multi-document summarization, but it can also be used in a single-document summarization straightforwardly.

Preparation
-------
 You can find the best checkpoints from https://pan.baidu.com/s/1Jiccf2_f9zJ4CB7e0V2coA code: e3vd, inclduing the best ckpt for the summarization model and ckpts for the attention prediction model.
 
 The ranked WikiSum dataset from https://github.com/nlpyang/hiersumm
 
 You should dowload above checkpoints and dataset and put them in your project.



 Train HT
 -----
    python main.py --mode train --ckpt_path ./checkpoints/train_large_p_3d_30 --batch_size 16 --epoch 5 --para_len 100 --para_num 30
   
   This is to train the HT, we use default hyper-parameters of the model defined in the paper.
   
 Valid HT
 ------
    python main.py --mode valid --ckpt_path ./checkpoints/train_large_p_3d_30 
    
   Validation is to find the best checkpoint, You don't need to do this because we have provided the best ckeckpoints.
   
  Train att-pre model
  ------
     python main.py --mode train_att --ckpt_path_att ./checkpoints2/3d_l 
     
   This is to train the attention prediction model, we use default hyper-parameters of the model defined in the paper.
   
 Valid att-pre model
 -----
     python main.py --mode valid_att --ckpt_path_att ./checkpoints2/3d_l 
     
     
  Generation
  ------
     python main.py --mode gen --ckpt_path_att ./checkpoints2/3d_l --ckpt_path ./checkpoints/train_large_p_3d_30 --beta 0.8 --compress_s 25 --beam_size 5 --block_n_grams 3 -- block_n_words_before 2
     
   This is to use HT and att-aware inference to generate summaries, the results are saved in a txt file. 
   We use beam search, and two regulariztions to prevent repetitive grams during inference.
   when beta==0, it is a vanilla beam search without att-aware inference algorithm.
   when compress_s==30, there is no compression.
     
     
