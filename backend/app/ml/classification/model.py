"""The classifier network: MobileNetV3-Small with a replaced head.

Why MobileNetV3-Small rather than a ResNet or a ViT: inference has to run on the
same modest CPU box that serves the API, because a waste-sorting system that
needs a GPU to look at a photo will not be deployed by a municipality. It
classifies a 224x224 image in tens of milliseconds on CPU, and its ImageNet
features transfer well to object photographs, which is exactly what waste images
are.

Why transfer learning rather than training from scratch: public waste datasets
run to a few thousand images. That is nowhere near enough to learn edges,
textures and material appearance from nothing, but it is ample to re-aim a
backbone that already knows them.
"""

from __future__ import annotations

import torch
from torch import nn
from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

from app.ml.classification.labels import NUM_CLASSES


def build_model(*, pretrained: bool = True, dropout: float = 0.3) -> nn.Module:
    """MobileNetV3-Small with its 1000-way ImageNet head swapped for ours."""
    weights = MobileNet_V3_Small_Weights.IMAGENET1K_V1 if pretrained else None
    model = mobilenet_v3_small(weights=weights)

    in_features = model.classifier[-1].in_features
    model.classifier[-1] = nn.Linear(in_features, NUM_CLASSES)

    # Extra dropout before the new head: the head is the part most able to
    # memorise a few thousand images, so it is where regularisation is needed.
    model.classifier.insert(len(model.classifier) - 1, nn.Dropout(p=dropout))
    return model


def parameter_groups(
    model: nn.Module, *, head_lr: float, backbone_lr: float
) -> list[dict]:
    """Discriminative learning rates: fast head, slow backbone.

    The head starts from random weights and must move a long way. The backbone
    already holds useful features, and training it at the head's rate would
    destroy them before the head has learned anything worth backpropagating —
    the classic transfer-learning failure.
    """
    head_parameters = list(model.classifier.parameters())
    head_ids = {id(p) for p in head_parameters}
    backbone_parameters = [p for p in model.parameters() if id(p) not in head_ids]

    return [
        {"params": backbone_parameters, "lr": backbone_lr},
        {"params": head_parameters, "lr": head_lr},
    ]


def freeze_backbone(model: nn.Module, *, frozen: bool) -> None:
    """Toggle gradient flow through the feature extractor."""
    for parameter in model.features.parameters():
        parameter.requires_grad = not frozen


def select_device(prefer_gpu: bool = True) -> torch.device:
    if prefer_gpu and torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")
