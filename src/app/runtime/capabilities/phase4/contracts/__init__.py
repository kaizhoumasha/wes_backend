"""Low-level Phase4 business contracts for runtime capabilities.

This package may contain pure value objects, constants, parsers, catalogs and
side-effect-free helpers only. Service, repository and database imports are
blocked by the Phase5 business cleanup guardrail.
"""

from src.app.runtime.capabilities.phase4.contracts.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    MaterialIdentityResolver,
    hash_material_evidence,
    material_identity_input_to_hash,
)
from src.app.runtime.capabilities.phase4.contracts.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonCatalog,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
)
from src.app.runtime.capabilities.phase4.contracts.rough_sorter_context import RoughSorterContext
from src.app.runtime.capabilities.phase4.contracts.six_in_one import SixInOne
from src.app.runtime.capabilities.phase4.contracts.sorting_inbound_context import (
    SortingInboundContext,
    SortingInboundContextError,
)

__all__ = [
    "BUILTIN_NG_REASONS",
    "MaterialIdentity",
    "MaterialIdentityInput",
    "MaterialIdentityResolutionStatus",
    "MaterialIdentityResolver",
    "NgReasonCatalog",
    "NgReasonDefinition",
    "NgReasonSource",
    "RoughSorterContext",
    "SixInOne",
    "SortingInboundContext",
    "SortingInboundContextError",
    "build_ng_reason_catalog",
    "hash_material_evidence",
    "material_identity_input_to_hash",
]
