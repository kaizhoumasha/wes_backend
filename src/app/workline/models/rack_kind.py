"""WES 工作位允许的货架形态。"""

from enum import Enum


class RackKind(str, Enum):
    SINGLE_LAYER = "SINGLE_LAYER"
    FIVE_LAYER = "FIVE_LAYER"
    RETURN = "RETURN"
    TRANSFER = "TRANSFER"
    PRODUCTION = "PRODUCTION"
