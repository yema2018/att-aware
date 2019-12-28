from __future__ import absolute_import, division, print_function, unicode_literals

import tensorflow as tf
import numpy as np
from sen_encoder import *


def create_look_ahead_mask(size):
  mask = 1 - tf.linalg.band_part(tf.ones((size, size)), -1, 0)
  return mask  # (seq_len, seq_len)


def cal_past_att(att_dists):
    shape = att_dists.shape
    past_att = tf.constant(0, tf.float32, [shape[0], 1, shape[-1]])
    for i in range(shape[1] - 1):
        temp = tf.reduce_sum(att_dists[:, :i + 1, :], axis=1, keepdims=True)
        past_att = tf.concat((past_att, temp), axis=1)
    return past_att


class Encoder(tf.keras.layers.Layer):
    def __init__(self, word_enc_layer, d_model, num_heads, dff, input_vocab_size,
                 para_num, w_emb, rate):
        super(Encoder, self).__init__()

        self.para_encoder = SenEncoder(word_enc_layer, d_model, num_heads, dff, input_vocab_size, w_emb, rate)
        self.pos_encoding = positional_encoding(para_num, d_model)

        # self.rank_embedding = tf.keras.layers.Embedding(para_num + 1, d_model, trainable=True)
        # self.relative_sentence_pos = tf.keras.layers.Embedding(para_num + 1, d_model)

        self.para_num = para_num
        self.d_model = d_model

    def call(self, inp, training, ranks):
        shape = inp.shape

        inp = tf.reshape(inp, shape=[-1, shape[-1]])  # shape == (batch_size * para_num, inp_seq_len)
        padding_mask_l = create_padding_mask(inp)

        padding_mask = create_padding_mask(inp)
        output_mask = create_output_mask(inp)

        # (batch_size * para_num, d_model), (batch_size * para_num, inp_seq_len, d_model)
        para_encoder, con_words = self.para_encoder(inp, training, padding_mask, output_mask)

        para_encoder = tf.reshape(para_encoder, [-1, self.para_num, self.d_model])  # (batch_size, para_num, d_model)
        # para_encoder += self.rank_embedding(ranks)
        para_encoder += self.pos_encoding[:, :self.para_num, :]

        return para_encoder, con_words, padding_mask_l


class DecoderLayer(tf.keras.layers.Layer):
    def __init__(self, d_model, num_heads, dff, para_num, rate, para_inter_layer=2, hisum=False):
        super(DecoderLayer, self).__init__()
        self.para_num = para_num

        self.mha1 = MultiHeadAttention(d_model, num_heads)
        self.mha2_g = MultiHeadAttention(d_model, num_heads)
        self.mha2_l = MultiHeadAttention(d_model, num_heads)

        self.ffn = point_wise_feed_forward_network(d_model, dff)

        self.layernorm1 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = tf.keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm3 = tf.keras.layers.LayerNormalization(epsilon=1e-6)

        self.dropout1 = tf.keras.layers.Dropout(rate)
        self.dropout2 = tf.keras.layers.Dropout(rate)
        self.dropout3 = tf.keras.layers.Dropout(rate)

        if hisum:
            self.para_enc = [EncoderLayer(d_model, num_heads, dff, rate) for _ in range(para_inter_layer)]
            self.pl = para_inter_layer

        self.hisum = hisum

    def call(self, x, enc_g, enc_l, training, look_ahead_mask, padding_mask_g, padding_mask_l):
        """

        :param enc_g: shape == (batch_size, para_num, d_model)
        :param enc_l: shape == (batch_size * para_num, inp_seq_len, d_model)

        """

        attn1, _ = self.mha1(x, x, x, look_ahead_mask)  # (batch_size, target_seq_len, d_model)
        attn1 = self.dropout1(attn1, training=training)
        out1 = self.layernorm1(attn1 + x)

        if self.hisum:
            for i in range(self.pl):
                enc_g = self.para_enc[i](enc_g, training, padding_mask_g)

        attn_g, attn_weights_g = self.mha2_g(
            enc_g, enc_g, out1, padding_mask_g)  # attn_weights_g.shape == (batch_size, tar_seq_len, para_num)

        words_weights = None

        if not self.hisum:
            out1r = tf.tile(out1, [self.para_num, 1, 1])   # (batch_size * para_num, target_seq_len, d_model)
            shape = tf.shape(attn1)

            attn_weights_g1 = tf.expand_dims(attn_weights_g, -1)
            attn2, attn_weights_l = self.mha2_l(enc_l, enc_l, out1r, padding_mask_l)

            # attn2.shape = (batch_size, tar_seq_len, para_num, d_model)
            # attn_weights_l.shape==(batch_size, tar_seq_len, para_num, inp_seq_len)
            attn2 = tf.reshape(attn2, shape=(shape[0], shape[1], -1, shape[-1]))
            attn_weights_l = tf.reshape(attn_weights_l, shape=(shape[0], shape[1], self.para_num, -1))
            attn2 = tf.reduce_sum(tf.multiply(attn2, attn_weights_g1), axis=-2)  # (batch_size, tar_seq_len, d_model)

            attn2 = self.dropout2(attn2 + attn_g, training=training)
            out2 = self.layernorm2(attn2 + out1)  # (batch_size, target_seq_len, d_model)
            words_weights = tf.multiply(attn_weights_l, attn_weights_g1)  # (batch_size, tar_seq_len, para_num, inp_seq_len)
        else:
            attn_g = self.dropout2(attn_g, training=training)
            out2 = self.layernorm2(attn_g + out1)  # (batch_size, target_seq_len, d_model)

        ffn_output = self.ffn(out2)  # (batch_size, target_seq_len, d_model)
        ffn_output = self.dropout3(ffn_output, training=training)
        out3 = self.layernorm3(ffn_output + out2)  # (batch_size, target_seq_len, d_model)

        return out3, words_weights, attn_weights_g


class Decoder(tf.keras.layers.Layer):
    def __init__(self, num_layers, d_model, num_heads, dff, target_vocab_size, w_emb, para_num, rate):
        super(Decoder, self).__init__()

        self.d_model = d_model
        self.num_layers = num_layers

        self.embedding = w_emb
        self.pos_encoding = positional_encoding(target_vocab_size, d_model)

        self.dec_layers = [DecoderLayer(d_model, num_heads, dff, para_num, rate, hisum=False)
                           for _ in range(self.num_layers)]

        self.dropout = tf.keras.layers.Dropout(rate)

    def call(self, x, enc_g, enc_l, training, ranks, padding_mask_l):
        padding_mask_g = create_padding_mask(ranks)
        look_ahead_mask = create_look_ahead_mask(tf.shape(x)[1])
        dec_target_padding_mask = create_padding_mask(x)
        combined_mask = tf.maximum(dec_target_padding_mask, look_ahead_mask)

        seq_len = tf.shape(x)[1]
        para_weights = 0
        words_weights = None

        x = self.embedding(x)  # (batch_size, target_seq_len, d_model)
        x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
        x += self.pos_encoding[:, :seq_len, :]

        x = self.dropout(x, training=training)

        for i in range(self.num_layers):
            x, words_weights, pw = self.dec_layers[i](x, enc_g, enc_l, training,
                                                   combined_mask, padding_mask_g, padding_mask_l)
            para_weights += pw

        # x.shape == (batch_size, target_seq_len, d_model)
        # words_weights.shape == (batch_size, tar_seq_len, para_num, inp_seq_len)
        # para_weights.shape == (batch_size, tar_seq_len, para_num)
        return x, words_weights, para_weights


class MyModel(tf.keras.Model):
    def __init__(self, num_layers, d_model, num_heads, dff, vocab_size, para_num, rate):
        super(MyModel, self).__init__()

        self.num_layers = num_layers

        self.vocab_size = vocab_size

        w_emb = tf.keras.layers.Embedding(vocab_size, d_model, trainable=True)
        self.encoder = Encoder(num_layers, d_model, num_heads, dff, vocab_size, para_num, w_emb, rate)

        self.decoder = Decoder(num_layers, d_model, num_heads, dff, vocab_size, w_emb, para_num, rate)

        self.out_layer = tf.keras.layers.Dense(vocab_size, activation=tf.nn.softmax)
        # self.p_layer = tf.keras.layers.Dense(1, activation=tf.nn.sigmoid)

    def cal_final_dist(self, vocab_dists, att_dists, p_gen, encoded_sen_x):
        """
        :param vocab_dists: shape = (batch_size, tar_seq_len, vocab_size)
        :param att_dists: shape = (batch_size, tar_seq_len, para_num * inp_seq_len)
        :param p_gen: shape = (batch_size, tar_seq_len, 1)
        :param encoded_sen_x: shape = (batch_size, para_num, inp_seq_len)
        :return: final distributions, shape = (batch_size, tar_seq_len, extended_vocab_size)
        """

        tar_seq_len = vocab_dists.shape[1]
        batch_size = vocab_dists.shape[0]
        encoded_sen_x = tf.stack([encoded_sen_x for _ in range(tar_seq_len)], axis=1)
        encoded_sen_x = tf.reshape(encoded_sen_x, shape=[batch_size, tar_seq_len, -1])

        vocab_dists = tf.multiply(vocab_dists, p_gen)
        att_dists = tf.multiply(att_dists, (1-p_gen))

        extend_vsize = self.vocab_size + self.oov_size
        extra_zeros = tf.zeros(shape=(batch_size, tar_seq_len, self.oov_size))
        # vocab_dists_extended.shape == (batch_size, tar_seq_len, extend_vsize)
        vocab_dists_extended = tf.concat(axis=-1, values=[vocab_dists, extra_zeros])

        # reshape to (batch_size * tar_seq_len, para_num * inp_seq_len)
        encoded_sen_x = tf.reshape(encoded_sen_x, shape=[batch_size * tar_seq_len, -1])
        shape = encoded_sen_x.shape
        att_dists = tf.reshape(att_dists, shape=[shape[0], -1])
        batch_nums = tf.range(0, limit=shape[0])  # shape (batch_size)
        batch_nums = tf.expand_dims(batch_nums, 1)  # shape (batch_size, 1)
        batch_nums = tf.tile(batch_nums, [1, shape[-1]])
        encoded_sen_x = tf.cast(encoded_sen_x, tf.int32)
        indices = tf.stack((batch_nums, encoded_sen_x), axis=2)
        att_dists_projected = tf.scatter_nd(indices, att_dists, shape=[shape[0], extend_vsize])
        # reshape to (batch_size, tar_seq_len, extend_vsize)
        att_dists_projected = tf.reshape(att_dists_projected, shape=[batch_size, tar_seq_len, -1])

        final_dists = vocab_dists_extended + att_dists_projected

        return final_dists

    def call(self, inp, training, ranks, tar_inp, tar_real=None, cal_pw=False):
        global_info, con_words, padding_mask_l = self.encoder(inp, training, ranks)

        decoder_out, att_dists, para_weights = self.decoder(tar_inp, global_info,
                                                            con_words, training, ranks, padding_mask_l)
        # para_weights = para_weights[1]

        pw = None
        if cal_pw:
            if tar_real is None:
                pw = tf.reduce_sum(para_weights, axis=1)
            else:
                tar_mask = tf.math.logical_not(tf.math.equal(tar_real, 0))
                tar_mask = tf.cast(tar_mask, dtype=para_weights.dtype)
                pw = tf.reduce_sum(tf.expand_dims(tar_mask, axis=-1) * para_weights, axis=1)

        vocab_dists = self.out_layer(decoder_out)  # (batch_size, target_seq_len, vocab_size)

        # p_gen = self.p_layer(decoder_out)   # shape = (batch_size, tar_seq_len, 1)
        #
        # shape = att_dists.shape
        # tt = tf.reshape(att_dists, [shape[0], shape[1], -1])
        # att_dists = tt / tf.reduce_sum(tt, axis=-1, keepdims=True)  # (batch_size, tar_inp_seq, para_num * inp_seq_len)
        #
        # with tf.device('/cpu:0'):
        #     final_dists = self.cal_final_dist(vocab_dists, att_dists, p_gen, inp_x)

        return vocab_dists, pw, global_info


if __name__ == "__main__":
    inp = tf.ones([32, 100, 256])
    ranks = tf.ones([32, 100])
    tar_inp = tf.ones([32, 512])
    sp = tf.ones([32, 100])
    encoded_sen_x = tf.ones([32, 100, 256])
    model =MyModel(2, 256, 4, 1028, 30000, 40000, 100, 0.5)
    a = model(inp, True, ranks, tar_inp,sp,encoded_sen_x)
    print(a)

