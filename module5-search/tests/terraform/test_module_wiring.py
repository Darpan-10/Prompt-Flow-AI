"""
Module 5 – Terraform Tests: Cross-Module Wiring

Same approach as Module 4's tests/terraform/test_module_wiring.py: parse
the ACTUAL .tf files with python-hcl2 and verify every module call in
root main.tf supplies a value for every required (no-default) variable
declared in that module's variables.tf.

Module 5's root main.tf currently has only ONE active module call
(security_groups) -- modules/ecs and modules/iam are intentionally
commented out pending ECR/ALB/JWT-key inputs, same pattern as Module 4's
ecs module. ACTIVE_MODULE_CALLS below reflects that; when you uncomment
ecs/iam, add them here too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import hcl2
import pytest

TERRAFORM_ROOT = Path(__file__).parent.parent.parent / "terraform"


def load_tf_file(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        return hcl2.load(f)


def merge_tf_dir(directory: Path) -> Dict[str, List[Dict[str, Any]]]:
    merged: Dict[str, List[Dict[str, Any]]] = {}
    for tf_file in sorted(directory.glob("*.tf")):
        data = load_tf_file(tf_file)
        for key, blocks in data.items():
            merged.setdefault(key, []).extend(blocks)
    return merged


def get_required_variables(module_dir: Path) -> set[str]:
    data = merge_tf_dir(module_dir)
    required = set()
    for var_block in data.get("variable", []):
        for var_name, var_def in var_block.items():
            if "default" not in var_def:
                required.add(var_name)
    return required


def get_root_module_calls() -> Dict[str, Dict[str, Any]]:
    data = load_tf_file(TERRAFORM_ROOT / "main.tf")
    calls: Dict[str, Dict[str, Any]] = {}
    for module_block in data.get("module", []):
        for call_name, call_args in module_block.items():
            clean_args = {k: v for k, v in call_args.items() if not k.startswith("__")}
            calls[call_name] = clean_args
    return calls


# ── Module source -> directory mapping (mirrors root main.tf's ACTIVE calls) ──
# Only includes modules NOT commented out. modules/ecs and modules/iam
# are intentionally excluded -- see module docstring above.

ACTIVE_MODULE_CALLS = {
    "security_groups": "modules/security_groups",
}

# Modules that exist and are fully built, but deliberately not yet wired
# into root main.tf (commented out pending external inputs). Tracked
# here so a test below can confirm they're STILL internally
# self-consistent (every variable they declare is something the
# commented-out call in main.tf actually attempts to supply), even
# though they're not part of an active `terraform plan`.
PENDING_MODULE_CALLS = {
    "ecs": "modules/ecs",
    "iam": "modules/iam",
}


class TestCrossModuleWiring:
    """Verify every ACTIVE module call supplies all required variables."""

    @pytest.fixture(scope="class")
    @classmethod
    def module_calls(cls) -> Dict[str, Dict[str, Any]]:
        return get_root_module_calls()

    @pytest.mark.parametrize("call_name,module_path", ACTIVE_MODULE_CALLS.items())
    def test_all_required_variables_supplied(self, module_calls, call_name, module_path):
        assert call_name in module_calls, (
            f"Expected an active 'module \"{call_name}\"' block in root main.tf, "
            f"but none was found. Did it get commented out or renamed?"
        )

        required = get_required_variables(TERRAFORM_ROOT / module_path)
        supplied = set(module_calls[call_name].keys()) - {"source", "version"}

        missing = required - supplied
        assert not missing, (
            f"module \"{call_name}\" ({module_path}) is missing required "
            f"argument(s): {sorted(missing)}. Root main.tf must pass a "
            f"value for every variable in {module_path}/variables.tf that "
            f"has no default."
        )

    def test_no_unknown_active_modules(self, module_calls):
        """
        Catches the inverse mistake: an active module call exists in
        main.tf that isn't in ACTIVE_MODULE_CALLS above. If you uncomment
        modules/ecs or modules/iam, this test will fail until you add
        them to ACTIVE_MODULE_CALLS (forcing you to also verify their
        required-variable wiring via the parametrized test above).
        """
        unexpected = set(module_calls.keys()) - set(ACTIVE_MODULE_CALLS.keys())
        assert not unexpected, (
            f"Found module call(s) in root main.tf not covered by "
            f"ACTIVE_MODULE_CALLS: {sorted(unexpected)}. Add them to "
            f"ACTIVE_MODULE_CALLS in this test file (and verify their "
            f"required variables) before considering this build correct."
        )


class TestPendingModulesAreReadyToUncomment:
    """
    modules/ecs and modules/iam are fully built but commented out in root
    main.tf. These tests don't validate the (commented) call itself --
    HCL comments aren't parsed -- but they DO validate that each pending
    module's own .tf files are internally well-formed, so that whenever
    you DO uncomment the call, you're only debugging the NEW wiring (the
    arguments you pass in), not pre-existing problems inside the module.
    """

    @pytest.mark.parametrize("module_name,module_path", PENDING_MODULE_CALLS.items())
    def test_pending_module_parses_cleanly(self, module_name, module_path):
        # A successful merge_tf_dir() call (no exception) means every .tf
        # file in the module directory is syntactically valid HCL2.
        data = merge_tf_dir(TERRAFORM_ROOT / module_path)
        assert "variable" in data or "resource" in data, (
            f"modules/{module_name} parsed but declared no variables or "
            f"resources -- that's suspicious for a non-trivial module."
        )

    def test_ecs_module_declares_locked_sizing_defaults(self):
        """
        Regression guard for the locked sizing decision (1 vCPU / 2GB
        RAM) -- if someone changes the defaults in modules/ecs/variables.tf
        without updating the rationale comments, at least this test makes
        the change visible and deliberate rather than silent.
        """
        data = merge_tf_dir(TERRAFORM_ROOT / "modules/ecs")
        var_defaults = {
            name: var_def.get("default", [None])[0]
            for var_block in data.get("variable", [])
            for name, var_def in var_block.items()
        }
        assert var_defaults.get("task_cpu") == 1024, (
            "Locked decision: search-api task should default to 1024 CPU "
            "units (1 vCPU). If this intentionally changed, update this "
            "test AND the rationale comment in modules/ecs/variables.tf."
        )
        assert var_defaults.get("task_memory") == 2048, (
            "Locked decision: search-api task should default to 2048 MB "
            "(2GB) RAM, sized for the embedding model + PyTorch runtime. "
            "If this intentionally changed, update this test AND the "
            "rationale comment in modules/ecs/variables.tf."
        )
