import torch
import torch.nn.functional as F

from fastNLP.core.utils import CheckError
from fastNLP.core.utils import CheckRes
from fastNLP.core.utils import _get_arg_list
from fastNLP.core.utils import _map_args
from fastNLP.core.utils import get_func_signature
from fastNLP.core.utils import _build_args
from fastNLP.core.utils import _check_function_or_method


class LossBase(object):
    def __init__(self):
        # key: name in target function; value: name in output function
        self.param_map = {}
        self._checked = False

    def get_loss(self, *args, **kwargs):
        raise NotImplementedError

    def __call__(self, output_dict, target_dict, force_check=False):
        """
        :param output_dict: A dict from forward function of the network.
        :param target_dict: A dict from DataSet.batch_y.
        :param force_check: Boolean. Force to check the mapping functions when it is running.
        :return:
        """
        args, defaults, defaults_val, varargs, kwargs = _get_arg_list(self.get_loss)
        if varargs is not None:
            raise RuntimeError(
                f"The function {get_func_signature(self.get_loss)} should not use Positional Argument."
            )

        param_map = self.param_map
        if args is None:
            raise RuntimeError(
                f"There is not any param in function{get_func_signature(self.get_loss)}"
            )
        self._checked = self._checked and not force_check
        if not self._checked:
            for keys in args:
                if keys not in param_map:
                    param_map.update({keys: keys})
            if defaults is not None:
                for keys in defaults:
                    if keys not in param_map:
                        param_map.update({keys: keys})
            self.param_map = param_map
        # param map: key= name in get_loss function, value= name in param dict
        reversed_param_map = {val: key for key, val in param_map.items()}
        # reversed param map: key= name in param dict, value= name in get_loss function

        duplicated = []
        missing = []
        if not self._checked:
            for keys, val in output_dict.items():
                if keys in target_dict.keys():
                    duplicated.append(keys)

        param_val_dict = {}
        for keys, val in output_dict.items():
            param_val_dict.update({keys: val})
        for keys, val in target_dict.items():
            param_val_dict.update({keys: val})

        if not self._checked:
            for keys in args:
                if param_map[keys] not in param_val_dict.keys():
                    missing.append(keys)

        if len(duplicated) > 0 or len(missing) > 0:
            raise CheckError(
                CheckRes(missing=missing, unused=[], duplicated=duplicated, required=[], all_needed=[]),
                func_signature=get_func_signature(self.get_loss)
            )

        self._checked = True

        param_map_val = _map_args(reversed_param_map, **param_val_dict)
        param_value = _build_args(self.get_loss, **param_map_val)

        loss = self.get_loss(**param_value)

        if not (isinstance(loss, torch.Tensor) and len(loss.size()) == 0):
            if not isinstance(loss, torch.Tensor):
                raise RuntimeError(f"loss ERROR: loss except a torch.Tensor but get {type(loss)}")
            raise RuntimeError(f"loss ERROR: the size of loss except torch.Size([]) but got {loss.size}")

        return loss


class NewLoss(LossBase):
    def __init__(self, func, key_map=None, **kwargs):
        super(NewLoss, self).__init__()
        _check_function_or_method(func)
        if key_map is not None:
            if not isinstance(key_map, dict):
                raise RuntimeError(f"Loss error: key_map except a {type({})} but got a {type(key_map)}")
            self.param_map = key_map
        if len(kwargs) > 0:
            for key, val in kwargs.items():
                self.param_map.update({key: val})

        self.get_loss = func


class L1Loss(LossBase):
    def __init__(self):
        super(L1Loss, self).__init__()
        self.get_loss = F.l1_loss


class BCELoss(LossBase):
    def __init__(self):
        super(BCELoss, self).__init__()
        self.get_loss = F.binary_cross_entropy


class NLLLoss(LossBase):
    def __init__(self):
        super(NLLLoss, self).__init__()
        self.get_loss = F.nll_loss


class LossInForward(LossBase):
    def __init__(self, loss_key='loss'):
        super().__init__()
        if not isinstance(loss_key, str):
            raise TypeError(f"Only str allowed for loss_key, got {type(loss_key)}.")
        self.loss_key = loss_key

    def get_loss(self, **kwargs):
        if self.loss_key not in kwargs:
            check_res = CheckRes(missing=[self.loss_key],
                                 unused=[],
                                 duplicated=[],
                                 required=[],
                                 all_needed=[],
                                 varargs=[])
            raise CheckError(check_res=check_res, func_signature=get_func_signature(self.get_loss))

    def __call__(self, output_dict, predict_dict, force_check=False):

        loss = self.get_loss(**output_dict)

        if not (isinstance(loss, torch.Tensor) and len(loss.size()) == 0):
            if not isinstance(loss, torch.Tensor):
                raise TypeError(f"loss ERROR: loss except a torch.Tensor but got {type(loss)}")
            raise RuntimeError(f"loss ERROR: the size of loss except torch.Size([]) but got {loss.size}")

        return loss


def _prepare_losser(losser):
    if losser is None:
        losser = LossInForward()
        return losser
    elif isinstance(losser, LossBase):
        return losser
    else:
        raise TypeError(f"Type of losser should be `fastNLP.LossBase`, got {type(losser)}")


def squash(predict, truth, **kwargs):
    '''To reshape tensors in order to fit Loss functions in pytorch

    :param predict	: Tensor, model output
    :param truth	: Tensor, truth from dataset
    :param **kwargs : extra arguments

    :return predict , truth: predict & truth after processing
    '''
    return predict.view(-1, predict.size()[-1]), truth.view(-1, )


def unpad(predict, truth, **kwargs):
    '''To process padded sequence output to get true loss
    Using pack_padded_sequence() method
    This method contains squash()

    :param predict	: Tensor, [batch_size , max_len , tag_size]
    :param truth	: Tensor, [batch_size , max_len]
    :param **kwargs : extra arguments, kwargs["lens"] is expected to be exsist
        kwargs["lens"] : list or LongTensor, [batch_size]
                      the i-th element is true lengths of i-th sequence

    :return predict , truth: predict & truth after processing
    '''
    if kwargs.get("lens") is None:
        return predict, truth
    lens = torch.LongTensor(kwargs["lens"])
    lens, idx = torch.sort(lens, descending=True)
    predict = torch.nn.utils.rnn.pack_padded_sequence(predict[idx], lens, batch_first=True).data
    truth = torch.nn.utils.rnn.pack_padded_sequence(truth[idx], lens, batch_first=True).data
    return predict, truth


def unpad_mask(predict, truth, **kwargs):
    '''To process padded sequence output to get true loss
    Using mask() method
    This method contains squash()

    :param predict	: Tensor, [batch_size , max_len , tag_size]
    :param truth	: Tensor, [batch_size , max_len]
    :param **kwargs : extra arguments, kwargs["lens"] is expected to be exsist
        kwargs["lens"] : list or LongTensor, [batch_size]
                      the i-th element is true lengths of i-th sequence

    :return predict , truth: predict & truth after processing
    '''
    if kwargs.get("lens") is None:
        return predict, truth
    mas = make_mask(kwargs["lens"], truth.size()[1])
    return mask(predict, truth, mask=mas)


def mask(predict, truth, **kwargs):
    '''To select specific elements from Tensor
    This method contains squash()

    :param predict	: Tensor, [batch_size , max_len , tag_size]
    :param truth	: Tensor, [batch_size , max_len]
    :param **kwargs : extra arguments, kwargs["mask"] is expected to be exsist
        kwargs["mask"] : ByteTensor, [batch_size , max_len]
                      the mask Tensor , the position that is 1 will be selected

    :return predict , truth: predict & truth after processing
    '''
    if kwargs.get("mask") is None:
        return predict, truth
    mask = kwargs["mask"]

    predict, truth = squash(predict, truth)
    mask = mask.view(-1, )

    predict = torch.masked_select(predict.permute(1, 0), mask).view(predict.size()[-1], -1).permute(1, 0)
    truth = torch.masked_select(truth, mask)

    return predict, truth


def make_mask(lens, tar_len):
    '''to generate a mask that select [:lens[i]] for i-th element
    embezzle from fastNLP.models.sequence_modeling.seq_mask

    :param lens		: list or LongTensor, [batch_size]
    :param tar_len	: int

    :return mask 	: ByteTensor
    '''
    lens = torch.LongTensor(lens)
    mask = [torch.ge(lens, i + 1) for i in range(tar_len)]
    mask = torch.stack(mask, 1)
    return mask


# map string to function. Just for more elegant using
method_dict = {
    "squash": squash,
    "unpad": unpad,
    "unpad_mask": unpad_mask,
    "mask": mask,
}

loss_function_name = {
    "L1Loss".lower(): torch.nn.L1Loss,
    "BCELoss".lower(): torch.nn.BCELoss,
    "MSELoss".lower(): torch.nn.MSELoss,
    "NLLLoss".lower(): torch.nn.NLLLoss,
    "KLDivLoss".lower(): torch.nn.KLDivLoss,
    "NLLLoss2dLoss".lower(): torch.nn.NLLLoss2d,  # every name should end with "loss"
    "SmoothL1Loss".lower(): torch.nn.SmoothL1Loss,
    "SoftMarginLoss".lower(): torch.nn.SoftMarginLoss,
    "PoissonNLLLoss".lower(): torch.nn.PoissonNLLLoss,
    "MultiMarginLoss".lower(): torch.nn.MultiMarginLoss,
    "CrossEntropyLoss".lower(): torch.nn.CrossEntropyLoss,
    "BCEWithLogitsLoss".lower(): torch.nn.BCEWithLogitsLoss,
    "MarginRankingLoss".lower(): torch.nn.MarginRankingLoss,
    "TripletMarginLoss".lower(): torch.nn.TripletMarginLoss,
    "HingeEmbeddingLoss".lower(): torch.nn.HingeEmbeddingLoss,
    "CosineEmbeddingLoss".lower(): torch.nn.CosineEmbeddingLoss,
    "MultiLabelMarginLoss".lower(): torch.nn.MultiLabelMarginLoss,
    "MultiLabelSoftMarginLoss".lower(): torch.nn.MultiLabelSoftMarginLoss,
}


class Loss(object):
    '''a Loss object is a callable object represents loss functions
    '''

    def __init__(self, loss_name, pre_pro=[squash], **kwargs):
        '''

        :param loss_name: str or None , the name of loss function
        :param pre_pro 	: list of function or str, methods to reform parameters before calculating loss
            the strings will be auto translated to pre-defined functions
        :param **kwargs: kwargs for torch loss function

        pre_pro funcsions should have three arguments: predict, truth, **arg
            predict and truth is the necessary parameters in loss function
            kwargs is the extra parameters passed-in when calling loss function
        pre_pro functions should return two objects, respectively predict and truth that after processed

        '''

        if loss_name is None:
            # this is useful when Trainer.__init__ performs type check
            self._loss = None
        else:
            if not isinstance(loss_name, str):
                raise NotImplementedError
            else:
                self._loss = self._get_loss(loss_name, **kwargs)

        self.pre_pro = [f if callable(f) else method_dict.get(f) for f in pre_pro]

    def add_pre_pro(self, func):
        '''add a pre_pro function

        :param func: a function or str, methods to reform parameters before calculating loss
            the strings will be auto translated to pre-defined functions
        '''
        if not callable(func):
            func = method_dict.get(func)
            if func is None:
                return
        self.pre_pro.append(func)

    @staticmethod
    def _get_loss(loss_name, **kwargs):
        '''Get loss function from torch

        :param loss_name: str, the name of loss function
        :param **kwargs: kwargs for torch loss function
        :return: A callable loss function object
        '''
        loss_name = loss_name.strip().lower()
        loss_name = "".join(loss_name.split("_"))

        if len(loss_name) < 4 or loss_name[-4:] != "loss":
            loss_name += "loss"
        return loss_function_name[loss_name](**kwargs)

    def get(self):
        '''This method exists just for make some existing codes run error-freely
        '''
        return self

    def __call__(self, predict, truth, **kwargs):
        '''call a loss function
        predict and truth will be processed by pre_pro methods in order of addition

        :param predict	: Tensor, model output
        :param truth 	: Tensor, truth from dataset
        :param **kwargs : extra arguments, pass to pre_pro functions
            for example, if used unpad_mask() in pre_pro, there should be a kwarg named lens
        '''
        for f in self.pre_pro:
            if f is None:
                continue
            predict, truth = f(predict, truth, **kwargs)

        return self._loss(predict, truth)