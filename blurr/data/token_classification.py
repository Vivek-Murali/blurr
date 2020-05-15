# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/01d_data-token-classification.ipynb (unless otherwise specified).

__all__ = ['HF_TokenTensorCategory', 'HF_TokenCategorize', 'HF_TokenCategoryBlock']

# Cell
import ast
from functools import reduce

from ..utils import *
from .core import *

import torch
from transformers import *
from fastai2.text.all import *

# Cell
class HF_TokenTensorCategory(TensorBase): pass

# Cell
class HF_TokenCategorize(Transform):
    "Reversible transform of a list of category string to `vocab` id"

    def __init__(self, vocab=None, ignore_token=None, ignore_token_id=None):
        self.vocab = None if vocab is None else CategoryMap(vocab)
        self.ignore_token = '[xIGNx]' if ignore_token is None else ignore_token
        self.ignore_token_id = CrossEntropyLossFlat().ignore_index if ignore_token_id is None else ignore_token_id

        self.loss_func, self.order = CrossEntropyLossFlat(ignore_index=self.ignore_token_id), 1

    def setups(self, dsets):
        if self.vocab is None and dsets is not None: self.vocab = CategoryMap(dsets)
        self.c = len(self.vocab)

    def encodes(self, labels):
        ids = [[self.vocab.o2i[lbl]] + [self.ignore_token_id]*(n_subtoks-1) for lbl, n_subtoks in labels]
        return HF_TokenTensorCategory(reduce(operator.concat, ids))

    def decodes(self, encoded_labels):
        return Category([(self.vocab[lbl_id]) for lbl_id in encoded_labels if lbl_id != self.ignore_token_id ])

# Cell
def HF_TokenCategoryBlock(vocab=None, ignore_token=None, ignore_token_id=None):
    "`TransformBlock` for single-label categorical targets"
    return TransformBlock(type_tfms=HF_TokenCategorize(vocab=vocab,
                                                       ignore_token=ignore_token,
                                                       ignore_token_id=ignore_token_id))

# Cell
@typedispatch
def build_hf_input(task:ForTokenClassificationTask, tokenizer, a_tok_ids, b_tok_ids=None, targets=None,
                   max_length=512, pad_to_max_length=True, truncation_strategy='longest_first'):

    res = tokenizer.prepare_for_model(a_tok_ids, b_tok_ids,
                                      max_length=max_length,
                                      pad_to_max_length=pad_to_max_length,
                                      truncation_strategy=truncation_strategy,
                                      return_special_tokens_mask=True,
                                      return_tensors='pt')

    input_ids = res['input_ids'][0]
    attention_mask = res['attention_mask'][0] if ('attention_mask' in res) else torch.tensor([-9999])
    token_type_ids = res['token_type_ids'][0] if ('token_type_ids' in res) else torch.tensor([-9999])

    # we assume that first target = the categories we want to predict for each token
    if (len(targets) > 0):
        target_cls = type(targets[0])
        idx_first_input_id = res['special_tokens_mask'].index(0)
        targ_ids = target_cls([ el*-100 if (el == 1) else targets[0][idx-idx_first_input_id].item()
                    for idx, el in enumerate(res['special_tokens_mask']) ])

        # just in case there are other targets, we modify the first with the padded targ_ids
        updated_targets = list(targets)
        updated_targets[0] = targ_ids
    else:
        updated_targets= list(targets)

    return HF_BaseInput([input_ids, attention_mask, token_type_ids]), tuple(updated_targets)