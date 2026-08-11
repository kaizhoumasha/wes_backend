"""Low-level material-flow business contracts for runtime capabilities.

This package may contain pure value objects, constants, parsers, catalogs and
side-effect-free helpers only. Service, repository and database imports are
blocked by the business legacy absence guardrail.
"""

from src.app.runtime.capabilities.material_flow.contracts.material_identity import (
    MaterialIdentity,
    MaterialIdentityInput,
    MaterialIdentityResolutionStatus,
    MaterialIdentityResolver,
    hash_material_evidence,
    material_identity_input_to_hash,
)
from src.app.runtime.capabilities.material_flow.contracts.ng_reason import (
    BUILTIN_NG_REASONS,
    NgReasonCatalog,
    NgReasonDefinition,
    NgReasonSource,
    build_ng_reason_catalog,
)
from src.app.runtime.capabilities.material_flow.contracts.six_in_one import SixInOne

__all__ = [
    "BUILTIN_NG_REASONS",
    "MaterialIdentity",
    "MaterialIdentityInput",
    "MaterialIdentityResolutionStatus",
    "MaterialIdentityResolver",
    "NgReasonCatalog",
    "NgReasonDefinition",
    "NgReasonSource",
    "SixInOne",
    "build_ng_reason_catalog",
    "hash_material_evidence",
    "material_identity_input_to_hash",
]
