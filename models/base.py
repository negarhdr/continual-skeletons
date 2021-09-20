from operator import attrgetter
from collections import OrderedDict
import math
import numpy as np
import torch
import torch.nn as nn
from ride.core import Configs, RideMixin
from torch import Tensor

import continual as co
from models.utils import init_weights, unity, zero


class CoModelBase(RideMixin, co.Sequential):

    hparams: ...

    @staticmethod
    def configs() -> Configs:
        c = Configs()
        c.add(
            name="forward_mode",
            type=str,
            default="clip",
            choices=["clip", "frame"],
            strategy="choice",
            description="Run forward on a whole clip or a single frame.",
        )
        c.add(
            name="predict_after_frames",
            type=int,
            default=0,
            strategy="choice",
            description="Predict the final results after N frames.",
        )
        c.add(
            name="continual_temporal_fill",
            type=str,
            default="replicate",
            choices=["zeros", "replicate"],
            strategy="choice",
            description="Fill mode for samples along temporal dimension.",
        )
        c.add(
            name="pool_size",
            type=int,
            default=-1,
            description=(
                "Size of pooling layer. If `-1`, a pool size will be chosen, "
                "which matches the network delay to the input_shape"
            ),
        )
        return c

    def on_init_end(self, hparams, *args, **kwargs):
        # Shapes from Dataset:
        # num_channels, num_frames, num_vertices, num_skeletons
        (C_in, T, V, S) = self.input_shape
        N = self.hparams.batch_size

        reshape1 = co.Lambda(
            lambda x: x.permute(0, 3, 2, 1).contiguous().view(N, S * V * C_in),
        )
        data_bn = nn.BatchNorm1d(S * C_in * V)
        reshape2 = co.Lambda(
            lambda x: x.view(N, S, V, C_in)
            .permute(0, 1, 3, 2)
            .contiguous()
            .view(N * S, C_in, V)
        )

        def dummy(x):
            return x

        spatial_pool = co.Lambda(lambda x: x.view(N, S, 256, V).mean(3).mean(1))

        pool_size = self.hparams.pool_size
        if pool_size == -1:
            pool_size = math.ceil(
                (T - (self.receptive_field - 1) + 2 * self.padding) / self.stride
            )
        pool = co.AvgPool1d(pool_size)

        fc = co.Linear(256, self.num_classes, channel_dim=1)

        squeeze = co.Lambda(lambda x: x.squeeze(-1), takes_time=True)

        # Initialize weights
        init_weights(data_bn, bs=1)
        init_weights(fc, bs=self.num_classes)

        # Add blocks sequentially
        co.Sequential.__init__(
            self,
            OrderedDict(
                [
                    ("reshape1", reshape1),
                    ("data_bn", data_bn),
                    ("reshape2", reshape2),
                    ("layers", self.layers),
                    ("spatial_pool", spatial_pool),
                    ("dummy", co.Lambda(dummy, takes_time=True)),
                    ("pool", pool),
                    ("fc", fc),
                    ("squeeze", squeeze),
                ]
            ),
        )

        if getattr(self.hparams, "profile_model", False):
            (num_channels, num_frames, num_vertices, num_skeletons) = self.input_shape

            # A new output is created every `self.stride` frames.
            self.input_shape = (num_channels, self.stride, num_vertices, num_skeletons)

    def validate_attributes(self):
        attrgetter("parameters")(self)
        for hparam in CoModelBase.configs().names:
            attrgetter(f"hparams.{hparam}")(self)

        for attribute in [f"layers.layer{i+1}" for i in range(10)]:
            assert issubclass(type(attrgetter(attribute)(self)), torch.nn.Module)

    def load_state_dict(
        self,
        state_dict: "OrderedDict[str, Tensor]",
        strict: bool = True,
    ):
        def map_key(k: str):
            # Handle "layers.layer2.0.1.gcn.g_conv.0.weight" -> "layers.layer2.gcn.g_conv.0.weight"
            k = k.replace("0.1.", "")

            # Handle "layers.layer8.0.0.residual.t_conv.weight" ->layers.layer8.residual.t_conv.weight'
            k = k.replace("0.0.residual", "residual")
            return k

        long_keys = nn.Module.state_dict(self, keep_vars=True).keys()

        if len(long_keys - state_dict.keys()):
            short2long = {map_key(k): k for k in long_keys}
            state_dict = OrderedDict(
                [
                    (short2long[k], v)
                    for k, v in state_dict.items()
                    if strict or k in short2long
                ]
            )

        return nn.Module.load_state_dict(self, state_dict, strict)

    # def __call__(self, x: Tensor) -> Tensor:
    #     result = None
    #     if self.hparams.forward_mode == "clip" and self.training:
    #         result = self.forward_regular_unrolled(x)
    #     elif self.hparams.forward_mode == "clip" and not self.training:
    #         result = self.forward_regular_naive(x)
    #     elif self.hparams.forward_mode == "frame":
    #         result = self.forward_frame(x[:, :, 0])
    #     else:  # pragma: no cover
    #         raise RuntimeError("Model forward_mode should be one of {'clip', 'frame'}.")
    #     return result

    # def _forward_steps(self, x: Tensor) -> Tensor:
    #     """Efficient version of forward regular.
    #     Compute features layer by layer as in regular convolution.
    #     Produce prediction corresponding to last frame.
    #     """
    #     self.clean_state()

    #     N, C, T, V, M = x.shape
    #     x = x.permute(0, 4, 3, 1, 2).contiguous().view(N, M * V * C, T)

    #     x = torch.stack(
    #         [
    #             self.data_bn(x[:, :, i])
    #             .view(N, M, V, C)
    #             .permute(0, 1, 3, 2)
    #             .contiguous()
    #             .view(N * M, C, V)
    #             for i in range(T)
    #         ],
    #         dim=2,
    #     )

    #     for i in range(len(self.layers)):
    #         x = self.layers[f"layer{i + 1}"].forward_steps(x)
    #         # Discard frames from transient response
    #         discard = (
    #             self.layers[f"layer{i + 1}"].delay
    #             // self.layers[f"layer{i + 1}"].stride
    #         )
    #         x = x[:, :, discard:]

    #     # N*M, C, T, V
    #     _, C_new, T_new, _ = x.shape
    #     x = x.view(N, M, C_new, -1, V).mean(4).mean(1)
    #     assert self.pool.window_size == T_new
    #     x = [self.pool(x[:, :, t]) for t in range(T_new)]
    #     d = self.hparams.predict_after_frames - self.delay_conv_blocks
    #     i = d if d > 0 else -1
    #     x = self.fc(x[i])
    #     self.last_output = x
    #     return self.last_output

    # def _forward(self, x: Tensor) -> Tensor:
    #     """Clip-wise forward without state initialisation, but which is identical to the non-continual component

    #     Args:
    #         x (Tensor): Input Tensor

    #     Returns:
    #         Tensor: Output Tensor
    #     """
    #     N, C, T, V, M = x.size()
    #     x = x.permute(0, 4, 3, 1, 2).contiguous().view(N, M * V * C, T)
    #     x = self.data_bn.forward(x)
    #     x = (
    #         x.view(N, M, V, C, T)
    #         .permute(0, 1, 3, 4, 2)
    #         .contiguous()
    #         .view(N * M, C, T, V)
    #     )
    #     x = self.layers.forward(x)
    #     # N*M,C,T,V
    #     c_new = x.size(1)
    #     x = x.view(N, M, c_new, -1)
    #     x = x.mean(3).mean(1)
    #     x = self.fc(x)
    #     return x

    # def _forward_step(self, x: Tensor) -> Tensor:
    #     self.clean_state_on_shape_change(x.shape)
    #     N, C, V, M = x.shape
    #     x = x.permute(0, 3, 2, 1).contiguous().view(N, M * V * C, 1)
    #     x = self.data_bn.forward(x)
    #     x = x.view(N, M, V, C).permute(0, 1, 3, 2).contiguous().view(N * M, C, V)
    #     for i in range(len(self.layers)):
    #         x = self.layers[f"layer{i + 1}"].forward_step(x)
    #         if isinstance(x, co.TensorPlaceholder):
    #             return self.last_output
    #     # N*M,C,V
    #     C_new = x.size(1)
    #     x = x.view(N, M, C_new, V).mean(3).mean(1)
    #     x = self.pool(x)
    #     x = self.fc(x)
    #     self.last_output = x
    #     return x

    def clean_state_on_shape_change(self, shape):
        if not hasattr(self, "_current_input_shape"):
            self._current_input_shape = shape

        if self._current_input_shape != shape:
            self.clean_state()


class GraphConvolution(nn.Module):
    def __init__(self, in_channels, out_channels, A, bn_momentum=0.1, *args, **kwargs):
        super(GraphConvolution, self).__init__()
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.graph_attn = nn.Parameter(torch.from_numpy(A.astype(np.float32)))
        nn.init.constant_(self.graph_attn, 1)
        self.A = nn.Parameter(
            torch.from_numpy(A.astype(np.float32)), requires_grad=False
        )
        self.num_subset = 3
        self.g_conv = nn.ModuleList()
        for i in range(self.num_subset):
            self.g_conv.append(nn.Conv2d(in_channels, out_channels, 1))
            init_weights(self.g_conv[i], bs=self.num_subset)

        if in_channels != out_channels:
            self.gcn_residual = nn.Sequential(
                nn.Conv2d(in_channels, out_channels, 1),
                nn.BatchNorm2d(out_channels, momentum=bn_momentum),
            )
            init_weights(self.gcn_residual[0], bs=1)
            init_weights(self.gcn_residual[1], bs=1)
        else:
            self.gcn_residual = lambda x: x

        self.bn = nn.BatchNorm2d(out_channels, momentum=bn_momentum)
        init_weights(self.bn, bs=1e-6)
        self.relu = nn.ReLU()

    def forward(self, x):
        N, C, T, V = x.size()
        A = self.A * self.graph_attn
        hidden_ = None
        for i in range(self.num_subset):
            x_a = x.view(N, C * T, V)
            z = self.g_conv[i](torch.matmul(x_a, A[i]).view(N, C, T, V))
            hidden_ = z + hidden_ if hidden_ is not None else z
        hidden_ = self.bn(hidden_)
        hidden_ += self.gcn_residual(x)
        return self.relu(hidden_)


def CoGraphConvolution(in_channels, out_channels, A, bn_momentum=0.1):
    return co.forward_stepping(
        GraphConvolution(in_channels, out_channels, A, bn_momentum)
    )


class TemporalConvolution(nn.Module):
    def __init__(
        self,
        in_channels,
        out_channels,
        kernel_size=9,
        stride=1,
        padding=4,
    ):
        super(TemporalConvolution, self).__init__()

        self.padding = padding
        self.t_conv = nn.Conv2d(
            in_channels,
            out_channels,
            kernel_size=(kernel_size, 1),
            padding=(self.padding, 0),
            stride=(stride, 1),
        )
        self.bn = nn.BatchNorm2d(out_channels)
        init_weights(self.t_conv, bs=1)
        init_weights(self.bn, bs=1)

    def forward(self, x):
        x = self.bn(self.t_conv(x))
        return x


def CoTemporalConvolution(
    in_channels,
    out_channels,
    kernel_size=9,
    padding=0,
    stride=1,
) -> co.Sequential:

    if padding == "equal":
        padding = int((kernel_size - 1) / 2)

    t_conv = co.Conv2d(
        in_channels,
        out_channels,
        kernel_size=(kernel_size, 1),
        padding=(padding, 0),
        stride=(stride, 1),
    )

    bn = nn.BatchNorm2d(out_channels)

    init_weights(t_conv, bs=1)
    init_weights(bn, bs=1)

    seq = []
    seq.append(("t_conv", t_conv))
    seq.append(("bn", bn))
    return co.Sequential(OrderedDict(seq))


class SpatioTemporalBlock(nn.Module):
    def __init__(
        self,
        in_channels,
        out_channels,
        A,
        stride=1,
        residual=True,
        temporal_kernel_size=9,
        temporal_padding=-1,
        GraphConv=GraphConvolution,
        TempConv=TemporalConvolution,
    ):
        super(SpatioTemporalBlock, self).__init__()
        equal_padding = int((temporal_kernel_size - 1) / 2)
        if temporal_padding < 0:
            temporal_padding = equal_padding
            self.residual_shrink = None
        else:
            assert temporal_padding <= equal_padding
            self.residual_shrink = equal_padding - temporal_padding
        self.gcn = GraphConv(in_channels, out_channels, A)
        self.tcn = TempConv(
            out_channels,
            out_channels,
            stride=stride,
            kernel_size=temporal_kernel_size,
            padding=temporal_padding,
        )
        self.relu = nn.ReLU()
        if not residual:
            self.residual = zero
        elif (in_channels == out_channels) and (stride == 1):
            self.residual = unity
        else:
            self.residual = TempConv(
                in_channels, out_channels, kernel_size=1, stride=stride, padding=0
            )

    def forward(self, x):
        z = self.tcn(self.gcn(x))

        if self.residual_shrink:
            # Centered residuals:
            # If temporal zero-padding is removed, the feature-map shrinks at every temporal conv
            # The residual should shrink correspondingly, i.e. (kernel_size - 1) / 2) on each side
            r = self.residual(x[:, :, self.residual_shrink : -self.residual_shrink])
        else:
            r = self.residual(x)

        return self.relu(z + r)


def CoSpatioTemporalBlock(
    in_channels,
    out_channels,
    A,
    stride=1,
    residual=True,
    window_size=1,
    padding=0,
    CoGraphConv=CoGraphConvolution,
    CoTempConv=CoTemporalConvolution,
):
    window_size = int(window_size)

    gcn = CoGraphConv(in_channels, out_channels, A, bn_momentum=0.1)
    tcn = CoTempConv(
        out_channels,
        out_channels,
        stride=stride,
        padding=padding,
    )
    relu = torch.nn.ReLU()

    if not residual:
        return co.Sequential(OrderedDict([("gcn", gcn), ("tcn", tcn), ("relu", relu)]))

    if (in_channels == out_channels) and (stride == 1):
        return co.Sequential(
            co.Residual(
                co.Sequential(OrderedDict([("gcn", gcn), ("tcn", tcn)])),
                phantom_padding=True,
            ),
            relu,
        )

    residual = CoTempConv(
        in_channels,
        out_channels,
        kernel_size=1,
        stride=stride,
    )

    phantom_padding = tcn.receptive_field - 2 * tcn.padding != 1

    delay = tcn.delay
    if phantom_padding:
        delay = delay // 2

    return co.Sequential(
        co.BroadcastReduce(
            co.Sequential(
                OrderedDict(
                    [
                        ("residual", residual),
                        ("align", co.Delay(delay, auto_shrink=phantom_padding)),
                    ]
                )
            ),
            co.Sequential(OrderedDict([("gcn", gcn), ("tcn", tcn)])),
            auto_delay=False,
        ),
        relu,
    )