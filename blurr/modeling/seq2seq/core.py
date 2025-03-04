# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/10_modeling-seq2seq-core.ipynb (unless otherwise specified).

__all__ = ['HF_Seq2SeqMetricsCallback', 'seq2seq_splitter']

# Cell
import ast, inspect, torch
from typing import Any, Callable, Dict, List, Optional, Union, Type

from datasets import load_metric as hf_load_metric, list_metrics as hf_list_metrics
from fastcore.all import *
from fastai.callback.all import *
from fastai.data.block import DataBlock, ColReader, CategoryBlock, MultiCategoryBlock, ColSplitter, RandomSplitter
from fastai.data.core import DataLoader, DataLoaders, TfmdDL
from fastai.imports import *
from fastai.learner import *
from fastai.losses import CrossEntropyLossFlat
from fastai.optimizer import Adam, OptimWrapper, params
from fastai.torch_core import *
from fastai.torch_imports import *
from fastprogress.fastprogress import progress_bar,master_bar
from transformers import (
    AutoModelForSequenceClassification, logging,
    PretrainedConfig, PreTrainedTokenizerBase, PreTrainedModel
)

import nltk
nltk.download('wordnet', quiet=True)

from ...utils import BLURR
from ...data.core import first_blurr_tfm
from ...data.seq2seq.core import (
    HF_Seq2SeqInput, HF_Seq2SeqBeforeBatchTransform, HF_Seq2SeqAfterBatchTransform, HF_Seq2SeqBlock,
    default_text_gen_kwargs
)
from ..core import HF_BaseModelWrapper, HF_BaseModelCallback

logging.set_verbosity_error()

# Cell
class HF_Seq2SeqMetricsCallback(Callback):
    """A callback that adds seq2seq metrics"""
    def __init__(
        self,
        # A dictionary of seq2seq metrics we want to use. See below and the various task specific seq2seq docs
        # for examples of how to configure this per task
        custom_metrics:dict=None,
        # Calculation of these metrics requires text generation, which is expensive.  You can choose to calculate
        # these metrics on every 'epoch', 'other_epoch', or 'last_epoch' instead (default: 'epoch')
        calc_every:str='epoch',
        # The token ID that should be ignored when calculating the loss
        ignore_token_id=CrossEntropyLossFlat().ignore_index,
        # Any keyword arguments to pass to the `hf_model.generate` method
        text_gen_kwargs:dict={},
        # Keyword arguments to apply to `HF_Seq2SeqMetricsCallback`
        **kwargs
    ):
        super().__init__(**kwargs)
        self.order = Recorder.order-1

        store_attr(self=self, names='custom_metrics, calc_every, ignore_token_id, text_gen_kwargs, kwargs')
        self.custom_metric_funcs, self.custom_metric_vals, self.do_calc = {}, {}, True

        if (custom_metrics is not None):
            for metric_name, metric_info_dict in custom_metrics.items():
                # self.custom_metric_funcs (tuple): the function to compute the metric and what should be returned,
                # if the "compute_func" is not defined, we assume it is a Hugging Face metric
                if ('compute_func' in metric_info_dict):
                    compute_func = metric_info_dict['compute_func']
                else:
                    compute_func = hf_load_metric(metric_name).compute

                compute_kwargs = metric_info_dict['compute_kwargs'] if ('compute_kwargs' in metric_info_dict) else {}
                metric_returns = metric_info_dict['returns']

                self.custom_metric_funcs[metric_name] = (partial(compute_func, **compute_kwargs),  metric_returns)

                # self.custom_metric_vals (list): all the custom metrics to report as a "ValueMetric"
                if (metric_name == 'rouge'):
                    self.custom_metric_vals.update({ rouge_type:None for rouge_type in metric_returns })
                elif (is_listy(metric_returns)):
                    self.custom_metric_vals.update({ f'{metric_name}_{ret_val}':None for ret_val in metric_returns })
                else:
                    self.custom_metric_vals.update({ metric_name:None })

        self.do_setup = True

    def setup(self):
        # one time setup code here.
        if (not self.do_setup): return

        # grab the hf_tokenizer from the HF_BeforeBatchTransform (used for rouge metrics)
        tfm = first_blurr_tfm(self.learn.dls)
        self.hf_tokenizer = tfm.hf_tokenizer
        self.tok_kwargs = tfm.tok_kwargs

        # use before batch tfm's text_gen_kwargs if user doesn't pass in their own kwargs
        if (len(self.text_gen_kwargs) == 0): self.text_gen_kwargs = tfm.text_gen_kwargs

        # add seq2seq generation specific metrics (rouge, bertscore, bleu, etc...) to learner metrics
        metric_keys = list(self.custom_metric_vals.keys())
        custom_metrics = L([ ValueMetric(partial(self.metric_value, metric_key=k), k) for k in metric_keys ])
        self.learn.metrics = self.learn.metrics + custom_metrics

        self.do_setup = False

    def before_fit(self): self.setup()

    # --- epoch begin/after phases ---
    def before_epoch(self):
        current_epoch_n = self.epoch + 1
        if (current_epoch_n == self.n_epoch): # we always calc on last epoch
            self.do_calc = True
        elif (self.calc_every == 'epoch'):
            self.do_calc = True
        elif (self.calc_every == 'other_epoch' and current_epoch_n % 2 == 0):
            self.do_calc = True
        else:
            self.do_calc = False

    # --- batch begin/after phases ---
    def after_batch(self):
        if (self.training or self.learn.y is None or self.custom_metrics is None or not self.do_calc): return

        # grab predicted and reference ids for any metrics that need them
        input_ids, attention_mask = self.xb[0]['input_ids'], self.xb[0]['attention_mask']
        gen_ids = self.learn.model.hf_model.generate(input_ids=input_ids,
                                                     attention_mask=attention_mask,
                                                     **self.text_gen_kwargs)

        self.generated_ids += gen_ids.tolist()
        self.refernce_ids += [ seq[seq != self.ignore_token_id].tolist()  for seq in self.yb[0] ]

    # --- validation begin/after phases ---
    def before_validate(self): self.generated_ids, self.refernce_ids = [], []

    def after_validate(self):
        if (self.learn.y is None or self.custom_metrics is None or not self.do_calc): return

        # fetch the generated prediction and reference tokens and texts
        gen_toks = [ self.hf_tokenizer.convert_ids_to_tokens(ids, skip_special_tokens=True)
                    for ids in self.generated_ids ]

        ref_toks = [ self.hf_tokenizer.convert_ids_to_tokens(ids, skip_special_tokens=True)
                    for ids in self.refernce_ids ]

        gen_texts = self.hf_tokenizer.batch_decode(self.generated_ids,
                                                   skip_special_tokens=True,
                                                   clean_up_tokenization_spaces=True)

        ref_texts = self.hf_tokenizer.batch_decode(self.refernce_ids,
                                                   skip_special_tokens=True,
                                                   clean_up_tokenization_spaces=True)

        # calculate any seq2seq metrics
        for metric_name, metric_info in self.custom_metric_funcs.items():
            compute_func, return_val = metric_info

            # some metrics work on tokens (bleu), some allow for multiple references (blue, sacrebleu), and most
            # work directly on the generated and reference texts; here blurr does the dirty work of getting your
            # preds/references formatted for the metric you are using
            if (metric_name == 'bleu'):
                predictions, references = gen_toks, [ [toks] for toks in ref_toks ]
            elif (metric_name == 'sacrebleu'):
                predictions, references = gen_texts, [ [txt] for txt in ref_texts ]
            else:
                predictions, references = gen_texts, ref_texts

            # calls the metrics "compute" function
            res = compute_func(predictions=predictions, references=references)

            # updates the custom_metric_vals with the metric's value
            if (metric_name == 'rouge'):
                for rouge_key, scores in res.items():
                    self.custom_metric_vals[rouge_key] = scores.mid.fmeasure
            if (metric_name == 'bertscore'):
                for score_key, score in res.items():
                    if (f'{metric_name}_{score_key}' not in self.custom_metric_vals): continue
                    self.custom_metric_vals[f'{metric_name}_{score_key}'] = np.array(score).mean().item()
            elif (is_listy(return_val)):
                for score_key, score in res.items():
                    if (f'{metric_name}_{score_key}' not in self.custom_metric_vals): continue
                    self.custom_metric_vals[f'{metric_name}_{score_key}'] = score
            else:
                self.custom_metric_vals[metric_name] = res[return_val]


    # --- for ValueMetric metrics ---
    def metric_value(self, metric_key): return self.custom_metric_vals[metric_key]

# Cell
def seq2seq_splitter(
    # A Hugging Face model
    m:PreTrainedModel,
    # The name of the architecture you are working with (e.g., bart, fsmt, pegasus, etc...)
    arch:str
):
    """Custom param splitter for summarization models"""
    model = m.hf_model if (hasattr(m, 'hf_model')) else m

    if arch in ['bart', 'blenderbot', 'blenderbot_small', 'fsmt', 'marian', 'mbart', 'pegasus']:
        embeds_modules = [
            model.model.encoder.embed_positions,
            model.model.encoder.embed_tokens,
            model.model.decoder.embed_positions,
            model.model.decoder.embed_tokens
        ]
        if arch != 'fsmt': embeds_modules.insert(0, model.model.shared)

        embeds = nn.Sequential(*embeds_modules)
        groups = L(embeds, model.model.encoder, model.model.decoder)
        return groups.map(params).filter(lambda el: len(el) > 0)

    if arch in['led']:
        embeds_modules = [
            model.led.encoder.embed_positions,
            model.led.encoder.embed_tokens,
            model.led.decoder.embed_positions,
            model.led.decoder.embed_tokens
        ]

        embeds = nn.Sequential(*embeds_modules)
        groups = L(embeds, model.led.encoder, model.led.decoder)
        return groups.map(params).filter(lambda el: len(el) > 0)

    if arch in['mt5', 't5']:
        embeds = nn.Sequential(
            model.shared,
            model.encoder.embed_tokens,
            model.decoder.embed_tokens
        )

        groups = L(embeds, model.encoder, model.decoder)
        return groups.map(params).filter(lambda el: len(el) > 0)

    if arch in ['prophetnet', 'xlm_prophetnet']:
        embeds = nn.Sequential(
            model.prophetnet.word_embeddings,
            model.prophetnet.encoder.word_embeddings,
            model.prophetnet.encoder.position_embeddings,
            model.prophetnet.decoder.word_embeddings,
            model.prophetnet.decoder.position_embeddings,
            model.prophetnet.decoder.ngram_embeddings
        )

        groups = L(embeds, model.prophetnet.encoder.layers, model.prophetnet.decoder.layers, model.lm_head)
        return groups.map(params).filter(lambda el: len(el) > 0)


    raise ValueError(f'seq2seq_splitter does not support this architecutre: {arch}')

# Cell
@typedispatch
def show_results(
    # This typedispatched `show_results` will be called for `HF_Seq2SeqInput` typed inputs
    x:HF_Seq2SeqInput,
    # Your targets
    y,
    # Your raw inputs/targets
    samples,
    # The model's predictions
    outs,
    # Your `Learner`. This is required so as to get at the Hugging Face objects for decoding them into
    # something understandable
    learner,
    # Your `show_results` context
    ctxs=None,
    # The maximum number of items to show
    max_n=6,
    # Any truncation your want applied to your decoded inputs
    input_trunc_at=None,
    # Any truncation your want applied to your decoded targets
    target_trunc_at=None,
    # If you want to override your Blurr transform's `text_gen_kwargs`, do that here
    text_gen_kwargs={},
     # Any other keyword arguments you want applied to `show_results`
    **kwargs
):
    tfm = first_blurr_tfm(learner.dls)
    hf_config = tfm.hf_config
    hf_tokenizer = tfm.hf_tokenizer
    ignore_token_id = tfm.ignore_token_id

    if (len(text_gen_kwargs) == 0): text_gen_kwargs = tfm.text_gen_kwargs

    gen_text_txts = learner.blurr_generate(x, **text_gen_kwargs)
    res = L([(
        hf_tokenizer.decode(s[0], skip_special_tokens=True)[:input_trunc_at],
        hf_tokenizer.decode(s[1][s[1] != ignore_token_id], skip_special_tokens=True)[:target_trunc_at],
        gen_txt[:target_trunc_at]
    ) for s, gen_txt in zip(samples, gen_text_txts) ])

    display_df(pd.DataFrame(res, columns=['text', 'target', 'prediction'])[:max_n])
    return ctxs