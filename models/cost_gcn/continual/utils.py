from enum import Enum
from torch import Tensor
from torch.nn import Module
from functools import wraps
from typing import Callable


class FillMode(Enum):
    REPLICATE = "replicate"
    ZEROS = "zeros"


def unsqueezed(instance: Module, dim: int = 2):
    def decorator(func: Callable[[Tensor], Tensor]):
        @wraps(func)
        def call(x: Tensor) -> Tensor:
            x = x.unsqueeze(dim)
            x = func(x)
            x = x.squeeze(dim)
            return x

        return call

    instance.forward_regular = instance.forward
    instance.forward = decorator(instance.forward)

    return instance
